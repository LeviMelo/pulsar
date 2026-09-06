"""Records into the graph: works with their people and venues, the career, the
lineage, and the board company that no paper records."""
from __future__ import annotations

from pathlib import Path

import pytest

from pulsar_research import graph as g
from pulsar_research.db import Database
from pulsar_research.graph.model import Kind, Relation, eid, person_id
from pulsar_research.records import build as build_records
from pulsar_research.semantics.corpus import load_corpus
from tests.test_records import ROWS


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "g.duckdb")
    database.initialize()
    with database.connect() as con:
        con.execute("INSERT INTO professors (siape, canonical_name, center, department) VALUES "
                    "('1', 'ana souza', 'FAMED', 'Pediatria'), ('2', 'eva melo', 'ICBS', 'Biologia')")
        con.execute("CREATE TABLE sigaa_public_lattes_flat (siape VARCHAR, lattes_id VARCHAR, path VARCHAR, "
                    "value_type VARCHAR, value_text VARCHAR)")
        con.executemany("INSERT INTO sigaa_public_lattes_flat VALUES (?, '999', ?, 'string', ?)", ROWS)
    build_records(database)
    return database


def _edges(p, relation):
    return [(e.source_id, e.target_id) for e in p.edges if e.relation is relation]


def test_a_work_is_authored_by_everyone_named_and_published_somewhere(db):
    p = g.build(db, load_corpus(db))
    works = [e for e in p.entities.values() if e.kind is Kind.WORK]
    article = next(w for w in works if w.name == "Sepse em UTI neonatal")
    authored = _edges(p, Relation.AUTHORED)
    assert (person_id(siape="1"), article.entity_id) in authored
    # "Bruno Lima" is nobody indexed: a name node, authored, and a co-author tie
    bruno = person_id(name="Bruno Lima")
    assert (bruno, article.entity_id) in authored
    assert p.entities[bruno].payload["indexed"] is False
    assert (person_id(siape="1"), bruno) in {tuple(sorted(e)) for e in _edges(p, Relation.COLLABORATES_WITH)}
    assert (article.entity_id, eid(Kind.VENUE, "Jornal de Pediatria")) in _edges(p, Relation.PUBLISHED_IN)


def test_a_board_member_who_is_faculty_is_the_faculty_node_and_the_pair_served_together(db):
    p = g.build(db, load_corpus(db))
    served = {tuple(sorted(e)) for e in _edges(p, Relation.SERVED_WITH)}
    assert (person_id(siape="1"), person_id(siape="2")) in served
    assert person_id(name="Eva Melo") not in p.entities
    examined = _edges(p, Relation.EXAMINED)
    assert (person_id(siape="1"), person_id(name="Davi Rocha")) in examined


def test_the_career_and_the_lineage_become_edges_to_orgs_and_people(db):
    p = g.build(db, load_corpus(db))
    assert (person_id(siape="1"), eid(Kind.ORG, "UFAL")) in _edges(p, Relation.WORKED_AT)
    assert (person_id(siape="1"), eid(Kind.ORG, "UFPE")) in _edges(p, Relation.TRAINED_AT)
    assert (person_id(siape="1"), person_id(name="Carla Dias")) in _edges(p, Relation.ADVISED_BY)
    assert (person_id(siape="1"), person_id(name="Fábio Nunes")) in _edges(p, Relation.SUPERVISES)


def test_the_projection_is_valid_and_writable(db):
    p = g.build(db, load_corpus(db))
    stats = g.write_graph(db, p.entities.values(), p.edges)
    assert stats["by_relation"]["served_with"] == 1
    assert stats["by_kind"]["venue"] == 1


def test_without_records_the_atom_path_still_projects_works(db):
    db.execute("DELETE FROM records")
    p = g.build(db, load_corpus(db))
    assert any(e.kind is Kind.WORK for e in p.entities.values())
    assert not _edges(p, Relation.SERVED_WITH)
