"""DuckDB canonical store: schema, migrations and small query helpers.

Layering rule: **acquired** tables (`opportunities`, `professors`,
`sigaa_public_*`) are facts and are only ever written by acquisition.
**Derived** tables (everything keyed by `space_id` or `run_id`) are disposable
and always rebuildable from the acquired tables plus configuration.

Nothing here holds a long-lived connection. DuckDB takes a process-level write
lock, so short scoped connections keep the CLI, the dashboard and a background
embedding job from deadlocking each other.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import duckdb

SCHEMA_VERSION = "3"


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Acquired facts
# ---------------------------------------------------------------------------

ACQUIRED_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS meta (
    key VARCHAR PRIMARY KEY,
    value VARCHAR,
    updated_at VARCHAR
);

CREATE TABLE IF NOT EXISTS professors (
    siape VARCHAR PRIMARY KEY,
    canonical_name VARCHAR,
    department VARCHAR,
    unit VARCHAR,
    center VARCHAR,
    email VARCHAR,
    phone VARCHAR,
    profile_url VARCHAR,
    photo_url VARCHAR,
    lattes_id VARCHAR,
    lattes_update_date VARCHAR,
    lattes_update_time VARCHAR,
    profile_summary VARCHAR,
    public_scraped_at VARCHAR,
    source_json_path VARCHAR
);

CREATE TABLE IF NOT EXISTS professor_aliases (
    alias VARCHAR,
    normalized_alias VARCHAR,
    siape VARCHAR,
    source VARCHAR,
    match_score DOUBLE,
    created_at VARCHAR
);

CREATE TABLE IF NOT EXISTS projects (
    project_code VARCHAR PRIMARY KEY,
    title VARCHAR,
    area VARCHAR,
    first_seen_at VARCHAR,
    last_seen_at VARCHAR
);

CREATE TABLE IF NOT EXISTS opportunities (
    id_opportunity VARCHAR PRIMARY KEY,
    project_code VARCHAR,
    project_title VARCHAR,
    plan_title VARCHAR,
    professor_siape VARCHAR,
    professor_name VARCHAR,
    vacancies_text VARCHAR,
    funded_slots BIGINT,
    has_funding BOOLEAN,
    funding_basis VARCHAR,
    unit VARCHAR,
    department VARCHAR,
    center VARCHAR,
    large_area VARCHAR,
    area VARCHAR,
    edital VARCHAR,
    quota VARCHAR,
    status VARCHAR,
    has_apply_link BOOLEAN,
    has_details_link BOOLEAN,
    details_collected BOOLEAN,
    introduction_justification VARCHAR,
    objectives VARCHAR,
    methodology VARCHAR,
    acquired_skills VARCHAR,
    references_text VARCHAR,
    discovered_at VARCHAR,
    updated_at VARCHAR,
    applied_at VARCHAR
);

CREATE TABLE IF NOT EXISTS applications (
    id_opportunity VARCHAR PRIMARY KEY,
    status VARCHAR,
    raw_status VARCHAR,
    applied_at VARCHAR,
    synced_at VARCHAR
);

CREATE TABLE IF NOT EXISTS sync_runs (
    run_id VARCHAR,
    source VARCHAR,
    started_at VARCHAR,
    finished_at VARCHAR,
    status VARCHAR,
    details_json VARCHAR
);
"""

# ---------------------------------------------------------------------------
# Derived intelligence
# ---------------------------------------------------------------------------

