"""The store, opened over HTTP: records, portfolios, entities and one search.

The property under test is not the SQL — `store` has its own tests — but the
serving contract: what the console can ask for, that every answer is strict
JSON, that a stranger on a record resolves the way the graph resolved them, and
that a faculty name in a co-author list opens as faculty.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pulsar_research import graph as g
from pulsar_research.config import AppConfig
from pulsar_research.db import Database
from pulsar_research.records import build as build_records
from pulsar_research.semantics.corpus import load_corpus
from pulsar_research.webapp.server import Console, NotFound
from tests.test_records import ROWS


@pytest.fixture
def console(tmp_path: Path) -> Console:
    db = Database(tmp_path / "c.duckdb")
    db.initialize()
    with db.connect() as con:
        con.execute("INSERT INTO professors (siape, canonical_name, center, department) VALUES "
                    "('1', 'ana souza', 'FAMED', 'Pediatria'), ('2', 'eva melo', 'ICBS', 'Biologia')")
        con.execute("CREATE TABLE sigaa_public_lattes_flat (siape VARCHAR, lattes_id VARCHAR, path VARCHAR, "
                    "value_type VARCHAR, value_text VARCHAR)")
        con.executemany("INSERT INTO sigaa_public_lattes_flat VALUES (?, '999', ?, 'string', ?)", ROWS)
    build_records(db)
    projection = g.build(db, load_corpus(db))
    g.write_graph(db, projection.entities.values(), projection.edges)
    (tmp_path / "config").mkdir()
    return Console(AppConfig(tmp_path, {"paths": {"database": "c.duckdb"}}), db)


def get(console: Console, path: str, **query: str):
    payload = console.get(path, {k: [v] for k, v in query.items()})
    json.dumps(payload, allow_nan=False)     # the server's strict JSON
    return payload


def test_records_are_searchable_with_the_filters_the_screen_exposes(console: Console):
    assert get(console, "/api/records", q="sepse")["total"] == 1
    assert get(console, "/api/records", family="committee")["total"] == 1
    assert get(console, "/api/records", person="eva melo")["total"] == 1
    assert get(console, "/api/records", siape="1", since="2019")["total"] >= 3
    row = get(console, "/api/records", q="sepse")["rows"][0]
    assert row["subject"] == "Ana Souza"
    assert row["venue"] == "Jornal de Pediatria"
    assert row["people"] == 2


def test_facets_carry_the_meaning_of_each_family(console: Console):
    facets = get(console, "/api/records/facets")
    assert facets["summary"]["built"]
    families = {f["family"]: f for f in facets["families"]}
    assert "Publications" in families["work"]["meaning"]
    assert facets["meanings"]["committee"]


def test_a_record_resolves_the_people_named_on_it(console: Console):
    rid = get(console, "/api/records", family="committee")["rows"][0]["record_id"]
    record = get(console, f"/api/record/{rid}")
    by_name = {p["name"]: p for p in record["people"]}
    assert by_name["Eva Melo"]["siape"] == "2"                 # faculty opens as faculty
    assert by_name["Ana Souza"]["siape"] == "1"
    article = get(console, "/api/records", q="sepse")["rows"][0]["record_id"]
    record = get(console, f"/api/record/{article}")
    bruno = next(p for p in record["people"] if p["name"] == "Bruno Lima")
    assert bruno["siape"] is None
    assert bruno["entity_id"] == "person:name-bruno-lima"       # a stranger opens as a node
    assert record["entity_id"] == f"work:{article}"


def test_a_portfolio_holds_everything_and_the_company_without_the_subject(console: Console):
    portfolio = get(console, "/api/professor/1/portfolio")
    assert portfolio["name"] == "Ana Souza"
    assert {r["family"] for r in portfolio["records"]} >= {"work", "degree", "committee", "career"}
    names = [p["name"] for p in portfolio["people"]]
    assert "Ana Souza" not in names
    eva = next(p for p in portfolio["people"] if p["name"] == "Eva Melo")
    assert eva["siape"] == "2"
    assert [r["family"] for r in portfolio["career"]] and all(
        r["family"] in ("career", "degree", "training", "activity") for r in portfolio["career"])


def test_an_entity_page_groups_its_edges_by_what_they_mean(console: Console):
    page = get(console, "/api/entity/org:ufal")
    labels = {group["label"]: group for group in page["groups"]}
    assert "People who worked here" in labels
    assert labels["People who worked here"]["rows"][0]["name"] == "Ana Souza"
    assert any(r["org"] == "UFAL" for r in page["records"])
    venue = get(console, "/api/entity/venue:jornal-de-pediatria")
    assert "Works published here" in {group["label"] for group in venue["groups"]}


def test_one_search_finds_people_records_and_entities(console: Console):
    found = get(console, "/api/search", q="souza")
    assert [p["siape"] for p in found["professors"]] == ["1"]
    found = get(console, "/api/search", q="pediatria")
    assert any(e["kind"] == "venue" for e in found["entities"])
    assert found["records_total"] >= 1
    assert get(console, "/api/search", q="s") == {"professors": [], "records": [], "entities": []}


def test_sources_are_served_for_the_freshness_screen(console: Console):
    payload = get(console, "/api/sources")
    assert {s["id"] for s in payload["sources"]} >= {"sigaa.professors", "lattes.embedded"}


def test_unknown_records_and_entities_are_not_found(console: Console):
    with pytest.raises(NotFound):
        get(console, "/api/record/nope")
    with pytest.raises(NotFound):
        get(console, "/api/entity/org:nowhere")
