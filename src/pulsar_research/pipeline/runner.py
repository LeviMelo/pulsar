"""Running the declared pipeline.

Two rules, both of which exist because the alternative has already gone wrong
somewhere in this project's history.

*A scrape is not a recomputation.* Acquisition stages reach the network, take
minutes, and are rate-limited by somebody else's server. They never run because
something downstream of them went stale; they run because they were asked for.

*Nothing runs on stale inputs.* A stage whose dependency failed is skipped, not
attempted. A derived table computed from half-updated inputs is worse than a
missing one, because it looks like an answer.
"""

from __future__ import annotations

import traceback
from typing import Any, Callable, Iterable

from ..config import AppConfig
from ..db import Database, json_text, utcnow
from .registry import BY_NAME, Stage, order, with_dependencies


def plan(
    db: Database,
    *,
    only: Iterable[str] | None = None,
    include_acquisition: bool = False,
    force: bool = False,
) -> list[Stage]:
    """Which stages would run, in the order they would run.

    Separated from `run` so `--dry-run` shows exactly what the real invocation
    will do, rather than an approximation of it.
    """
    wanted = with_dependencies(only) if only else list(BY_NAME)
    stages = order(wanted)
    if not include_acquisition:
        stages = [s for s in stages if not s.acquires]
    if force:
        return stages

    chosen: list[Stage] = []
    dirty: set[str] = set()
    for stage in stages:
        needs = stage.status(db).needs_run or bool(set(stage.depends_on) & dirty)
        # An explicitly named stage runs whether or not it looks fresh: asking
        # for it by name is the operator overruling the probe.
        if needs or (only and stage.name in set(only)):
            chosen.append(stage)
            dirty.add(stage.name)
    return chosen


def run(
    config: AppConfig,
    db: Database,
    *,
    only: Iterable[str] | None = None,
    include_acquisition: bool = False,
    force: bool = False,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Execute the plan, journalling every stage into `sync_runs`.

    The journal is the same table acquisition already wrote to, so freshness,
    history and failure all have one home rather than one per subsystem.
    """
    stages = plan(db, only=only, include_acquisition=include_acquisition, force=force)
    if not stages:
        log("Nothing to do — every stage is current.")
        return []

    done: set[str] = set()
    results: list[dict[str, Any]] = []
    for index, stage in enumerate(stages, start=1):
        blocked = [d for d in stage.depends_on if d in _failed(results)]
        if blocked:
            log(f"[{index}/{len(stages)}] {stage.name}: skipped — {', '.join(blocked)} failed")
            results.append({"stage": stage.name, "status": "skipped",
                            "detail": f"upstream failed: {', '.join(blocked)}"})
            continue

        log(f"[{index}/{len(stages)}] {stage.title} ({stage.name})")
        started = utcnow()
        try:
            payload = stage.run(config, db) if stage.run else None
            outcome = {"stage": stage.name, "status": "ok", "result": payload}
        except Exception as exc:
            traceback.print_exc()
            outcome = {"stage": stage.name, "status": "failed",
                       "detail": f"{type(exc).__name__}: {exc}"}
        _journal(db, stage, started, outcome)
        results.append(outcome)
        done.add(stage.name)
    return results


def _failed(results: Iterable[dict[str, Any]]) -> set[str]:
    return {r["stage"] for r in results if r["status"] in ("failed", "skipped")}


def _journal(db: Database, stage: Stage, started: str, outcome: dict[str, Any]) -> None:
    # Acquisition stages journal themselves under their source name so the
    # existing freshness probes keep working; derived stages journal under the
    # stage name, which is what their probes read.
    payload = {k: v for k, v in outcome.items() if k != "stage"}
    with db.connect() as con:
        con.execute(
            "INSERT INTO sync_runs (run_id, source, started_at, finished_at, status, details_json) "
            "VALUES (?,?,?,?,?,?)",
            [f"pipeline-{stage.name}", stage.name, started, utcnow(),
             outcome["status"], json_text(payload)])
