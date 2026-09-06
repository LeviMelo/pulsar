"""The entity graph: its id space, its vocabulary, and what it refuses.

The graph is the layer everything above it now reasons over, which makes its
failure mode quiet. A wrong score shows up as a strange ranking; a wrong edge
shows up as nothing at all, because a graph always looks like a graph. So the
things checked here are the ones no screen would reveal:

* one person is one node, however their name is spelled
* a symmetric tie is stored once, not once per direction
* the relation vocabulary is enforced rather than documented
* rebuilding replaces, so a closed opportunity actually disappears
"""
from __future__ import annotations

import pytest

from pulsar_research import graph as g
from pulsar_research.graph.model import Edge, Entity, Kind, Relation, eid, person_id, slug
from pulsar_research.graph.store import edge_id


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_accents_do_not_split_a_person_in_two():
    """`Nóbrega` and `Nobrega` are one researcher written twice."""
    assert slug("Diego Figueiredo Nóbrega") == slug("diego figueiredo nobrega")
    assert person_id(name="Nóbrega") == person_id(name="Nobrega")


def test_a_siape_wins_over_a_name():
    """Faculty are identified by registry number; only strangers are named."""
    assert person_id(siape="1157495", name="Diego") == "person:1157495"
    assert g.is_indexed_person("person:1157495")
    assert not g.is_indexed_person(person_id(name="Diego"))


def test_a_person_with_neither_a_number_nor_a_name_is_not_a_person():
    with pytest.raises(ValueError):
        person_id()


def test_an_id_carries_its_kind():
    assert eid(Kind.ORG, "ICBS") == "org:icbs"
    assert eid(Kind.POSITION, 12345) == "position:12345"


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def test_a_symmetric_tie_is_stored_once():
    """Co-authorship seen from both ends must not become two edges."""
    forward = Edge("person:2", "person:1", Relation.COLLABORATES_WITH)
    backward = Edge("person:1", "person:2", Relation.COLLABORATES_WITH)
    assert (forward.source_id, forward.target_id) == (backward.source_id, backward.target_id)
    assert edge_id(*[forward.source_id, forward.target_id], Relation.COLLABORATES_WITH) == \
        edge_id(*[backward.source_id, backward.target_id], Relation.COLLABORATES_WITH)


def test_a_directed_relation_keeps_its_direction():
    edge = Edge("person:1", "work:x", Relation.AUTHORED)
    assert (edge.source_id, edge.target_id) == ("person:1", "work:x")
    assert edge.directed


def test_the_vocabulary_is_enforced_not_merely_documented():
    """A work cannot author a person, however the caller phrases it."""
    kinds = {"person:1": Kind.PERSON, "work:x": Kind.WORK}
    Edge("person:1", "work:x", Relation.AUTHORED).validate(kinds)
    with pytest.raises(ValueError):
        Edge("work:x", "person:1", Relation.AUTHORED).validate(kinds)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


@pytest.fixture()
def tiny():
    people = [Entity(person_id(siape=str(i)), Kind.PERSON, f"P{i}") for i in (1, 2, 3)]
    work = Entity("work:paper", Kind.WORK, "A paper")
    edges = [
        Edge("person:1", "work:paper", Relation.AUTHORED, source="lattes"),
        Edge("person:2", "work:paper", Relation.AUTHORED, source="lattes"),
        Edge("person:1", "person:2", Relation.COLLABORATES_WITH, weight=2.0, source="lattes"),
    ]
    return [*people, work], edges


def test_an_edge_to_an_entity_that_does_not_exist_is_refused(db, tiny):
    """A dangling edge is how a graph starts lying about who is connected."""
    entities, edges = tiny
    edges.append(Edge("person:1", "person:99", Relation.COLLABORATES_WITH))
    with pytest.raises(ValueError):
        g.write_graph(db, entities, edges)


def test_the_same_relation_seen_twice_accumulates_weight(db, tiny):
    """Two papers with the same person is a stronger tie, not a duplicate row."""
    entities, edges = tiny
    edges.append(Edge("person:2", "person:1", Relation.COLLABORATES_WITH, weight=3.0))
    stats = g.write_graph(db, entities, edges)
    assert stats["by_relation"]["collaborates_with"] == 1
    weight = db.scalar("SELECT weight FROM graph_edges WHERE relation='collaborates_with'")
    assert float(weight) == 5.0


def test_rebuilding_replaces_rather_than_accumulates(db, tiny):
    """An opportunity that closed has to be able to disappear."""
    entities, edges = tiny
    g.write_graph(db, entities, edges)
    stats = g.write_graph(db, entities[:1], [])
    assert stats == {"entities": 1, "edges": 0,
                     "by_kind": {"person": 1}, "by_relation": {}}
    assert g.summary(db)["entities"] == 1


