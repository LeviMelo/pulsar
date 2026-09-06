"""The archive, read: typed records out of the raw acquisition tables.

    from pulsar_research.records import build, store

`build(db)` extracts every record the store can support from the Lattes object
and the public SIGAA tables, and writes them as one table. `store` holds the
readers: search, one person's portfolio, facets, and who appears around whom.

This sits between acquisition and everything else. The corpus, the graph and
the console all read records rather than parsing paths; a new family is a new
spec in `lattes.py` and one line in `model.FAMILIES`, and it shows up in the
search, the timeline and the counts without anyone touching them.
"""

from __future__ import annotations

from typing import Any

from ..db import Database
from . import store
from .lattes import attach_employers, lattes_records
from .model import FAMILIES, Person, Record
from .sigaa import sigaa_records


def extract(db: Database) -> list[Record]:
    """Every record the store supports, from whatever archive tables exist."""
    records: list[Record] = []
    if db.table_exists("sigaa_public_lattes_flat"):
        with db.connect(read_only=True) as con:
            rows = con.execute(
                "SELECT siape, path, value_text FROM sigaa_public_lattes_flat "
                "WHERE value_type IN ('string', 'number', 'boolean')").fetchall()
        records.extend(lattes_records(rows))
        attach_employers(records)
    if db.table_exists("sigaa_public_table_rows"):
        with db.connect(read_only=True) as con:
            rows = con.execute(
                "SELECT siape, page_type, url, table_index, row_index, cell_texts_json "
                "FROM sigaa_public_table_rows "
                "WHERE page_type IN ('disciplinas', 'pesquisa', 'extensao', 'monitoria') "
                "ORDER BY siape, page_type, url, table_index, row_index").fetchall()
        records.extend(sigaa_records(rows))
    return records


def build(db: Database) -> dict[str, Any]:
    """Extract and write, journalling the fingerprint the build was made from."""
    fp = store.fingerprint(db)
    records = extract(db)
    return store.write_records(db, records, fp=fp)


__all__ = ["FAMILIES", "Person", "Record", "build", "extract", "store"]
