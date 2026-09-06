"""The source registry: declarations the pipeline and CLI are derived from.

What is worth pinning is the contract, not any one scraper: that every source
says how it is reached and what it owns, that ownership is exclusive, that the
network ones are the ones marked as reaching it, that a run is journalled
whether it succeeds or dies, and that a source which cannot run here says so
before it tries.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pulsar_research.config import AppConfig
from pulsar_research.db import Database
from pulsar_research.sources import (
    Access, Capture, Method, NETWORK_METHODS, RunOptions, SOURCES, Source, SourceUnavailable,
    get, history, journalled_run, last_run, readiness,
)
from pulsar_research.sources.registry import BY_STAGE


@pytest.fixture
def store(tmp_path: Path) -> tuple[AppConfig, Database]:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "default.toml").write_text("[paths]\n", encoding="utf-8")
    config = AppConfig(tmp_path, {"paths": {"database": "pulsar.duckdb"}})
    db = Database(config.paths.database)
    db.initialize()
    return config, db


# ---------------------------------------------------------------------------
# The declarations
# ---------------------------------------------------------------------------


def test_every_source_declares_how_it_is_reached_and_what_it_owns():
    for source in SOURCES:
        assert isinstance(source.method, Method)
        assert isinstance(source.access, Access)
        assert source.yields, f"{source.id} owns no table"
        assert source.why.strip()
        assert source.stage.startswith("acquire.")


def test_ids_and_stages_are_unique():
    assert len({s.id for s in SOURCES}) == len(SOURCES)
    assert len({s.stage for s in SOURCES}) == len(SOURCES)
    assert set(BY_STAGE) == {s.stage for s in SOURCES}


def test_dependencies_name_declared_sources():
    ids = {s.id for s in SOURCES}
    for source in SOURCES:
        assert set(source.depends_on) <= ids, source.id


def test_only_network_methods_reach_the_network():
    for source in SOURCES:
        assert source.reaches_network == (source.method in NETWORK_METHODS)
    # And the ones that need a password are exactly the authenticated ones.
    for source in SOURCES:
        assert bool(source.secrets) == (source.access is Access.AUTHENTICATED), source.id


def test_the_embedded_lattes_source_is_local_and_depends_on_the_crawl_that_embeds_it():
    lattes = get("lattes.embedded")
    assert lattes.method is Method.EMBEDDED
    assert lattes.access is Access.LOCAL
    assert not lattes.reaches_network
    assert "sigaa.professors" in lattes.depends_on


def test_unknown_source_names_the_known_ones():
    with pytest.raises(KeyError, match="sigaa.professors"):
        get("nope")


# ---------------------------------------------------------------------------
# Running and journalling
# ---------------------------------------------------------------------------


def _fake(source_id: str, body, **kwargs) -> Source:
    return Source(
        id=source_id, title="t", provider="p", method=Method.FILE_IMPORT, access=Access.LOCAL,
        why="w", yields=("x",), stage=f"acquire.{source_id}", run=body, **kwargs)


def test_a_run_is_journalled_with_what_it_captured(store):
    config, db = store

    def body(config, db, options):
        return Capture("fake", rows={"x": 3}, notes={"seen": options.refresh})

    capture = journalled_run(config, db, _fake("fake", body), RunOptions(refresh=True))
    assert capture.rows == {"x": 3}
    run = last_run(db, "fake")
    assert run["status"] == "ok"
    assert run["details"]["rows"] == {"x": 3}
    assert run["details"]["options"]["refresh"] is True


def test_a_run_that_dies_is_journalled_as_failed_before_the_error_propagates(store):
    config, db = store

    def body(config, db, options):
        raise RuntimeError("the portal changed")

    with pytest.raises(RuntimeError, match="portal changed"):
        journalled_run(config, db, _fake("dying", body))
    run = last_run(db, "dying")
    assert run["status"] == "failed"
    assert "portal changed" in run["details"]["error"]


def test_a_dry_run_is_not_journalled(store):
    config, db = store
    journalled_run(config, db, _fake("dry", lambda c, d, o: Capture("dry")), RunOptions(dry_run=True))
    assert last_run(db, "dry") is None


def test_a_source_missing_its_secret_refuses_before_running(store, monkeypatch):
    config, db = store
    monkeypatch.delenv("PULSAR_TEST_SECRET", raising=False)
    calls = []
    source = _fake("locked", lambda c, d, o: calls.append(1) or Capture("locked"),
                   secrets=("PULSAR_TEST_SECRET",))
    assert readiness(config, source) == ["environment variable PULSAR_TEST_SECRET is not set"]
    with pytest.raises(SourceUnavailable, match="PULSAR_TEST_SECRET"):
        journalled_run(config, db, source)
    assert not calls
    assert last_run(db, "locked") is None


def test_a_source_can_be_disabled_in_config(store):
    config, db = store
    config.raw["sources"] = {"off": {"enabled": False}}
    assert readiness(config, _fake("off", lambda c, d, o: Capture("off"))) == [
        "disabled in config [sources]"]


def test_history_is_newest_first_and_filterable(store):
    config, db = store
    for name in ("a", "b", "a"):
        journalled_run(config, db, _fake(name, lambda c, d, o, _n=name: Capture(_n, rows={"x": 1})))
    assert [r["source"] for r in history(db)][:3] == ["a", "b", "a"]
    assert {r["source"] for r in history(db, "a")} == {"a"}


# ---------------------------------------------------------------------------
# The pipeline is derived from the registry
# ---------------------------------------------------------------------------


def test_every_source_is_a_pipeline_stage_with_the_same_dependencies():
    from pulsar_research.pipeline import registry as reg
    for source in SOURCES:
        stage = reg.BY_NAME[source.stage]
        assert stage.acquires == source.reaches_network
        assert set(stage.depends_on) == {get(d).stage for d in source.depends_on}
        assert stage.produces == source.yields


def test_a_changed_local_input_makes_its_stage_stale(store, monkeypatch):
    from pulsar_research.pipeline import registry as reg
    config, db = store
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, root=None: config))
    # A ledger that was imported, then edited: the file is newer than the run.
    ledger = config.paths.opportunity_ledger_json
    ledger.parent.mkdir(parents=True, exist_ok=True)
    journalled_run(config, db, _fake("ledger.file", lambda c, d, o: Capture("ledger.file", rows={"opportunities": 1})))
    import time
    time.sleep(0.01)
    ledger.write_text("{}", encoding="utf-8")
    fresh = reg.BY_NAME["acquire.ledger"].status(db)
    assert fresh.state == "stale"
    assert "changed after" in fresh.detail
