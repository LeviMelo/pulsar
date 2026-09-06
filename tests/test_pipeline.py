"""The build graph: ordering, staleness, and the two rules the runner enforces.

This replaced a CLI function whose steps were ordered by the sequence a human
had typed them in. The properties worth testing are the ones that ordering could
not express at all:

* a stage never runs before something it depends on
* a scrape never runs because something downstream of it went stale
* a stage that is fresh on its own terms but sits below a stale one is reported
  as blocked, not as fine
"""
from __future__ import annotations

import pytest

from pulsar_research.pipeline import registry as reg
from pulsar_research.pipeline.runner import plan


# ---------------------------------------------------------------------------
# The declaration itself
# ---------------------------------------------------------------------------


def test_every_dependency_names_a_stage_that_exists():
    for stage in reg.STAGES:
        unknown = set(stage.depends_on) - set(reg.BY_NAME)
        assert not unknown, f"{stage.name} depends on {unknown}"


def test_every_stage_can_say_how_to_run_it_and_how_to_tell_if_it_is_current():
    for stage in reg.STAGES:
        assert stage.run is not None, f"{stage.name} has no body"
        assert stage.freshness is not None, f"{stage.name} has no freshness rule"
        assert stage.why.strip(), f"{stage.name} does not say what it is for"


def test_only_the_stages_that_reach_the_network_are_marked_as_acquiring():
    """The flag is what stops a recomputation from turning into a crawl."""
    assert {s.name for s in reg.STAGES if s.acquires} == {
        "acquire.opportunities", "acquire.professors", "acquire.applications"}
    # The local ones — re-reading an archive, importing a file — are stages
    # too, but they can run without being asked twice.
    assert {s.name for s in reg.STAGES if s.name.startswith("acquire.") and not s.acquires} == {
        "acquire.lattes", "acquire.ledger"}


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_nothing_is_ordered_before_something_it_needs():
    placed: set[str] = set()
    for stage in reg.order():
        assert not (set(stage.depends_on) - placed), f"{stage.name} ran too early"
        placed.add(stage.name)


def test_the_order_is_the_same_on_every_call():
    assert [s.name for s in reg.order()] == [s.name for s in reg.order()]


def test_ordering_a_subset_keeps_the_relative_order_of_that_subset():
    subset = ["graph.metrics", "graph.project", "semantics.space"]
    assert [s.name for s in reg.order(subset)] == [
        "semantics.space", "graph.project", "graph.metrics"]


def test_asking_for_a_stage_that_does_not_exist_raises_rather_than_running_nothing():
    with pytest.raises(KeyError):
        reg.order(["graph.invented"])
    with pytest.raises(KeyError):
        reg.with_dependencies(["graph.invented"])
    with pytest.raises(KeyError):
        reg.dependents("graph.invented")


def test_a_stage_pulls_in_everything_it_transitively_needs():
    needed = reg.with_dependencies(["graph.metrics"])
    assert "graph.project" in needed
    assert "semantics.space" in needed
    assert "acquire.professors" in needed          # two levels up
    assert "semantics.profile" not in needed       # a sibling, not an input


def test_the_blast_radius_is_what_re_running_something_invalidates():
    """The question `sync all` could never answer."""
    assert reg.dependents("semantics.space") == [
        "graph.metrics", "graph.project", "semantics.profile"]
    assert reg.dependents("graph.metrics") == []


# ---------------------------------------------------------------------------
# Freshness, against a real store
# ---------------------------------------------------------------------------


def test_data_present_without_a_journalled_run_is_unknown_not_never(db):
    """A store bootstrapped from a ledger has the rows but not the run that fetched them.

    Calling that "never acquired" would cascade every derived stage into
    `blocked` on a store that is in fact complete.
    """
    state = reg.BY_NAME["acquire.opportunities"].status(db)
    assert state.state == "unknown"
    assert "no run was journalled" in state.detail


def test_a_stage_that_has_genuinely_never_run_says_so(db):
    assert reg.BY_NAME["graph.metrics"].status(db).state == "never"


def test_a_broken_probe_does_not_take_the_pipeline_down(db):
    """One unreadable table must not make `pipeline status` unrunnable."""
    exploding = reg.Stage(name="x", title="X", why="…",
                          freshness=lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
    state = exploding.status(db)
    assert state.state == "unknown"
    assert "boom" in state.detail


def test_a_fresh_stage_below_a_stale_one_is_reported_as_blocked(db):
    """The failure mode the module exists to make unreachable by accident.

    Build the graph without building the semantic space it is declared to depend
    on. `graph.project` is then perfectly current *on its own terms* — its
    fingerprint matches the corpus — and reporting that as `ok` would invite an
    operator to trust a graph assembled from inputs that were never computed.
    """
    from pulsar_research import graph as g
    from pulsar_research.semantics.corpus import load_corpus
    corpus = load_corpus(db)
    projection = g.build(db, corpus)
    g.record_build(db, reg._graph_inputs(db, corpus),
                   g.write_graph(db, projection.entities.values(), projection.edges))

    assert reg.BY_NAME["graph.project"].status(db).state == "ok"
    rows = {row["stage"]: row for row in reg.status(db)}
    assert rows["semantics.space"]["state"] in ("never", "stale")
    assert rows["graph.project"]["state"] == "blocked"
    assert "semantics.space" in rows["graph.project"]["detail"]
    assert rows["graph.project"]["detail"].startswith("waiting on ")
    # And the block propagates: nothing below a blocked stage is trustworthy either.
    assert rows["graph.metrics"]["state"] in ("never", "blocked")


def test_status_reports_every_declared_stage_in_dependency_order(db):
    reported = [row["stage"] for row in reg.status(db)]
    assert reported == [stage.name for stage in reg.order()]


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def test_a_plan_never_includes_a_scrape_unless_it_was_asked_for(db):
    """A derived table going stale is not a reason to hit somebody else's server."""
    assert not any(s.acquires for s in plan(db))
    assert not any(s.acquires for s in plan(db, force=True))
    assert any(s.acquires for s in plan(db, include_acquisition=True, force=True))


def test_force_runs_everything_derived_even_where_nothing_looks_stale(db):
    forced = {s.name for s in plan(db, force=True)}
    assert forced == {s.name for s in reg.STAGES if not s.acquires}


def test_naming_a_stage_runs_it_whether_or_not_the_probe_thinks_it_is_current(db):
    """Asking for a stage by name is the operator overruling the probe."""
    chosen = [s.name for s in plan(db, only=["graph.project"], force=False)]
    assert "graph.project" in chosen


def test_a_plan_is_ordered_even_when_the_request_was_not(db):
    chosen = [s.name for s in plan(db, only=["graph.metrics"], force=True)]
    assert chosen.index("graph.project") < chosen.index("graph.metrics")
