"""The run journal: one place every acquisition writes to, whichever way it ran.

Before this, three things wrote to `sync_runs` — the `sync` CLI commands, the
pipeline runner, and nothing else — with two different ideas of what
`details_json` held. A freshness probe reading the table had to know which
writer had produced the row. Now a run is journalled here, under the source id,
with the `Capture` the source returned, and both the CLI and the pipeline go
through this function.

A run that dies halfway is the run you most want a record of, so the failure
path writes its row before re-raising.
"""

from __future__ import annotations

import traceback
from typing import Any
from uuid import uuid4

from ..config import AppConfig
from ..db import Database, json_load, json_text, utcnow
from .model import Capture, RunOptions, Source


class SourceUnavailable(RuntimeError):
    """The source cannot run on this machine as configured."""


def journalled_run(config: AppConfig, db: Database, source: Source,
                   options: RunOptions | None = None) -> Capture:
    options = options or RunOptions()
    problems = readiness(config, source)
    if problems:
        raise SourceUnavailable(f"{source.id}: " + "; ".join(problems))

    run_id = uuid4().hex[:12]
    started = utcnow()
    try:
        capture = source.run(config, db, options)
    except Exception as exc:
        _write(db, run_id, source.id, started, "failed", {
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-4000:],
            "options": options.__dict__ if hasattr(options, "__dict__") else _opts(options),
        })
        raise
    if options.dry_run:
        return capture
    payload = capture.as_dict()
    payload["options"] = _opts(options)
    _write(db, run_id, source.id, started, "failed" if capture.errors else "ok", payload)
    return capture


def _opts(options: RunOptions) -> dict[str, Any]:
    return {"refresh": options.refresh, "limit": options.limit, "dry_run": options.dry_run}


def _write(db: Database, run_id: str, source_id: str, started: str, status: str,
           details: dict[str, Any]) -> None:
    with db.connect() as con:
        con.execute(
            "INSERT INTO sync_runs (run_id, source, started_at, finished_at, status, details_json) "
            "VALUES (?,?,?,?,?,?)",
            [run_id, source_id, started, utcnow(), status, json_text(details)])


def readiness(config: AppConfig, source: Source) -> list[str]:
    """Why this source could not run here, as a list of plain sentences.

    Empty means ready. Checked before a run and shown by `pulsar sources list`,
    so an operator learns about a missing password from a table, not from a
    Playwright stack trace twenty seconds into a login.
    """
    import os

    problems: list[str] = []
    for name in source.secrets:
        if not os.getenv(name):
            problems.append(f"environment variable {name} is not set")
    section = config.raw.get("sources", {}).get(source.id, {})
    if section.get("enabled") is False:
        problems.append("disabled in config [sources]")
    return problems


def last_run(db: Database, source_id: str) -> dict[str, Any] | None:
    """The most recent journal row for a source, or None if it never ran."""
    if not db.table_exists("sync_runs"):
        return None
    # Rows journalled before sources had ids sit under the bare suffix
    # ("professors"). Read both, so a store that predates the registry still
    # knows when it last fetched.
    row = db.query_df(
        "SELECT run_id, status, started_at, finished_at, details_json FROM sync_runs "
        "WHERE source IN (?, ?) ORDER BY finished_at DESC LIMIT 1",
        [source_id, source_id.rsplit(".", 1)[-1]])
    if not len(row):
        return None
    record = row.iloc[0].to_dict()
    record["details"] = json_load(record.pop("details_json"), {}) or {}
    return record


def history(db: Database, source_id: str | None = None, *, limit: int = 20) -> list[dict[str, Any]]:
    if not db.table_exists("sync_runs"):
        return []
    where = "WHERE source=?" if source_id else ""
    params = [source_id] if source_id else []
    frame = db.query_df(
        f"SELECT run_id, source, status, started_at, finished_at, details_json FROM sync_runs "
        f"{where} ORDER BY finished_at DESC LIMIT {int(limit)}", params)
    out = []
    for row in frame.itertuples():
        details = json_load(row.details_json, {}) or {}
        out.append({
            "run_id": row.run_id, "source": row.source, "status": row.status,
            "started_at": row.started_at, "finished_at": row.finished_at,
            "rows": details.get("rows") or {},
            "error": details.get("error") or "",
        })
    return out
