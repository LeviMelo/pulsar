"""The build graph: what has to happen, in what order, and whether it is stale.

The ordering used to live in one CLI function. `sync all` ran opportunities,
then exported a seed CSV, then the public professor scrape, then resolution,
then applications, then semantics — six steps whose dependencies existed only in
the sequence a human had typed. Nothing could answer "is the graph stale", and
nothing could run *just* the part that had gone out of date, so the honest
options were to re-run everything or to guess.

A platform that maintains scraped data cannot work that way. So each stage
declares what it needs, what it produces, and how to tell whether its output
still reflects its inputs, and the runner does the ordering. The result is that
three previously unanswerable questions become one table:

* what is stale, and *why* — because an upstream stage ran, or because nothing
  has run at all
* what would be re-run if I asked for X
* what is the blast radius of re-running Y

Freshness is derived from the store, never from a timestamp file. A stage is
stale when the thing it wrote disagrees with the thing it read — which survives
someone editing the database by hand, moving the project, or restoring a backup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Sequence

from ..config import AppConfig
from ..db import Database

State = Literal["ok", "stale", "never", "blocked", "unknown"]


@dataclass(frozen=True, slots=True)
class Freshness:
    state: State
    detail: str = ""
    at: str | None = None

    @property
    def needs_run(self) -> bool:
        return self.state in ("stale", "never")


@dataclass(frozen=True, slots=True)
class Stage:
    """One unit of the build.

    `why` is not documentation for its own sake: `pulsar pipeline status` prints
    it, so an operator who has not touched the project for a month can see what
    each step is for without reading the source.
    """

    name: str
    title: str
    why: str
    produces: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    #: Acquisition stages reach the network and cost minutes; derived stages are
    #: pure functions of the store. `pipeline run` refuses to touch the first
    #: kind without being asked, because a scrape is not a recomputation.
    acquires: bool = False
    run: Callable[[AppConfig, Database], Any] | None = field(default=None, repr=False)
    freshness: Callable[[Database], Freshness] | None = field(default=None, repr=False)

    def status(self, db: Database) -> Freshness:
        if self.freshness is None:
            return Freshness("unknown", "no freshness rule declared")
        try:
            return self.freshness(db)
        except Exception as exc:      # a broken probe must not hide the pipeline
            return Freshness("unknown", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Freshness probes
# ---------------------------------------------------------------------------


def _last_sync(db: Database, source: str, *, witness: str = "") -> Freshness:
    """When acquisition last journalled this source, and whether it worked.

    `witness` is the table the stage fills. A store bootstrapped from a ledger,
    or restored from a backup taken before the journal existed, holds the data
    without holding the run that fetched it — and calling that "never acquired"
    would cascade every derived stage into `blocked` on a store that is in fact
    complete. The honest answer there is that we do not know when it arrived.
    """
    rows = db.query_df(
        "SELECT status, finished_at FROM sync_runs WHERE source=? "
        "ORDER BY finished_at DESC LIMIT 1", [source])
    if not len(rows):
        if witness and db.table_exists(witness):
            count = int(db.scalar(f'SELECT COUNT(*) FROM "{witness}"', default=0) or 0)
            if count:
                return Freshness("unknown", f"{count:,} rows, but no run was journalled")
        return Freshness("never", "no run journalled")
    status = str(rows.iloc[0]["status"])
    at = str(rows.iloc[0]["finished_at"])
    if status != "ok":
        return Freshness("stale", f"last run {status}", at)
    # Acquisition is never "stale" on its own account: only the source can go
    # out of date, and we cannot see the source without going to fetch it. What
    # this reports is that a successful run happened and when.
    return Freshness("ok", f"last acquired {at[:16]}", at)


def _fingerprint_probe(db: Database, table: str, column: str, label: str):
    def probe(db_: Database) -> Freshness:
        from ..semantics.corpus import load_corpus
        if not db_.table_exists(table):
            return Freshness("never", f"{label} has never been built")
        rows = db_.query_df(f"SELECT {column} AS fp, created_at FROM {table} "
                            "ORDER BY created_at DESC LIMIT 1")
        if not len(rows):
            return Freshness("never", f"{label} has never been built")
        stored = str(rows.iloc[0]["fp"] or "")
        at = str(rows.iloc[0]["created_at"])
        current = load_corpus(db_).fingerprint()
        if stored != current:
            return Freshness("stale", "the corpus has changed since this was built", at)
        return Freshness("ok", f"built {at[:16]} against the current corpus", at)
    return probe


def _space_fresh(db: Database) -> Freshness:
    return _fingerprint_probe(db, "semantic_spaces", "corpus_fingerprint", "the semantic space")(db)


def _graph_fresh(db: Database) -> Freshness:
    return _fingerprint_probe(db, "graph_builds", "corpus_fingerprint", "the entity graph")(db)


def _run_fresh(db: Database) -> Freshness:
    """A profile run is stale when it points at a space that is no longer current."""
    from ..semantics.provenance import current_run_id, current_space_id
    if not db.table_exists("profile_runs"):
        return Freshness("never", "no profile run recorded")
    rows = db.query_df("SELECT run_id, space_id, created_at FROM profile_runs "
                       "ORDER BY created_at DESC LIMIT 1")
    if not len(rows):
        return Freshness("never", "no profile run recorded")
    at = str(rows.iloc[0]["created_at"])
    if str(rows.iloc[0]["space_id"]) != current_space_id(db):
        return Freshness("stale", "scored against a superseded semantic space", at)
    if not current_run_id(db):
        return Freshness("stale", "no run is marked current", at)
    return Freshness("ok", f"scored {at[:16]} against the current space", at)


def _rows_probe(table: str, label: str, *, depends_on_corpus: bool = False):
    def probe(db: Database) -> Freshness:
        if not db.table_exists(table):
            return Freshness("never", f"{label}: never computed")
        count = int(db.scalar(f'SELECT COUNT(*) FROM "{table}"', default=0) or 0)
        if not count:
            return Freshness("never", f"{label}: never computed")
        return Freshness("ok", f"{count:,} rows")
    return probe


# ---------------------------------------------------------------------------
# Stage bodies
# ---------------------------------------------------------------------------


def _acquire_opportunities(config: AppConfig, db: Database) -> Any:
    from ..acquisition.sigaa_authenticated import sync_opportunities
    return sync_opportunities(config)


def _acquire_professors(config: AppConfig, db: Database) -> Any:
    from ..acquisition.ledger import export_professors_csv_for_scraper, resolve_opportunity_professors
    from ..acquisition.sigaa_public import sync_professors
    seed = config.paths.professors_csv
    if int(db.scalar("SELECT COUNT(*) FROM opportunities", default=0) or 0):
        export_professors_csv_for_scraper(db, seed)
    result = sync_professors(config, input_csv=seed)
    resolve_opportunity_professors(db)
    return result


def _acquire_applications(config: AppConfig, db: Database) -> Any:
    from ..acquisition.sigaa_authenticated import sync_applications
    return sync_applications(config)


def _build_space(config: AppConfig, db: Database) -> Any:
    from ..semantics.engine import build_space
    return build_space(config, db)


def _build_profile(config: AppConfig, db: Database) -> Any:
    from ..semantics.engine import SemanticSpace, run_profile
    from ..semantics.provenance import current_space_id
    space_id = current_space_id(db)
    if not space_id:
        raise RuntimeError("no semantic space to score against")
    return run_profile(config, db, SemanticSpace.load(config, db, space_id))


def _build_metrics(config: AppConfig, db: Database) -> Any:
    from ..intelligence.metrics import calculate_metrics
    from ..semantics.corpus import load_corpus
    return calculate_metrics(db, load_corpus(db))


def _build_graph(config: AppConfig, db: Database) -> Any:
    from .. import graph as g
    from ..semantics.corpus import load_corpus
    from ..semantics.provenance import current_space_id
    corpus = load_corpus(db)
    projection = g.build(db, corpus, space_id=current_space_id(db) or None)
    stats = g.write_graph(db, projection.entities.values(), projection.edges)
    g.record_build(db, corpus.fingerprint(), stats)
    return stats


def _build_graph_metrics(config: AppConfig, db: Database) -> Any:
    from .. import graph as g
    from ..graph.model import Relation, is_indexed_person
    from ..intelligence.network import structural_metrics
    adjacency = g.adjacency(db, relations=[Relation.COLLABORATES_WITH])
    inside = [node for node in adjacency if is_indexed_person(node)]
    rows, stats = structural_metrics(adjacency, inside=inside)
    g.write_metrics(db, rows)
    return stats


# ---------------------------------------------------------------------------
# The declared pipeline
# ---------------------------------------------------------------------------

STAGES: tuple[Stage, ...] = (
    Stage(
        name="acquire.opportunities",
        title="Authenticated SIGAA opportunities",
        why="The open calls and their work plans. Everything downstream that "
            "mentions a position starts here.",
        produces=("opportunities", "projects"),
        acquires=True,
        run=_acquire_opportunities,
        freshness=lambda db: _last_sync(db, "opportunities", witness="opportunities"),
    ),
    Stage(
        name="acquire.professors",
        title="Public professor corpus and Lattes",
        why="Portfolios, co-authorship and the accented spelling of every name. "
            "Seeded from the opportunities, so it runs after them.",
        produces=("professors", "sigaa_public_*"),
        depends_on=("acquire.opportunities",),
        acquires=True,
        run=_acquire_professors,
        freshness=lambda db: _last_sync(db, "professors", witness="professors"),
    ),
    Stage(
        name="acquire.applications",
        title="Registered interest",
        why="The authoritative record of what has actually been applied to, "
            "which no derivation can reconstruct.",
        produces=("applications",),
        depends_on=("acquire.opportunities",),
        acquires=True,
        run=_acquire_applications,
        freshness=lambda db: _last_sync(db, "applications", witness="applications"),
    ),
    Stage(
        name="semantics.space",
        title="Semantic space",
        why="Representations, topics, geometry and the retrieval battery. Stale "
            "the moment the corpus changes, because its identity includes the "
            "corpus fingerprint.",
        produces=("semantic_spaces", "semantic_topics", "entity_geometry"),
        depends_on=("acquire.opportunities", "acquire.professors"),
        run=_build_space,
        freshness=_space_fresh,
    ),
    Stage(
        name="semantics.profile",
        title="Profile scoring",
        why="Ranks every entity against the declared interests. Cheap, and "
            "re-run whenever config/profile.yaml changes.",
        produces=("profile_runs", "entity_scores", "professor_evidence"),
        depends_on=("semantics.space",),
        run=_build_profile,
        freshness=_run_fresh,
    ),
    Stage(
        name="intelligence.metrics",
        title="Portfolio and corpus metrics",
        why="Counting facts — funded slots, publication counts, collaboration "
            "pairs — computed from the atom corpus rather than by regex.",
        produces=("professor_metrics", "collaboration_edges", "global_metrics"),
        depends_on=("acquire.professors",),
        run=_build_metrics,
        freshness=_rows_probe("professor_metrics", "portfolio metrics"),
    ),
    Stage(
        name="graph.project",
        title="Entity graph",
        why="Projects every acquired fact into one id space and one relation "
            "vocabulary. This is what the platform reasons over.",
        produces=("graph_entities", "graph_edges"),
        depends_on=("intelligence.metrics", "semantics.space"),
        run=_build_graph,
        freshness=_graph_fresh,
    ),
    Stage(
        name="graph.metrics",
        title="Structural measures",
        why="Centrality, communities, bridging and external reach — the "
            "questions a ranked table cannot answer.",
        produces=("entity_metrics",),
        depends_on=("graph.project",),
        run=_build_graph_metrics,
        freshness=_rows_probe("entity_metrics", "structural measures"),
    ),
)

BY_NAME: dict[str, Stage] = {stage.name: stage for stage in STAGES}


def order(names: Iterable[str] | None = None) -> list[Stage]:
    """Topological order, with a stable tie-break so two runs agree.

    Cycles are a programming error rather than a runtime condition, so this
    raises rather than trying to make progress on a graph that cannot have any.
    """
    wanted = set(names) if names is not None else set(BY_NAME)
    unknown = wanted - set(BY_NAME)
    if unknown:
        raise KeyError(f"unknown stage(s): {sorted(unknown)}")

    done: list[Stage] = []
    placed: set[str] = set()
    pending = sorted(wanted)
    while pending:
        progressed = False
        for name in list(pending):
            stage = BY_NAME[name]
            blocking = [d for d in stage.depends_on if d in wanted and d not in placed]
            if blocking:
                continue
            done.append(stage)
            placed.add(name)
            pending.remove(name)
            progressed = True
        if not progressed:
            raise ValueError(f"dependency cycle among {sorted(pending)}")
    return done


def with_dependencies(names: Iterable[str]) -> list[str]:
    """A stage plus everything it needs, transitively."""
    out: set[str] = set()
    queue = list(names)
    while queue:
        name = queue.pop()
        if name in out:
            continue
        if name not in BY_NAME:
            raise KeyError(f"unknown stage: {name}")
        out.add(name)
        queue.extend(BY_NAME[name].depends_on)
    return sorted(out)


def dependents(name: str) -> list[str]:
    """Everything that would be invalidated by re-running this stage."""
    if name not in BY_NAME:
        raise KeyError(f"unknown stage: {name}")
    out: set[str] = set()
    frontier = {name}
    while frontier:
        nxt = {s.name for s in STAGES if set(s.depends_on) & frontier} - out - {name}
        if not nxt:
            break
        out |= nxt
        frontier = nxt
    return sorted(out)


def status(db: Database) -> list[dict[str, Any]]:
    """Every stage, its freshness, and whether an upstream stage has invalidated it.

    A stage that is fresh on its own terms but sits downstream of a stale one is
    reported as `blocked`, because re-running it alone would produce a confident
    answer from stale inputs — the failure mode this whole module exists to make
    impossible to reach by accident.
    """
    rows: list[dict[str, Any]] = []
    bad: set[str] = set()
    for stage in order():
        fresh = stage.status(db)
        state: State = fresh.state
        detail = fresh.detail
        upstream = [d for d in stage.depends_on if d in bad]
        if upstream and state == "ok":
            state = "blocked"
            detail = f"waiting on {', '.join(upstream)}"
        if state in ("stale", "never", "blocked"):
            bad.add(stage.name)
        rows.append({
            "stage": stage.name,
            "title": stage.title,
            "why": stage.why,
            "state": state,
            "detail": detail,
            "at": fresh.at,
            "acquires": stage.acquires,
            "depends_on": list(stage.depends_on),
            "blocks": dependents(stage.name),
        })
    return rows
