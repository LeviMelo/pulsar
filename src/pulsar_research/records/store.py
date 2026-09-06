"""Writing records to the store and reading them back.

Replacement-write: a build replaces the whole table, because a record's
identity is its position in the source and a source that reorders leaves nobody
a way to say which old row a new one supersedes. The build is cheap enough
(seconds) that incremental writes would buy nothing but bugs.

The readers here are what the console and CLI call. They return plain dicts, so
the HTTP layer never sees a dataclass, and they are the only place the
`records` SQL is written.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Sequence
from uuid import uuid4

from ..db import Database, json_load, json_text, utcnow
from ..semantics.normalize import normalize_person_name
from .model import FAMILIES, Record


def fingerprint(db: Database) -> str:
    """What the extraction depends on: which CVs, at which update date, plus the
    size of the scraped tables. Changes when acquisition changes, never otherwise."""
    parts: list[str] = []
    if db.table_exists("professors"):
        frame = db.query_df(
            "SELECT siape, lattes_id, lattes_update_date, lattes_update_time FROM professors "
            "ORDER BY siape")
        for row in frame.itertuples():
            parts.append(f"{row.siape}|{row.lattes_id}|{row.lattes_update_date}|{row.lattes_update_time}")
    for table in ("sigaa_public_lattes_flat", "sigaa_public_table_rows"):
        count = db.scalar(f'SELECT COUNT(*) FROM "{table}"', default=0) if db.table_exists(table) else 0
        parts.append(f"{table}={count}")
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()


def write_records(db: Database, records: Sequence[Record], *, fp: str,
                  stats: dict[str, Any] | None = None) -> dict[str, Any]:
    now = utcnow()
    rows = []
    people = []
    for rec in records:
        rid = rec.record_id
        rows.append([
            rid, rec.siape, rec.family, rec.form, rec.title, rec.year, rec.year_end, rec.status,
            rec.org, rec.org_code, rec.counterpart, rec.counterpart_id, rec.venue, rec.doi,
            rec.language, rec.nature, json_text(rec.keywords), json_text(rec.areas),
            json_text([{"name": p.name, "cnpq_id": p.cnpq_id, "ordinal": p.ordinal, "role": p.role}
                       for p in rec.people]),
            json_text(rec.payload), rec.source, rec.source_ref, rec.search_text, now,
        ])
        for p in rec.people:
            people.append([rid, rec.siape, rec.family, p.ordinal, p.name,
                           normalize_person_name(p.name), p.cnpq_id, p.role, rec.year])
    summary = dict(stats or {})
    summary.update({
        "records": len(rows), "people": len(people),
        "families": _family_counts(records),
    })
    # DuckDB's executemany is one statement per row; a hundred thousand rows
    # through it takes minutes. A registered frame is one bulk append.
    import pandas as pd
    record_frame = pd.DataFrame(rows, columns=list(_COLUMNS) + ["search_text", "computed_at"])
    people_frame = pd.DataFrame(people, columns=[
        "record_id", "siape", "family", "ordinal", "name", "normalized_name", "cnpq_id",
        "role", "year"])
    with db.connect() as con:
        con.execute("DELETE FROM records")
        con.execute("DELETE FROM record_people")
        if len(record_frame):
            con.register("record_frame", record_frame)
            con.execute("INSERT INTO records SELECT * FROM record_frame")
            con.unregister("record_frame")
        if len(people_frame):
            con.register("people_frame", people_frame)
            con.execute("INSERT INTO record_people SELECT * FROM people_frame")
            con.unregister("people_frame")
        con.execute("INSERT INTO record_builds VALUES (?,?,?,?)",
                    [uuid4().hex[:12], fp, json_text(summary), now])
    return summary


def _family_counts(records: Iterable[Record]) -> dict[str, int]:
    out: dict[str, int] = {}
    for rec in records:
        out[rec.family] = out.get(rec.family, 0) + 1
    return out


def last_build(db: Database) -> dict[str, Any] | None:
    if not db.table_exists("record_builds"):
        return None
    frame = db.query_df("SELECT build_id, fingerprint, stats_json, created_at FROM record_builds "
                        "ORDER BY created_at DESC LIMIT 1")
    if not len(frame):
        return None
    row = frame.iloc[0].to_dict()
    row["stats"] = json_load(row.pop("stats_json"), {}) or {}
    return row


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

_COLUMNS = ("record_id", "siape", "family", "form", "title", "year", "year_end", "status",
            "org", "org_code", "counterpart", "counterpart_id", "venue", "doi", "language",
            "nature", "keywords_json", "areas_json", "people_json", "payload_json", "source",
            "source_ref")


def _row(values: Sequence[Any]) -> dict[str, Any]:
    out = dict(zip(_COLUMNS, values))
    for key in ("keywords", "areas", "people", "payload"):
        out[key] = json_load(out.pop(f"{key}_json"), [] if key != "payload" else {}) or (
            [] if key != "payload" else {})
    for key in ("year", "year_end"):
        out[key] = None if out[key] is None else int(out[key])
    return out


def _select() -> str:
    return "SELECT " + ", ".join(_COLUMNS) + " FROM records"


def search(db: Database, *, q: str = "", family: str = "", form: str = "", siape: str = "",
           year_from: int | None = None, year_to: int | None = None, org: str = "",
           person: str = "", limit: int = 200, offset: int = 0) -> dict[str, Any]:
    """Store-wide search with the filters the console exposes.

    Text matching is substring over `search_text`; it is meant to find a record
    someone half-remembers, not to rank. The ranking problem belongs to the
    semantic engine, which is a different tool for a different question.
    """
    if not db.table_exists("records"):
        return {"rows": [], "total": 0}
    where: list[str] = []
    params: list[Any] = []
    for term in (q or "").lower().split():
        where.append("search_text LIKE ?")
        params.append(f"%{term}%")
    if family:
        where.append("family=?"); params.append(family)
    if form:
        where.append("form=?"); params.append(form)
    if siape:
        where.append("siape=?"); params.append(siape)
    if year_from is not None:
        where.append("COALESCE(year_end, year) >= ?"); params.append(int(year_from))
    if year_to is not None:
        where.append("year <= ?"); params.append(int(year_to))
    if org:
        where.append("lower(org) LIKE ?"); params.append(f"%{org.lower()}%")
    if person:
        where.append("record_id IN (SELECT record_id FROM record_people WHERE normalized_name LIKE ?)")
        params.append(f"%{normalize_person_name(person)}%")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with db.connect(read_only=True) as con:
        total = con.execute(f"SELECT COUNT(*) FROM records{clause}", params).fetchone()[0]
        rows = con.execute(
            f"{_select()}{clause} ORDER BY year DESC NULLS LAST, family, title "
            f"LIMIT {int(limit)} OFFSET {int(offset)}", params).fetchall()
    return {"rows": [_row(r) for r in rows], "total": int(total)}


def get(db: Database, record_id: str) -> dict[str, Any] | None:
    with db.connect(read_only=True) as con:
        row = con.execute(f"{_select()} WHERE record_id=?", [record_id]).fetchone()
    return _row(row) if row else None


def for_person(db: Database, siape: str) -> list[dict[str, Any]]:
    """Everything on file for one person, oldest first within a family."""
    if not db.table_exists("records"):
        return []
    with db.connect(read_only=True) as con:
        rows = con.execute(f"{_select()} WHERE siape=? ORDER BY family, year NULLS LAST, title",
                           [siape]).fetchall()
    return [_row(r) for r in rows]


def facets(db: Database, *, siape: str = "") -> dict[str, Any]:
    """Counts the console needs to draw filters: per family and form, per year,
    top organisations and venues."""
    if not db.table_exists("records"):
        return {"families": [], "years": [], "orgs": [], "venues": [], "forms": []}
    where = " WHERE siape=?" if siape else ""
    params = [siape] if siape else []
    with db.connect(read_only=True) as con:
        fam = con.execute(
            f"SELECT family, COUNT(*), MIN(year), MAX(COALESCE(year_end, year)) FROM records{where} "
            "GROUP BY family ORDER BY 2 DESC", params).fetchall()
        forms = con.execute(
            f"SELECT family, form, COUNT(*) FROM records{where} GROUP BY 1, 2 ORDER BY 3 DESC",
            params).fetchall()
        years = con.execute(
            f"SELECT year, family, COUNT(*) FROM records{where + (' AND' if where else ' WHERE')} "
            "year IS NOT NULL GROUP BY 1, 2 ORDER BY 1", params).fetchall()
        orgs = con.execute(
            f"SELECT org, COUNT(*) FROM records{where + (' AND' if where else ' WHERE')} "
            "org <> '' GROUP BY 1 ORDER BY 2 DESC LIMIT 40", params).fetchall()
        venues = con.execute(
            f"SELECT venue, COUNT(*) FROM records{where + (' AND' if where else ' WHERE')} "
            "venue <> '' AND family='work' GROUP BY 1 ORDER BY 2 DESC LIMIT 40", params).fetchall()
    return {
        "families": [{"family": f, "meaning": FAMILIES.get(f, ""), "count": int(c),
                      "from": None if a is None else int(a), "to": None if b is None else int(b)}
                     for f, c, a, b in fam],
        "forms": [{"family": f, "form": fo, "count": int(c)} for f, fo, c in forms],
        "years": [{"year": int(y), "family": f, "count": int(c)} for y, f, c in years],
        "orgs": [{"org": o, "count": int(c)} for o, c in orgs],
        "venues": [{"venue": v, "count": int(c)} for v, c in venues],
    }


def people_around(db: Database, siape: str, *, limit: int = 60) -> list[dict[str, Any]]:
    """Everyone named on this person's records, by how often and in what role.

    A co-author on twelve papers, a student supervised through a master's and
    then a doctorate, a colleague met on nine examination boards: this is the
    social record, and it is what the CV alone cannot show at a glance.
    """
    if not db.table_exists("record_people"):
        return []
    # The person is named on their own papers and boards; that is not company.
    me = db.query_df("SELECT canonical_name, lattes_id FROM professors WHERE siape=?", [siape])
    my_name = normalize_person_name(me.iloc[0]["canonical_name"]) if len(me) else ""
    my_id = str(me.iloc[0]["lattes_id"] or "") if len(me) else ""
    frame = db.query_df(
        "SELECT normalized_name, MAX(name) AS name, MAX(cnpq_id) AS cnpq_id, COUNT(*) AS n, "
        "MIN(year) AS first_year, MAX(year) AS last_year, "
        "string_agg(DISTINCT family, ',') AS families, string_agg(DISTINCT role, ',') AS roles "
        "FROM record_people WHERE siape=? AND normalized_name <> '' "
        "AND normalized_name <> ? AND (cnpq_id = '' OR cnpq_id <> ?) "
        "GROUP BY normalized_name ORDER BY n DESC, name LIMIT ?",
        [siape, my_name, my_id or "-", limit])
    out = []
    for row in frame.itertuples():
        out.append({
            "name": row.name, "normalized_name": row.normalized_name, "cnpq_id": row.cnpq_id or "",
            "count": int(row.n),
            "first_year": None if row.first_year is None else int(row.first_year),
            "last_year": None if row.last_year is None else int(row.last_year),
            "families": sorted(str(row.families or "").split(",")),
            "roles": sorted(str(row.roles or "").split(",")),
        })
    return out


def summary(db: Database) -> dict[str, Any]:
    build = last_build(db)
    return {
        "built": build is not None,
        "created_at": (build or {}).get("created_at"),
        "fingerprint_current": bool(build) and build.get("fingerprint") == fingerprint(db),
        "stats": (build or {}).get("stats") or {},
    }
