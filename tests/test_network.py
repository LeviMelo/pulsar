"""The three faculty graphs.

The Network screen makes structural claims — this pair co-authors, these two
units barely touch, this uncontacted supervisor is one edge from a live thread
— and a graph is the one visualisation where a wrong edge is invisible: it just
looks like a slightly different picture. So the edges are checked here, not
eyeballed in the browser.
"""
from __future__ import annotations

import json

import pytest

from pulsar_research.dashboard.queries import Repository
from pulsar_research.webapp import network as net


SPACE = "S1"


@pytest.fixture()
def spaced(db):
    """The synthetic corpus plus a semantic space the derived tables hang off."""
    with db.connect() as con:
        con.execute("INSERT INTO meta VALUES ('current_semantic_space_id', ?, '')", [SPACE])
    return db


def topics(db, rows):
    with db.connect() as con:
        for entity_type, entity_id, topic_id, weight in rows:
            con.execute(
                "INSERT INTO entity_topics (space_id, entity_type, entity_id, facet, "
                "topic_id, weight, is_dominant) VALUES (?,?,?,'domain',?,?,TRUE)",
                [SPACE, entity_type, entity_id, topic_id, weight])


def skills(db, rows):
    with db.connect() as con:
        for entity_id, skill_id, label, generic, mentions in rows:
            con.execute(
                "INSERT INTO entity_skills (space_id, entity_type, entity_id, skill_id, "
                "label, category, generic, mentions) VALUES (?,'professor',?,?,?,'m',?,?)",
                [SPACE, entity_id, skill_id, label, generic, mentions])


def build(db, mode):
    return net.build(db, mode)


# --------------------------------------------------------------- integrity

@pytest.mark.parametrize("mode", net.MODES)
def test_every_mode_serialises_under_the_servers_strict_json(spaced, mode):
    """`allow_nan=False` is what the server calls; a stray NaN there is a 500
    the browser shows as a blank screen."""
    json.dumps(build(spaced, mode), allow_nan=False)


@pytest.mark.parametrize("mode", net.MODES)
def test_no_edge_points_at_a_node_that_is_not_drawn(spaced, mode):
    """A dangling endpoint does not raise; it silently draws a line to the
    origin, which reads as a real relationship."""
    graph = build(spaced, mode)
    ids = {node["id"] for node in graph["nodes"]}
    for edge in graph["edges"]:
        assert edge["source"] in ids and edge["target"] in ids


def test_an_unknown_mode_falls_back_instead_of_erroring(spaced):
    assert build(spaced, "'; DROP TABLE professors")["mode"] == "collaboration"


def test_isolated_counts_the_nodes_with_no_edge(spaced):
    graph = build(spaced, "collaboration")
    assert graph["isolated"] == len(graph["nodes"])      # no collaboration recorded yet


# ---------------------------------------------------------- collaboration

def test_collaboration_draws_only_edges_between_indexed_supervisors(spaced):
    """The store holds thousands of external co-authors. Drawing them would bury
    the faculty structure under degree-one leaves, so they are counted on the
    node instead."""
    with spaced.connect() as con:
        con.execute("INSERT INTO collaboration_edges VALUES ('10','20','Bruno Lima',3,'')")
        con.execute("INSERT INTO collaboration_edges VALUES ('10','','Alguém de Fora',5,'')")
        con.execute("INSERT INTO collaboration_edges VALUES ('10','','Outro Externo',2,'')")

    graph = build(spaced, "collaboration")
    assert len(graph["edges"]) == 1
    assert {graph["edges"][0]["source"], graph["edges"][0]["target"]} == {"10", "20"}
    ana = next(n for n in graph["nodes"] if n["id"] == "10")
    assert ana["external_collaborators"] == 2
    assert ana["degree"] == 1


def test_an_edge_is_undirected_and_counted_once(spaced):
    with spaced.connect() as con:
        con.execute("INSERT INTO collaboration_edges VALUES ('10','20','Bruno',2,'')")
        con.execute("INSERT INTO collaboration_edges VALUES ('20','10','Ana',2,'')")
    assert len(build(spaced, "collaboration")["edges"]) == 1


# ----------------------------------------------------------------- topics