DERIVED_SCHEMA = r"""
-- A semantic space is corpus + preprocessing + architecture + model identity.
-- Topics, geometry, skills and benchmarks belong to a space.
CREATE TABLE IF NOT EXISTS semantic_spaces (
    space_id VARCHAR PRIMARY KEY,
    corpus_fingerprint VARCHAR,
    identity_json VARCHAR,
    stats_json VARCHAR,
    versions_json VARCHAR,
    created_at VARCHAR
);

-- A profile run is a semantic space plus the operator's interests.
-- Rankings and evidence belong to a run; editing a personal interest must not
-- invalidate the landscape.
CREATE TABLE IF NOT EXISTS profile_runs (
    run_id VARCHAR PRIMARY KEY,
    space_id VARCHAR,
    profile_json VARCHAR,
    stats_json VARCHAR,
    created_at VARCHAR
);

-- Tall score table. One row per (entity, facet, channel) so lexical relevance,
-- latent affinity and neural affinity stay separately inspectable instead of
-- being averaged into one mystical number.
CREATE TABLE IF NOT EXISTS entity_scores (
    run_id VARCHAR,
    entity_type VARCHAR,
    entity_id VARCHAR,
    facet VARCHAR,
    channel VARCHAR,
    score DOUBLE,
    percentile DOUBLE,
    computed_at VARCHAR
);

CREATE TABLE IF NOT EXISTS entity_geometry (
    space_id VARCHAR,
    entity_type VARCHAR,
    entity_id VARCHAR,
    x DOUBLE,
    y DOUBLE,
    cluster_id VARCHAR,
    computed_at VARCHAR
);

CREATE TABLE IF NOT EXISTS semantic_topics (
    space_id VARCHAR,
    facet VARCHAR,
    topic_id VARCHAR,
    parent_id VARCHAR,
    depth BIGINT,
    label VARCHAR,
    terms_json VARCHAR,
    diagnostics_json VARCHAR,
    computed_at VARCHAR
);

CREATE TABLE IF NOT EXISTS entity_topics (
    space_id VARCHAR,
    entity_type VARCHAR,
    entity_id VARCHAR,
    facet VARCHAR,
    topic_id VARCHAR,
    weight DOUBLE,
    is_dominant BOOLEAN,
    computed_at VARCHAR
);

-- Skills are a multi-label set, never a soft partition. `generic` marks
-- competencies every project claims, which are recorded but never scored.
CREATE TABLE IF NOT EXISTS entity_skills (
    space_id VARCHAR,
    entity_type VARCHAR,
    entity_id VARCHAR,
    skill_id VARCHAR,
    label VARCHAR,
    category VARCHAR,
    generic BOOLEAN,
    mentions BIGINT,
    evidence VARCHAR,
    computed_at VARCHAR
);

-- Atomic research evidence. This is what makes a ranking explainable.
CREATE TABLE IF NOT EXISTS professor_evidence (
    run_id VARCHAR,
    siape VARCHAR,
    scope VARCHAR,          -- 'current' | 'trajectory'
    evidence_rank BIGINT,
    atom_id VARCHAR,
    kind VARCHAR,
    label VARCHAR,
    year BIGINT,
    score DOUBLE,
    weight DOUBLE,
    payload_json VARCHAR,
    computed_at VARCHAR
);

CREATE TABLE IF NOT EXISTS semantic_benchmarks (
    space_id VARCHAR,
    benchmark VARCHAR,
    channel VARCHAR,
    metric VARCHAR,
    value DOUBLE,
    note VARCHAR,
    computed_at VARCHAR
);

CREATE TABLE IF NOT EXISTS map_diagnostics (
    space_id VARCHAR,
    entity_type VARCHAR,
    metric VARCHAR,
    value DOUBLE,
    computed_at VARCHAR
);


CREATE TABLE IF NOT EXISTS collaboration_edges (
    source_siape VARCHAR,
    target_siape VARCHAR,
    collaborator_name VARCHAR,
    weight BIGINT,
    calculated_at VARCHAR
);

CREATE TABLE IF NOT EXISTS global_metrics (
    metric VARCHAR PRIMARY KEY,
    value DOUBLE,
    payload_json VARCHAR,
    calculated_at VARCHAR
);

CREATE TABLE IF NOT EXISTS embedding_cache (
    provider_key VARCHAR,
    text_sha256 VARCHAR,
    dimension BIGINT,
    vector BLOB,
    created_at VARCHAR,
    PRIMARY KEY (provider_key, text_sha256)
);
"""

