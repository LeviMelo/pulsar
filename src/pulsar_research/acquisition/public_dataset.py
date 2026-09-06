from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Sequence

from ..db import Database, utcnow
from .ledger import normalize_name, resolve_opportunity_professors

PUBLIC_TABLES = [
    "input_professors", "input_matches", "resolution_candidates", "professors",
    "fetches", "pages", "fields", "table_rows", "links", "images", "forms",
    "text_nodes", "lattes_documents", "lattes_flat", "errors",
]


def _extract_summary(curriculo_json: str) -> str:
    if not curriculo_json:
        return ""
    try:
        obj = json.loads(curriculo_json)
        return (
            obj.get("dadosgerais", {})
               .get("resumocv", {})
               .get("textoresumocvrh", "")
            or ""
        )
    except Exception:
        return ""


def import_public_dataset(db: Database, dataset_dir: Path,
                          only: Sequence[str] | None = None) -> dict[str, int]:
    """Copy the scraper's standalone DuckDB into the canonical store.

    With `only`, just those archive tables are re-read and the professor
    registry is left alone — that is how the embedded Lattes source refreshes
    its tables from disk without pretending a crawl happened.
    """
    src_db = dataset_dir / "sigaa_ufal.duckdb"
    if not src_db.exists():
        raise FileNotFoundError(f"Public SIGAA dataset DB not found: {src_db}")
    tables = list(only) if only else PUBLIC_TABLES
    unknown = set(tables) - set(PUBLIC_TABLES)
    if unknown:
        raise ValueError(f"not archive tables: {sorted(unknown)}")
    stats: dict[str, int] = {}
    now = utcnow()
    with db.connect() as con:
        safe = str(src_db.resolve()).replace("'", "''")
        con.execute(f"ATTACH '{safe}' AS pub (READ_ONLY)")
        try:
            for table in tables:
                target = f"sigaa_public_{table}"
                con.execute(f'DROP TABLE IF EXISTS "{target}"')
                con.execute(f'CREATE TABLE "{target}" AS SELECT * FROM pub."{table}"')
                stats[target] = int(con.execute(f'SELECT COUNT(*) FROM "{target}"').fetchone()[0])
            if only:
                return stats

            prof_rows = con.execute(
                "SELECT siape,name,department,status,profile_url,photo_url,lattes_id,lattes_update_date,lattes_update_time,professor_json_path FROM pub.professors"
            ).fetchall()
            for row in prof_rows:
                siape, name, department, status, profile_url, photo_url, lattes_id, upd_d, upd_t, json_path = row
                email = ""
                phone = ""
                fields = con.execute(
                    "SELECT key,value FROM pub.fields WHERE siape=? AND page_type='perfil' AND kind='definition'",
                    [siape],
                ).fetchall()
                for key, value in fields:
                    lk = (key or "").lower()
                    if "eletr" in lk and "endere" in lk:
                        m = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", value or "", re.I)
                        if m:
                            email = m.group(0)
                    if "telefone" in lk or "ramal" in lk:
                        phone = value or phone
                lrow = con.execute(
                    "SELECT curriculo_json FROM pub.lattes_documents WHERE siape=? LIMIT 1",
                    [siape],
                ).fetchone()
                summary = _extract_summary(lrow[0] if lrow else "")
                con.execute(
                    """
                    INSERT OR REPLACE INTO professors VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    [
                        siape, name, department, department, "", email, phone,
                        profile_url, photo_url, lattes_id, upd_d, upd_t, summary,
                        now, json_path,
                    ],
                )

            con.execute("DELETE FROM professor_aliases WHERE source IN ('sigaa_public_resolution','sigaa_public_canonical')")
            match_rows = con.execute(
                "SELECT input_name,siape,match_score FROM pub.input_matches WHERE COALESCE(siape,'')<>''"
            ).fetchall()
            for alias, siape, score in match_rows:
                con.execute(
                    "INSERT INTO professor_aliases VALUES (?, ?, ?, 'sigaa_public_resolution', ?, ?)",
                    [alias, normalize_name(alias), siape, score, now],
                )
            for siape, name, *_ in prof_rows:
                con.execute(
                    "INSERT INTO professor_aliases VALUES (?, ?, ?, 'sigaa_public_canonical', 1.0, ?)",
                    [name or '', normalize_name(name or ''), siape, now],
                )
        finally:
            con.execute("DETACH pub")
    stats["resolved_opportunities"] = resolve_opportunity_professors(db)
    with db.connect() as con:
        # Prefer current opportunity metadata for center/unit when available.
        rows = con.execute("SELECT siape FROM professors").fetchall()
        for (siape,) in rows:
            hit = con.execute("SELECT unit,center FROM opportunities WHERE professor_siape=? AND (COALESCE(unit,'')<>'' OR COALESCE(center,'')<>'') LIMIT 1", [siape]).fetchone()
            if hit:
                con.execute("UPDATE professors SET unit=COALESCE(NULLIF(?,''),unit), center=COALESCE(NULLIF(?,''),center) WHERE siape=?", [hit[0] or '', hit[1] or '', siape])
    return stats
