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
    # Runs journalled before sources had ids sit under the bare stage suffix
    # ("professors"); reading both keeps a long-lived store from reporting
    # "never" for a corpus it plainly holds.
    legacy = source.rsplit(".", 1)[-1]
    rows = db.query_df(
        "SELECT status, finished_at FROM sync_runs WHERE source IN (?, ?) "
        "ORDER BY finished_at DESC LIMIT 1", [source, legacy])
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


def _source_stages() -> tuple[Stage, ...]:
    """One stage per declared source, so acquisition is never listed twice."""
    from ..sources import SOURCES, RunOptions, journalled_run
    from ..sources.registry import BY_ID

    stages = []
    for source in SOURCES:
        def run(config: AppConfig, db: Database, *, _s=source) -> Any:
            return journalled_run(config, db, _s, RunOptions()).as_dict()

        def fresh(db: Database, *, _s=source) -> Freshness:
            base = _last_sync(db, _s.id, witness=_s.witness)
            if not _s.implicit and base.state == "never":
                return Freshness("unknown", f"never imported; `pulsar sources run {_s.id}`")
            if _s.changed is not None and base.state == "ok":
                try:
                    reason = _s.changed(AppConfig.load(), db)
                except Exception:      # a probe must not take the pipeline down
                    reason = None
                if reason:
                    return Freshness("stale", reason, base.at)
            return base

        stages.append(Stage(
            name=source.stage,
            title=source.title,
            why=source.why,
            produces=source.yields,
            depends_on=tuple(BY_ID[d].stage for d in source.depends_on),
            acquires=source.reaches_network,
            run=run,
            freshness=fresh,
        ))
    return tuple(stages)


def _build_records(config: AppConfig, db: Database) -> Any:
    from ..records import build
    return build(db)


def _records_fresh(db: Database) -> Freshness:
    from ..records.store import fingerprint, last_build
    build = last_build(db)
    if build is None:
        return Freshness("never", "the archive has never been read into records")
    at = str(build.get("created_at") or "")
    if build.get("fingerprint") != fingerprint(db):
        return Freshness("stale", "acquisition changed since the records were extracted", at)
    stats = build.get("stats") or {}
    return Freshness("ok", f"{int(stats.get('records', 0)):,} records extracted {at[:16]}", at)


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
    # How much of the graph's identity was concluded rather than read. A build
    # that starts inferring hundreds of merges is a build to look at.
    stats["inferred_identities"] = len(projection.inferred_identities)
    g.record_build(db, corpus.fingerprint(), stats)
    return stats


def _build_graph_metrics(config: AppConfig, db: Database) -> Any:
    """Two measurements of the same relation, because they answer two questions.

    The whole co-authorship graph says which research world a person lives in,
    and for this faculty the answer is mostly "their own": externals outnumber
    colleagues fifty to one, so almost every professor lands in a community made
    of their own co-authors. That is a real finding and a useless colouring.

    The graph induced on the indexed faculty answers the other question — which
    clusters this faculty decomposes into, as against the units it is
    administratively divided into — and it is the one the console draws. Both are
    stored, the second under a `faculty_` prefix, because `entity_metrics` is
    tall and a second measurement is rows rather than a migration.
    """
    from .. import graph as g
    from ..graph.model import Relation, is_indexed_person
    from ..intelligence.network import structural_metrics
    adjacency = g.adjacency(db, relations=[Relation.COLLABORATES_WITH])
    inside = [node for node in adjacency if is_indexed_person(node)]
    rows, stats = structural_metrics(adjacency, inside=inside)

    induced = {node: {other: weight for other, weight in adjacency[node].items()
                      if is_indexed_person(other)}
               for node in inside}
    faculty_rows, faculty_stats = structural_metrics(induced)
    rows.extend((entity_id, f"faculty_{metric}", value, extra)
                for entity_id, metric, value, extra in faculty_rows)

    g.write_metrics(db, rows)
    return {**stats, **{f"faculty_{k}": v for k, v in faculty_stats.items()}}


# ---------------------------------------------------------------------------
# The declared pipeline
# ---------------------------------------------------------------------------

STAGES: tuple[Stage, ...] = (
    *_source_stages(),
    Stage(
        name="records.extract",
        title="Records from the archive",
        why="Every work, appointment, degree, board, student, event and course, "
            "read out of the Lattes object and the public tables into one typed "
            "table. This is what makes the archive searchable.",
        produces=("records", "record_people"),
        depends_on=("acquire.lattes", "acquire.professors"),
        run=_build_records,
        freshness=_records_fresh,
    ),
    Stage(
        name="semantics.space",
        title="Semantic space",
        why="Representations, topics, geometry and the retrieval battery. Stale "
            "the moment the corpus changes, because its identity includes the "
            "corpus fingerprint.",
        produces=("semantic_spaces", "semantic_topics", "entity_geometry"),
        depends_on=("acquire.opportunities", "acquire.lattes"),
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
        depends_on=("acquire.lattes",),
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