PROFESSOR_METRICS_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS professor_metrics (
    siape VARCHAR PRIMARY KEY,
    opportunity_count BIGINT,
    funded_opportunity_count BIGINT,
    funded_slots BIGINT,
    current_project_count BIGINT,
    public_project_count BIGINT,
    lattes_project_count BIGINT,
    publication_count BIGINT,
    orientation_count BIGINT,
    funding_agency_count BIGINT,
    collaborator_count BIGINT,
    research_area_count BIGINT,
    atom_count BIGINT,
    latest_evidence_year BIGINT,
    calculated_at VARCHAR
);
"""

# ---------------------------------------------------------------------------
# Outreach
# ---------------------------------------------------------------------------

OUTREACH_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id VARCHAR PRIMARY KEY,
    name VARCHAR,
    audience_query_json VARCHAR,
    provenance_json VARCHAR,
    attachments_json VARCHAR,
    subject_template VARCHAR,
    body_template VARCHAR,
    theme VARCHAR,
    status VARCHAR,
    created_at VARCHAR,
    updated_at VARCHAR
);

CREATE TABLE IF NOT EXISTS campaign_recipients (
    campaign_id VARCHAR,
    siape VARCHAR,
    professor_name VARCHAR,
    email VARCHAR,
    qualifying_evidence_json VARCHAR,
    rationale_json VARCHAR,
    selection_score DOUBLE,
    selected BOOLEAN,
    created_at VARCHAR
);

CREATE TABLE IF NOT EXISTS campaign_messages (
    campaign_id VARCHAR,
    siape VARCHAR,
    subject VARCHAR,
    body_text VARCHAR,
    body_html VARCHAR,
    is_customized BOOLEAN,
    status VARCHAR,
    rendered_at VARCHAR,
    sent_at VARCHAR,
    provider_message_id VARCHAR,
    error VARCHAR
);

-- What happened after a message was sent. Outreach is not finished when the
-- mail leaves: a reply arrives, a vacancy turns out to be taken, a conversation
-- goes quiet, and exactly one of them ends in a SIGAA indication. None of that
-- was recordable anywhere, so it lived in the operator's memory.
CREATE TABLE IF NOT EXISTS campaign_outcomes (
    campaign_id VARCHAR,
    siape VARCHAR,
    state VARCHAR,
    note VARCHAR,
    replied_at VARCHAR,
    updated_at VARCHAR
);

"""

#: Tables from the v1/v2 engines whose content is fully superseded. They are
#: dropped on migration; every value in them is rebuildable from acquired data.
LEGACY_TABLES = (
    "analysis_documents",
    "analysis_scores",
    "analysis_topics",
    "semantic_runs",
    "semantic_entity_scores",
    "semantic_entity_topics",
    "semantic_professor_evidence",
)


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(str(self.path), read_only=read_only)
        try:
            yield con
        finally:
            con.close()

    def initialize(self) -> dict[str, Any]:
        """Create the schema and migrate away from superseded engines."""
        report: dict[str, Any] = {"dropped": [], "rebuilt": [], "created": True}
        with self.connect() as con:
            existing = _existing_tables(con)
            report["dropped"] = _drop_legacy(con, existing)
            # `CREATE TABLE IF NOT EXISTS` cannot reshape a table that an older
            # engine created with different columns, and it fails silently, so
            # every derived table is reconciled against the current DDL first.
            # Derived tables are disposable by definition; acquired and outreach
            # tables hold state and are never dropped here.
            report["rebuilt"] = _reconcile_derived(con, existing)
            for block in (ACQUIRED_SCHEMA, DERIVED_SCHEMA, PROFESSOR_METRICS_SCHEMA, OUTREACH_SCHEMA):
                con.execute(block)
            # A populated outreach table is never dropped, so it can only gain
            # columns by ALTER. Done on every run against the declared DDL rather
            # than gated on a version number, which someone always forgets to bump.
            report["widened"] = _widen_outreach(con)
            previous = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            previous_version = str(previous[0]) if previous else "0"
            if previous_version != SCHEMA_VERSION:
                report.update(_migrate(con, previous_version, existing))
                report["dropped"] = sorted(set(report["dropped"]) | set(report.get("migration_dropped", [])))
                report.pop("migration_dropped", None)
            set_meta(con, "schema_version", SCHEMA_VERSION)
            con.execute("CHECKPOINT")
        return report

    def query_df(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None):
        with self.connect(read_only=True) as con:
            return con.execute(sql, list(params or [])).df()

    def execute(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> None:
        with self.connect() as con:
            con.execute(sql, list(params or []))

    def table_exists(self, table: str) -> bool:
        with self.connect(read_only=True) as con:
            return bool(con.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name=?", [table]
            ).fetchone()[0])

    def scalar(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None, default: Any = None) -> Any:
        with self.connect(read_only=True) as con:
            row = con.execute(sql, list(params or [])).fetchone()
        return row[0] if row and row[0] is not None else default

    def counts(self) -> dict[str, int]:
        tables = ["professors", "projects", "opportunities", "applications",
                  "entity_scores", "professor_evidence", "campaigns", "embedding_cache"]
        out: dict[str, int] = {}
        with self.connect(read_only=True) as con:
            for table in tables:
                try:
                    out[table] = int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                except Exception:
                    out[table] = 0
        return out


_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\((.*?)\);",
    re.IGNORECASE | re.DOTALL)