def test_a_symmetric_neighbour_is_found_from_either_end(db, tiny):
    """Stored once, read from both sides — otherwise half the graph is invisible."""
    g.write_graph(db, *tiny)
    from_one = {n["entity_id"] for n in g.neighbours(db, "person:1",
                                                    relations=[Relation.COLLABORATES_WITH])}
    from_two = {n["entity_id"] for n in g.neighbours(db, "person:2",
                                                    relations=[Relation.COLLABORATES_WITH])}
    assert from_one == {"person:2"}
    assert from_two == {"person:1"}


def test_an_apostrophe_in_a_name_does_not_break_the_read(db):
    """Entity ids are built from scraped text; some of that text has quotes in it."""
    odd = person_id(name="O'Brien")
    entities = [Entity(odd, Kind.PERSON, "O'Brien"), Entity("person:1", Kind.PERSON, "P1")]
    g.write_graph(db, entities, [Edge(odd, "person:1", Relation.COLLABORATES_WITH)])
    assert [n["entity_id"] for n in g.neighbours(db, odd)] == ["person:1"]


def test_adjacency_is_symmetric_for_a_symmetric_relation(db, tiny):
    g.write_graph(db, *tiny)
    adjacency = g.adjacency(db, relations=[Relation.COLLABORATES_WITH])
    assert adjacency["person:1"]["person:2"] == adjacency["person:2"]["person:1"] == 2.0
    # A vertex with no tie of this relation is not in the graph of it.
    assert "person:3" not in adjacency


def test_a_ranking_reports_the_degree_it_was_computed_over(db, tiny):
    """A share of 1.0 on a vertex with one tie is arithmetic, not a finding."""
    g.write_graph(db, *tiny)
    g.write_metrics(db, [("person:1", "bridging", 1.0, {}), ("person:1", "degree", 1.0, {}),
                         ("person:2", "bridging", 0.5, {}), ("person:2", "degree", 9.0, {})])
    assert [r["entity_id"] for r in g.top_by_metric(db, "bridging", min_degree=0)] == \
        ["person:1", "person:2"]
    assert [r["entity_id"] for r in g.top_by_metric(db, "bridging", min_degree=3)] == ["person:2"]
    assert g.top_by_metric(db, "bridging", min_degree=3)[0]["degree"] == 9


# ---------------------------------------------------------------------------
# The projection
# ---------------------------------------------------------------------------


def test_the_projection_turns_the_synthetic_corpus_into_a_graph(db):
    """End to end on the fixture store: every declared fact reaches the graph."""
    from pulsar_research.semantics.corpus import load_corpus
    projection = g.build(db, load_corpus(db))
    ids = set(projection.entities)

    # Two professors, three work plans, two projects, two centres.
    assert {"person:10", "person:20"} <= ids
    assert sum(1 for e in projection.entities.values() if e.kind is Kind.POSITION) == 3
    assert sum(1 for e in projection.entities.values() if e.kind is Kind.PROJECT) == 2
    assert {"org:famed", "org:icbs"} <= ids

    relations = {e.relation for e in projection.edges}
    assert {Relation.OFFERS, Relation.WITHIN, Relation.AFFILIATED_WITH} <= relations

    # Ana offers both plans of her project, and each plan sits within it.
    offered = {e.target_id for e in projection.edges
               if e.source_id == "person:10" and e.relation is Relation.OFFERS}
    assert len(offered) == 2
    within = {e.target_id for e in projection.edges
              if e.source_id in offered and e.relation is Relation.WITHIN}
    assert len(within) == 1


def test_the_projection_writes_and_reads_back_unchanged(db):
    """Whatever `build` produced, `write_graph` must accept without validation error."""
    from pulsar_research.semantics.corpus import load_corpus
    projection = g.build(db, load_corpus(db))
    stats = g.write_graph(db, projection.entities.values(), projection.edges)
    assert stats["entities"] == len(projection.entities)
    assert g.summary(db)["entities"] == stats["entities"]


def test_a_second_projection_of_the_same_store_is_identical(db):
    """Nothing in the projection may depend on dict iteration luck or a clock."""
    from pulsar_research.semantics.corpus import load_corpus
    corpus = load_corpus(db)
    first, second = g.build(db, corpus), g.build(db, corpus)
    assert sorted(first.entities) == sorted(second.entities)
    assert sorted((e.source_id, e.target_id, e.relation.value, e.weight) for e in first.edges) == \
        sorted((e.source_id, e.target_id, e.relation.value, e.weight) for e in second.edges)
