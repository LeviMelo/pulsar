"""The sources PULSAR knows how to read, declared in one place.

Each entry says what the source is and how it is reached; the pipeline turns
each into a stage, the CLI lists and runs them by id, `doctor` reports whether
they can run here. Adding a source is adding an entry — the run body can live
wherever the fetching code already does.

The ids are two-level, `provider.what`, because the same provider is read
several ways: SIGAA gives the opportunities through a logged-in browser and the
professors through a public crawl, and those have nothing in common but the
hostname.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..db import Database
from .model import Access, Capture, Method, RunOptions, Source
from .runs import last_run


# ---------------------------------------------------------------------------
# Run bodies. Thin: they call the acquisition code and describe what it did.
# ---------------------------------------------------------------------------


def _count(db: Database, table: str) -> int:
    if not db.table_exists(table):
        return 0
    return int(db.scalar(f'SELECT COUNT(*) FROM "{table}"', default=0) or 0)


def _run_opportunities(config: AppConfig, db: Database, options: RunOptions) -> Capture:
    from ..acquisition.sigaa_authenticated import sync_opportunities
    capture = Capture("sigaa.opportunities")
    if options.dry_run:
        capture.notes["would"] = "log in, search every configured centre, backfill plan details"
        return capture
    result = sync_opportunities(config)
    capture.notes.update(result if isinstance(result, dict) else {"result": str(result)})
    capture.rows = {t: _count(db, t) for t in ("opportunities", "projects")}
    return capture


def _run_professors(config: AppConfig, db: Database, options: RunOptions) -> Capture:
    from ..acquisition.ledger import export_professors_csv_for_scraper, resolve_opportunity_professors
    from ..acquisition.sigaa_public import sync_professors
    capture = Capture("sigaa.professors")
    seed = config.paths.professors_csv
    if options.dry_run:
        capture.notes["would"] = (f"seed {seed.name} from the opportunities, crawl seven tabs per "
                                  f"professor into {config.paths.public_dataset_dir}, import")
        return capture
    if _count(db, "opportunities"):
        capture.notes["seeded"] = export_professors_csv_for_scraper(db, seed)
    result = sync_professors(config, input_csv=seed, refresh=options.refresh)
    capture.notes["resolved_supervisors"] = resolve_opportunity_professors(db)
    capture.rows = dict(result.get("imported") or {})
    capture.rows["professors"] = _count(db, "professors")
    capture.artifacts = (str(result.get("dataset_dir") or config.paths.public_dataset_dir),)
    return capture


def _run_applications(config: AppConfig, db: Database, options: RunOptions) -> Capture:
    from ..acquisition.sigaa_authenticated import sync_applications
    capture = Capture("sigaa.applications")
    if options.dry_run:
        capture.notes["would"] = "log in and read the applicant's own registration table"
        return capture
    result = sync_applications(config)
    capture.notes.update(result if isinstance(result, dict) else {"result": str(result)})
    capture.rows = {"applications": _count(db, "applications")}
    return capture


#: The archive tables the embedded Lattes object lands in. Re-importable
#: without a crawl because the scraper keeps the parsed object on disk.
LATTES_TABLES = ("lattes_documents", "lattes_flat")


def _run_lattes(config: AppConfig, db: Database, options: RunOptions) -> Capture:
    from ..acquisition.public_dataset import import_public_dataset
    capture = Capture("lattes.embedded")
    archive = config.paths.public_dataset_dir / "sigaa_ufal.duckdb"
    capture.artifacts = (str(archive),)
    if options.dry_run:
        capture.notes["would"] = f"re-read {', '.join(LATTES_TABLES)} from {archive}"
        return capture
    capture.rows = import_public_dataset(db, config.paths.public_dataset_dir, only=LATTES_TABLES)
    return capture


def _run_ledger(config: AppConfig, db: Database, options: RunOptions) -> Capture:
    from ..acquisition.ledger import import_ledger_file
    capture = Capture("ledger.file")
    path = _ledger_path(config)
    capture.artifacts = (str(path),) if path else ()
    if path is None:
        capture.errors.append("no ledger file exists yet")
        return capture
    if options.dry_run:
        capture.notes["would"] = f"import {path}"
        return capture
    capture.rows = {"opportunities": import_ledger_file(db, path)}
    return capture


def _ledger_path(config: AppConfig) -> Path | None:
    for candidate in (config.paths.opportunity_ledger_json, config.paths.opportunity_ledger_csv):
        if candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Change probes for local inputs
# ---------------------------------------------------------------------------


def _file_changed(path: Path | None, db: Database, source_id: str) -> str | None:
    """A file newer than the last run is a reason to run again."""
    if path is None or not path.exists():
        return None
    from datetime import datetime, timezone
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    run = last_run(db, source_id)
    if run is None:
        # Never having run is reported by the run column, not as a change.
        return None
    finished = str(run.get("finished_at") or "")
    try:
        last = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    if modified > last:
        return f"{path.name} changed after the last import"
    return None


# ---------------------------------------------------------------------------
# The declarations
# ---------------------------------------------------------------------------

SOURCES: tuple[Source, ...] = (
    Source(
        id="sigaa.opportunities",
        title="Authenticated SIGAA opportunities",
        provider="UFAL SIGAA (portal discente)",
        method=Method.BROWSER_SESSION,
        access=Access.AUTHENTICATED,
        why="The open calls and their work plans. Everything downstream that "
            "mentions a position starts here.",
        yields=("opportunities", "projects"),
        stage="acquire.opportunities",
        run=_run_opportunities,
        secrets=("UFAL_SIGAA_USERNAME", "UFAL_SIGAA_PASSWORD"),
        config_section="sigaa",
        rate="one driven browser, one request at a time",
        cost="minutes; one wizard page per work plan",
        witness="opportunities",
    ),
    Source(
        id="sigaa.professors",
        title="Public professor corpus",
        provider="UFAL SIGAA (public faculty pages)",
        method=Method.HTTP_CRAWL,
        access=Access.PUBLIC,
        why="Seven public tabs per professor — profile, courses, production, "
            "projects, extension, mentoring — archived raw, plus the embedded CV.",
        yields=("professors", "professor_aliases", "sigaa_public_*"),
        stage="acquire.professors",
        run=_run_professors,
        depends_on=("sigaa.opportunities",),
        config_section="sigaa",
        rate="public_delay_seconds between requests; cached fetches reused unless --refresh",
        cost="tens of minutes for the faculty; hours with --refresh",
        witness="professors",
    ),
    Source(
        id="lattes.embedded",
        title="Lattes curricula",
        provider="CNPq Lattes, as embedded by SIGAA",
        method=Method.EMBEDDED,
        access=Access.LOCAL,
        why="The full CV object SIGAA embeds in each production page: every "
            "work, appointment, degree, committee and supervision. Never fetched "
            "from CNPq; carved out of the public archive on disk.",
        yields=tuple(f"sigaa_public_{t}" for t in LATTES_TABLES),
        stage="acquire.lattes",
        run=_run_lattes,
        depends_on=("sigaa.professors",),
        cost="seconds",
        witness="sigaa_public_lattes_flat",
        changed=lambda config, db: _file_changed(
            config.paths.public_dataset_dir / "sigaa_ufal.duckdb", db, "lattes.embedded"),
    ),
    Source(
        id="sigaa.applications",
        title="Registered interest",
        provider="UFAL SIGAA (portal discente)",
        method=Method.BROWSER_SESSION,
        access=Access.AUTHENTICATED,
        why="The authoritative record of what has actually been applied to, "
            "which no derivation can reconstruct.",
        yields=("applications",),
        stage="acquire.applications",
        run=_run_applications,
        depends_on=("sigaa.opportunities",),
        secrets=("UFAL_SIGAA_USERNAME", "UFAL_SIGAA_PASSWORD"),
        config_section="sigaa",
        cost="a minute",
        witness="applications",
    ),
    Source(
        id="ledger.file",
        title="Opportunity ledger file",
        provider="this project's own checkpoint",
        method=Method.FILE_IMPORT,
        access=Access.LOCAL,
        why="The JSON/CSV checkpoint a long crawl writes after every row. "
            "Importing it restores a store from provenance without a login.",
        yields=("opportunities",),
        stage="acquire.ledger",
        run=_run_ledger,
        cost="seconds",
        changed=lambda config, db: _file_changed(_ledger_path(config), db, "ledger.file"),
        implicit=False,
    ),
)

BY_ID: dict[str, Source] = {source.id: source for source in SOURCES}
BY_STAGE: dict[str, Source] = {source.stage: source for source in SOURCES}


def get(source_id: str) -> Source:
    try:
        return BY_ID[source_id]
    except KeyError:
        known = ", ".join(sorted(BY_ID))
        raise KeyError(f"unknown source {source_id!r}; known: {known}") from None


def describe(config: AppConfig, db: Database) -> list[dict[str, Any]]:
    """Every source with its readiness and last run, for the CLI and the console."""
    from .runs import readiness
    out = []
    for source in SOURCES:
        run = last_run(db, source.id)
        problems = readiness(config, source)
        changed = None
        if source.changed is not None:
            try:
                changed = source.changed(config, db)
            except Exception as exc:      # a probe must never take the listing down
                changed = f"probe failed: {type(exc).__name__}"
        out.append({
            "id": source.id,
            "title": source.title,
            "provider": source.provider,
            "method": source.method.value,
            "access": source.access.value,
            "reaches_network": source.reaches_network,
            "stage": source.stage,
            "yields": list(source.yields),
            "depends_on": list(source.depends_on),
            "secrets": list(source.secrets),
            "rate": source.rate,
            "cost": source.cost,
            "why": source.why,
            "ready": not problems,
            "problems": problems,
            "changed": changed,
            "last_run": None if run is None else {
                "status": run.get("status"), "finished_at": run.get("finished_at"),
                "rows": (run.get("details") or {}).get("rows") or {},
                "error": (run.get("details") or {}).get("error") or "",
            },
        })
    return out