def test_topics_are_derived_from_the_plans_each_supervisor_offers(spaced):
    """`entity_topics` holds no professor rows at all — the topic model is fitted
    over documents, not over people. Querying it for `entity_type='professor'`
    returns nothing and yields a graph of 65 isolated dots, which is what this
    guards against."""
    topics(spaced, [
        ("opportunity", "1", "T1", 0.9),
        ("opportunity", "3", "T1", 0.8),
        ("professor", "10", "T9", 1.0),     # ignored: people are not documents
    ])
    graph = build(spaced, "topics")
    assert len(graph["edges"]) == 1
    assert {graph["edges"][0]["source"], graph["edges"][0]["target"]} == {"10", "20"}


def test_supervisors_studying_different_things_are_not_joined(spaced):
    topics(spaced, [("opportunity", "1", "T1", 1.0), ("opportunity", "3", "T2", 1.0)])
    assert build(spaced, "topics")["edges"] == []


# ------------------------------------------------------------------ skills

def test_generic_competencies_do_not_create_neighbours(spaced):
    """"Data analysis" appears in nearly every portfolio. Counting it would make
    the whole faculty adjacent to the whole faculty."""
    skills(spaced, [
        ("10", "s-generic", "Análise de dados", True, 9),
        ("20", "s-generic", "Análise de dados", True, 9),
    ])
    assert build(spaced, "skills")["edges"] == []


def test_a_shared_distinctive_technique_makes_an_edge(spaced):
    skills(spaced, [
        ("10", "s-r", "R", False, 4),
        ("20", "s-r", "R", False, 3),
    ])
    graph = build(spaced, "skills")
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["weight"] == pytest.approx(1.0)


def test_each_node_carries_a_few_subject_keywords(spaced):
    """The drawing can encode position, size and colour but never what someone
    works on, so the node label needs the words themselves."""
    skills(spaced, [
        ("10", "s1", "Geoprocessamento", False, 9),
        ("10", "s2", "Regressão de Poisson", False, 6),
        ("10", "s3", "Curadoria de bases", False, 4),
        ("10", "s4", "Mapas temáticos", False, 2),
        ("10", "s5", "Análise de dados", True, 20),
    ])
    ana = next(n for n in build(spaced, "skills")["nodes"] if n["id"] == "10")
    assert ana["keywords"] == ["Geoprocessamento", "Regressão de Poisson", "Curadoria de bases"]


# ----------------------------------------------------------------- pruning

def test_similarity_graphs_keep_each_nodes_strongest_edges_only():
    """Every pair above the threshold would be a hairball on a dense facet. The
    cap is per node, and the union is kept, so nobody is stranded by it."""
    vectors = {str(i): {"a": 1.0, "b": 1.0} for i in range(12)}
    edges = net._pairs(vectors, net._jaccard)

    degree = {}
    for edge in edges:
        degree[edge["source"]] = degree.get(edge["source"], 0) + 1
        degree[edge["target"]] = degree.get(edge["target"], 0) + 1
    assert edges                                    # identical vectors do connect
    assert len(edges) < (12 * 11) // 2              # but not to everyone
    for node in vectors:
        assert degree.get(node, 0) >= 1             # and nobody is left isolated


def test_weak_similarity_is_dropped_as_noise():
    """Two portfolios sharing one common technique are not neighbours."""
    vectors = {"a": {f"s{i}": 1.0 for i in range(10)},
               "b": {"s0": 1.0, **{f"t{i}": 1.0 for i in range(9)}}}
    assert net._pairs(vectors, net._jaccard) == []


# ------------------------------------------------------------ neighbourhood

def test_neighbourhood_walks_the_requested_number_of_hops():
    edges = [{"source": "a", "target": "b"}, {"source": "b", "target": "c"},
             {"source": "c", "target": "d"}]
    assert net.neighbourhood(edges, "a", 1) == {"a", "b"}
    assert net.neighbourhood(edges, "a", 2) == {"a", "b", "c"}
    assert net.neighbourhood(edges, "a", 0) == {"a"}


# -------------------------------------------------------------- provenance

def test_the_graph_explains_its_own_edge_semantics(spaced):
    """Three modes that disagree are only useful if the screen says which one is
    being looked at and what an edge in it means."""
    for mode in net.MODES:
        graph = build(spaced, mode)
        assert graph["note"] and len(graph["note"]) > 40
        assert [m["key"] for m in graph["modes"]] == list(net.MODES)