def _declared_columns(*blocks: str) -> dict[str, tuple[str, ...]]:
    """Column names per table, in DDL order, parsed from the schema constants.

    The DDL text stays the single source of truth; nothing restates a column list.
    """
    out: dict[str, tuple[str, ...]] = {}
    for block in blocks:
        for table, body in _CREATE_RE.findall(block):
            cols: list[str] = []
            for line in body.splitlines():
                line = line.strip().strip(",")
                if not line or line.startswith("--") or line.upper().startswith(("PRIMARY", "UNIQUE", "FOREIGN")):
                    continue
                cols.append(line.split()[0].strip('"'))
            out[table.lower()] = tuple(cols)
    return out


DERIVED_TABLES = _declared_columns(DERIVED_SCHEMA, PROFESSOR_METRICS_SCHEMA)
OUTREACH_TABLES = _declared_columns(OUTREACH_SCHEMA)


def _existing_tables(con) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()}


def _drop_legacy(con, existing: set[str]) -> list[str]:
    dropped = [t for t in LEGACY_TABLES if t in existing]
    for table in dropped:
        con.execute(f'DROP TABLE IF EXISTS "{table}"')
    return dropped


def _reconcile_derived(con, existing: set[str]) -> list[str]:
    """Drop tables whose live shape no longer matches the declared DDL.

    Derived tables are disposable and are always rebuilt. Outreach tables hold
    real state, so they are rebuilt only when empty — that clears the column
    *order* drift an `ALTER TABLE ADD COLUMN` migration leaves behind without
    ever discarding a campaign. Writers use named columns regardless.
    """
    rebuilt: list[str] = []
    for table, expected in list(DERIVED_TABLES.items()) + list(OUTREACH_TABLES.items()):
        if table not in existing:
            continue
        live = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=? "
            "ORDER BY ordinal_position", [table]).fetchall()]
        if set(live) == set(expected) and (table in DERIVED_TABLES or live == list(expected)):
            continue
        if table in OUTREACH_TABLES:
            if con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]:
                continue  # populated: keep the data, named writes cope with the order
        con.execute(f'DROP TABLE IF EXISTS "{table}"')
        rebuilt.append(table)
    return rebuilt


def _widen_outreach(con) -> list[str]:
    """Add any declared outreach column the live table is missing."""
    added: list[str] = []
    types = {"attachments_json": "VARCHAR", "provenance_json": "VARCHAR", "theme": "VARCHAR",
             "rationale_json": "VARCHAR", "body_html": "VARCHAR", "body_text": "VARCHAR"}
    for table, expected in OUTREACH_TABLES.items():
        live = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=?",
            [table]).fetchall()}
        if not live:
            continue
        for column in expected:
            if column in live:
                continue
            try:
                con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" '
                            f'{types.get(column, "VARCHAR")}')
                added.append(f"{table}.{column}")
            except Exception:
                pass
    return added


def _migrate(con, previous_version: str, existing: set[str]) -> dict[str, Any]:
    """Forward-only migration. Derived tables are dropped, facts are preserved."""
    dropped: list[str] = []

    # v2 kept a wide professor_metrics and narrower campaign tables; rebuild the
    # derived one and widen the outreach ones in place.
    if previous_version in {"0", "1", "2"}:
        con.execute("DROP TABLE IF EXISTS professor_metrics")
        con.execute(PROFESSOR_METRICS_SCHEMA)
        for table, column, ddl in (
            ("campaigns", "provenance_json", "VARCHAR"),
            ("campaigns", "attachments_json", "VARCHAR"),
            ("campaigns", "theme", "VARCHAR"),
            ("campaign_recipients", "rationale_json", "VARCHAR"),
            ("campaign_messages", "body_html", "VARCHAR"),
        ):
            if table in existing:
                try:
                    con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}')
                except Exception:
                    pass
        if "campaign_messages" in existing:
            cols = {r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='campaign_messages'"
            ).fetchall()}
            if "body" in cols and "body_text" not in cols:
                con.execute('ALTER TABLE campaign_messages RENAME COLUMN body TO body_text')
        con.execute("DELETE FROM meta WHERE key IN ('current_semantic_model_id')")
    return {"migration_dropped": dropped, "migrated_from": previous_version}


def set_meta(con, key: str, value: str) -> None:
    """Upsert one `meta` row.

    Written as delete+insert rather than `INSERT OR REPLACE` so it also works on
    a store whose `meta` predates the primary key.
    """
    con.execute("DELETE FROM meta WHERE key=?", [key])
    con.execute("INSERT INTO meta VALUES (?,?,?)", [key, value, utcnow()])


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def json_load(value: Any, default: Any = None) -> Any:
    try:
        return json.loads(value) if value else (default if default is not None else {})
    except Exception:
        return default if default is not None else {}
