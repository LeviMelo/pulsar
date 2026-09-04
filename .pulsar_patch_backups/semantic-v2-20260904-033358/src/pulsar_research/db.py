from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import duckdb


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


SCHEMA_SQL = r"""
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

CREATE TABLE IF NOT EXISTS analysis_documents (
    entity_type VARCHAR,
    entity_id VARCHAR,
    document_text VARCHAR,
    document_hash VARCHAR,
    built_at VARCHAR
);

CREATE TABLE IF NOT EXISTS analysis_scores (
    entity_type VARCHAR,
    entity_id VARCHAR,
    tfidf_similarity DOUBLE,
    lsa_similarity DOUBLE,
    bm25_similarity DOUBLE,
    combined_score DOUBLE,
    cluster_id BIGINT,
    x DOUBLE,
    y DOUBLE,
    analyzed_at VARCHAR
);

CREATE TABLE IF NOT EXISTS analysis_topics (
    topic_id BIGINT,
    label VARCHAR,
    top_terms_json VARCHAR,
    analyzed_at VARCHAR
);

CREATE TABLE IF NOT EXISTS entity_topics (
    entity_type VARCHAR,
    entity_id VARCHAR,
    topic_id BIGINT,
    weight DOUBLE,
    analyzed_at VARCHAR
);

CREATE TABLE IF NOT EXISTS professor_metrics (
    siape VARCHAR PRIMARY KEY,
    opportunity_count BIGINT,
    funded_opportunity_count BIGINT,
    funded_slots BIGINT,
    current_year_public_projects BIGINT,
    public_project_count BIGINT,
    lattes_project_count BIGINT,
    publication_count BIGINT,
    funding_agency_count BIGINT,
    collaborator_count BIGINT,
    lattes_leaf_count BIGINT,
    research_area_count BIGINT,
    semantic_fit DOUBLE,
    calculated_at VARCHAR
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

CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id VARCHAR PRIMARY KEY,
    name VARCHAR,
    audience_query_json VARCHAR,
    subject_template VARCHAR,
    body_template VARCHAR,
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
    selection_score DOUBLE,
    selected BOOLEAN,
    created_at VARCHAR
);

CREATE TABLE IF NOT EXISTS campaign_messages (
    campaign_id VARCHAR,
    siape VARCHAR,
    subject VARCHAR,
    body VARCHAR,
    is_customized BOOLEAN,
    status VARCHAR,
    rendered_at VARCHAR,
    sent_at VARCHAR,
    provider_message_id VARCHAR,
    error VARCHAR
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

    def initialize(self) -> None:
        with self.connect() as con:
            con.execute(SCHEMA_SQL)
            con.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', '1', ?)", [utcnow()])
            con.execute("CHECKPOINT")

    def query_df(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None):
        with self.connect(read_only=True) as con:
            return con.execute(sql, params or []).df()

    def execute(self, sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> None:
        with self.connect() as con:
            con.execute(sql, params or [])

    def table_exists(self, table: str) -> bool:
        with self.connect(read_only=True) as con:
            return bool(con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name=?", [table]).fetchone()[0])

    def counts(self) -> dict[str, int]:
        tables = ["professors", "projects", "opportunities", "applications", "analysis_scores", "campaigns"]
        out: dict[str, int] = {}
        with self.connect(read_only=True) as con:
            for table in tables:
                try:
                    out[table] = int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
                except Exception:
                    out[table] = 0
        return out


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