def test_a_corpus_with_no_professors_is_not_an_error(db):
    with db.connect() as con:
        con.execute("DELETE FROM professors")
    graph = net.build(db, "skills")
    assert graph["nodes"] == [] and graph["edges"] == []


def test_the_repository_is_reused_rather_than_reimplemented(spaced):
    """The node payload has to agree with the Professors screen; if it drifted,
    the same supervisor would carry two different slot counts."""
    frame = Repository(spaced).professors()
    graph = build(spaced, "collaboration")
    assert len(graph["nodes"]) == len(frame)


# ------------------------------------------------- reading from the platform


def _project(db):
    """Build and measure the entity graph, as `pulsar pipeline run` would."""
    from pulsar_research import graph as g
    from pulsar_research.graph.model import Relation, is_indexed_person
    from pulsar_research.intelligence.network import structural_metrics
    from pulsar_research.semantics.corpus import load_corpus
    corpus = load_corpus(db)
    projection = g.build(db, corpus)
    g.record_build(db, corpus.fingerprint(),
                   g.write_graph(db, projection.entities.values(), projection.edges))
    adjacency = g.adjacency(db, relations=[Relation.COLLABORATES_WITH])
    inside = [n for n in adjacency if is_indexed_person(n)]
    rows, _ = structural_metrics(adjacency, inside=inside)
    induced = {n: {o: w for o, w in adjacency[n].items() if is_indexed_person(o)} for n in inside}
    faculty, _ = structural_metrics(induced)
    rows.extend((e, f"faculty_{m}", v, x) for e, m, v, x in faculty)
    g.write_metrics(db, rows)


def test_the_drawn_graph_inherits_the_platforms_identity_resolution(spaced):
    """A co-author recorded under a shortened name is still a colleague.

    Lattes records "Bruno Lima" on one team and "Bruno" on another. The raw
    table keeps the second as a stranger — no `target_siape` — so a console that
    derives its own graph from that table under-reports the faculty's internal
    density. Reading the entity graph instead inherits the resolution rather
    than re-deriving a weaker version of it.
    """
    with spaced.connect() as con:
        con.execute("UPDATE professors SET canonical_name='Bruno Cesar Lima' WHERE siape='20'")
        con.execute("INSERT INTO collaboration_edges VALUES ('10','','Bruno Lima',4,'')")
    assert build(spaced, "collaboration")["edges"] == []        # no graph built yet

    _project(spaced)
    graph = build(spaced, "collaboration")
    assert len(graph["edges"]) == 1
    assert {graph["edges"][0]["source"], graph["edges"][0]["target"]} == {"10", "20"}


def test_the_raw_table_is_still_drawn_when_no_graph_has_been_built(spaced):
    """A store that has never run `graph build` must not show an empty canvas.

    An empty drawing does not read as "not computed"; it reads as a faculty
    where nobody collaborates.
    """
    with spaced.connect() as con:
        con.execute("INSERT INTO collaboration_edges VALUES ('10','20','Bruno Lima',3,'')")
    assert len(build(spaced, "collaboration")["edges"]) == 1


def test_structural_measures_reach_the_node_once_they_exist(spaced):
    with spaced.connect() as con:
        con.execute("INSERT INTO collaboration_edges VALUES ('10','20','Bruno Lima',3,'')")
        con.execute("INSERT INTO collaboration_edges VALUES ('10','','Alguém de Fora',5,'')")
    assert build(spaced, "collaboration")["nodes"][0]["community"] is None

    _project(spaced)
    ana = next(n for n in build(spaced, "collaboration")["nodes"] if n["id"] == "10")
    assert ana["community"] == 0
    assert ana["betweenness"] == 0.0            # two people cannot have a middle
    # Five of her eight units of tie strength go to someone outside the faculty.
    assert ana["external_reach"] == pytest.approx(5 / 8)


def test_the_measures_survive_the_servers_strict_json(spaced):
    """`allow_nan=False`: a NaN in a structural measure is a blank screen."""
    with spaced.connect() as con:
        con.execute("INSERT INTO collaboration_edges VALUES ('10','20','Bruno Lima',3,'')")
    _project(spaced)
    json.dumps(build(spaced, "collaboration"), allow_nan=False)
