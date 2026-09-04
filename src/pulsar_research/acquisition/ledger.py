"""The opportunity ledger: a crash-safe JSON/CSV journal of authenticated sync.

SIGAA automation is fragile and long-running. Rewriting DuckDB after every row
would be slow and would leave a half-written database if Playwright dies
mid-crawl, so the browser loops checkpoint into a flat ledger file and DuckDB is
materialized at operation boundaries. The ledger is *provenance*, not a second
source of truth: `persist_ledger` always makes DuckDB authoritative afterwards.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from charset_normalizer import from_bytes as charset_from_bytes

from ..db import Database, utcnow
from ..semantics.normalize import normalize_person_name as normalize_name


def parse_slots(value: Any) -> int:
    """First integer in a SIGAA vacancy string ("2 vaga(s)" -> 2)."""
    if value is None:
        return 0
    match = re.search(r"(\d+)", str(value))
    return int(match.group(1)) if match else 0


def read_csv_robust(path: Path) -> list[dict[str, str]]:
    """Read a CSV whose encoding and delimiter are both unknown.

    SIGAA exports and hand-edited spreadsheets arrive as UTF-8, UTF-8-BOM or
    Windows-1252, with commas or semicolons. Guessing badly corrupts Portuguese
    names, so try in order of likelihood and sniff the dialect.
    """
    raw = path.read_bytes()
    encodings = ["utf-8-sig", "utf-8"]
    best = charset_from_bytes(raw).best()
    if best and best.encoding:
        encodings.append(best.encoding)
    encodings += ["cp1252", "latin-1"]
    text = None
    for enc in dict.fromkeys(encodings):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    try:
        delimiter = csv.Sniffer().sniff(text[:65536], delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    return [dict(row) for row in csv.DictReader(text.splitlines(), delimiter=delimiter)]


def import_ledger_file(db: Database, path: Path) -> int:
    """Bootstrap DuckDB from a ledger JSON file produced by an earlier sync."""
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as fh:
        ledger = json.load(fh)
    now = utcnow()
    with db.connect() as con:
        con.execute("DELETE FROM professor_aliases WHERE source='opportunity_ledger'")
        for key, item in ledger.items():
            op_id = str(item.get("id_oportunidade") or key or "").strip()
            if not op_id:
                continue
            project_code = str(item.get("codigo_projeto") or "").strip()
            project_title = str(item.get("titulo_projeto") or "").strip()
            slots = parse_slots(item.get("vagas"))
            if project_code:
                con.execute(
                    "INSERT OR REPLACE INTO projects VALUES (?, ?, ?, "
                    "COALESCE((SELECT first_seen_at FROM projects WHERE project_code=?), ?), ?)",
                    [project_code, project_title,
                     item.get("area") or item.get("grande_area") or "", project_code, now, now],
                )
            con.execute(
                "INSERT OR REPLACE INTO opportunities VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    op_id, project_code, project_title, item.get("titulo_plano") or "", None,
                    item.get("orientador") or "", item.get("vagas") or "", slots, slots > 0,
                    "sigaa_vagas_text", item.get("unidade") or "", item.get("departamento") or "",
                    item.get("centro_filtro") or item.get("centro") or "",
                    item.get("grande_area") or "", item.get("area") or "", item.get("edital") or "",
                    item.get("cota") or "", item.get("status") or "pending", None, None,
                    bool(item.get("detalhes_coletados")),
                    item.get("introducao_justificativa") or "", item.get("objetivos") or "",
                    item.get("metodologia") or "", item.get("habilidades_adquiridas") or "",
                    item.get("referencias") or "", now, now, item.get("applied_at") or "",
                ],
            )
            status = item.get("status") or ""
            if status in {"applied", "already_subscribed"} or item.get("applied_at"):
                con.execute("INSERT OR REPLACE INTO applications VALUES (?, ?, ?, ?, ?)",
                            [op_id, status, status, item.get("applied_at") or "", now])
            name = (item.get("orientador") or "").strip()
            if name:
                con.execute(
                    "INSERT INTO professor_aliases VALUES (?, ?, NULL, 'opportunity_ledger', NULL, ?)",
                    [name, normalize_name(name), now],
                )
    return len(ledger)


def resolve_opportunity_professors(db: Database) -> int:
    """Attach SIAPEs to opportunities using aliases resolved by the public scraper.

    Authenticated SIGAA only gives a supervisor *name*; the public faculty search
    gives the SIAPE. Names are aliases, never identities, so the join goes through
    `professor_aliases` and keeps its match score.
    """
    with db.connect() as con:
        rows = con.execute(
            "SELECT id_opportunity, professor_name FROM opportunities WHERE COALESCE(professor_siape,'')=''"
        ).fetchall()
        changed = 0
        for op_id, name in rows:
            norm = normalize_name(name or "")
            if not norm:
                continue
            hit = con.execute(
                "SELECT siape FROM professor_aliases WHERE normalized_alias=? "
                "AND COALESCE(siape,'')<>'' ORDER BY match_score DESC NULLS LAST LIMIT 1",
                [norm],
            ).fetchone()
            if hit:
                con.execute("UPDATE opportunities SET professor_siape=? WHERE id_opportunity=?",
                            [hit[0], op_id])
                changed += 1
        return changed


PROFESSOR_SEED_FIELDS = [
    "orientador", "departamento", "unidade", "centro", "total_planos",
    "total_vagas_remuneradas", "areas_cnpq", "codigos_projetos", "editais",
    "ids_oportunidades", "titulos_planos",
]


def export_professors_csv_for_scraper(db: Database, path: Path) -> int:
    """Regenerate the seed CSV the public SIGAA resolver expects."""
    with db.connect(read_only=True) as con:
        rows = con.execute(
            """
            SELECT professor_name orientador,
                   string_agg(DISTINCT department, '; ') departamento,
                   string_agg(DISTINCT unit, '; ') unidade,
                   string_agg(DISTINCT center, '; ') centro,
                   COUNT(*) total_planos,
                   COALESCE(SUM(funded_slots),0) total_vagas_remuneradas,
                   string_agg(DISTINCT area, '; ') areas_cnpq,
                   string_agg(DISTINCT project_code, '; ') codigos_projetos,
                   string_agg(DISTINCT edital, '; ') editais,
                   string_agg(id_opportunity, '; ') ids_oportunidades,
                   string_agg(plan_title, ' | ') titulos_planos
            FROM opportunities
            WHERE COALESCE(professor_name,'')<>''
            GROUP BY professor_name
            ORDER BY professor_name
            """
        ).fetchdf().to_dict("records")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PROFESSOR_SEED_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
