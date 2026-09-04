"""A v2 store must upgrade in place: derived tables reshape, facts survive."""
from __future__ import annotations

import duckdb
import pytest

from pulsar_research.db import DERIVED_TABLES, Database


@pytest.fixture()
def legacy_store(tmp_path):
    """A store shaped like the superseded v2 engine."""
    path = tmp_path / "legacy.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR, updated_at VARCHAR)")
    con.execute("INSERT INTO meta VALUES ('schema_version','2','2026-01-01T00:00:00+00:00')")
    con.execute("INSERT INTO meta VALUES ('current_semantic_model_id','abc','2026-01-01T00:00:00+00:00')")
    # Facts that must survive untouched.
    con.execute("CREATE TABLE opportunities (id_oportunidade VARCHAR, titulo VARCHAR)")
    con.execute("INSERT INTO opportunities VALUES ('1','Epidemiologia espacial')")
    # A derived table whose shape changed between engines.
    con.execute("CREATE TABLE semantic_topics (model_id VARCHAR, space VARCHAR, topic_id BIGINT, "
                "label VARCHAR, top_terms_json VARCHAR, diagnostics_json VARCHAR, analyzed_at VARCHAR)")
    con.execute("INSERT INTO semantic_topics VALUES ('abc','domain',0,'x','[]','{}','2026-01-01')")
    # A table the current engine dropped entirely.
    con.execute("CREATE TABLE semantic_runs (run_id VARCHAR)")
    con.close()
    return path


def test_migration_reshapes_derived_tables_and_keeps_facts(legacy_store):
    report = Database(legacy_store).initialize()

    assert "semantic_runs" in report["dropped"]
    assert "semantic_topics" in report["rebuilt"], "a reshaped derived table must be rebuilt"

    con = duckdb.connect(str(legacy_store), read_only=True)
    try:
        for table, expected in DERIVED_TABLES.items():
            live = {r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name=?",
                [table]).fetchall()}
            assert live == set(expected), f"{table} did not converge on the declared schema"
        assert con.execute("SELECT titulo FROM opportunities").fetchone()[0] == "Epidemiologia espacial"
        assert con.execute("SELECT COUNT(*) FROM meta WHERE key='current_semantic_model_id'").fetchone()[0] == 0
        assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "3"
    finally:
        con.close()


def test_initialize_is_idempotent(legacy_store):
    Database(legacy_store).initialize()
    second = Database(legacy_store).initialize()
    assert second["dropped"] == [] and second["rebuilt"] == []
