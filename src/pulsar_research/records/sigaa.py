"""Records out of the public SIGAA tables.

The scraper archives every table on a professor's public pages as rows of cell
texts, without knowing what any table means. Four of those pages carry facts the
CV does not: the courses taught this term (`disciplinas`), the projects
registered with the university rather than with CNPq (`pesquisa`, `extensao`),
and the mentoring programmes coordinated (`monitoria`).

Their layout is the same everywhere: a header row, then a one-cell row naming a
year or term, then the rows under it. So a reader is a small state machine over
`(cell_texts)` rows in page order, and each page type has one.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Iterator

from ..semantics.normalize import clean_text
from .model import Record

SOURCE = "sigaa.professors"

_TERM = re.compile(r"^(?:19|20)\d{2}(?:\.\d)?$")
_HOURS = re.compile(r"^\d+\s*h$", re.I)
_COORD = re.compile(r"\s*Coordenador\(a\):\s*(.+)$", re.I)


def _cells(cell_texts_json: Any) -> list[str]:
    try:
        raw = json.loads(cell_texts_json or "[]")
    except (TypeError, ValueError):
        return []
    return [clean_text(c) for c in raw] if isinstance(raw, list) else []


def _year(term: str) -> int | None:
    m = re.match(r"(?:19|20)\d{2}", term or "")
    return int(m.group(0)) if m else None


def _courses(siape: str, url: str, rows: list[list[str]]) -> Iterator[Record]:
    """Only the latest term on the page is "current"; the rest is history."""
    terms = [c[0] for c in rows if len(c) == 1 and _TERM.match(c[0])]
    latest = max(terms) if terms else ""
    term = ""
    for i, cells in enumerate(rows):
        if len(cells) == 1 and _TERM.match(cells[0]):
            term = cells[0]
            continue
        if len(cells) < 3 or cells[0].lower().startswith("disciplina"):
            continue
        code, name = cells[0], cells[1]
        hours = next((c for c in cells[2:] if _HOURS.match(c)), "")
        schedule = cells[-1] if len(cells) >= 4 else ""
        if not name:
            continue
        yield Record(
            siape=siape, family="course", form="teaching", title=name,
            year=_year(term), status="current" if term and term == latest else "past",
            source=SOURCE, source_ref=f"{url}#{term}:{code}:{i}",
            payload={"code": code, "term": term, "hours": hours, "schedule": schedule},
        )


def _projects(siape: str, url: str, rows: list[list[str]], form: str) -> Iterator[Record]:
    year = None
    for i, cells in enumerate(rows):
        if len(cells) == 1 and _TERM.match(cells[0]):
            year = _year(cells[0])
            continue
        if len(cells) < 2:
            continue
        head = cells[0].lower()
        if head.startswith(("projeto", "código", "codigo", "título", "titulo")):
            continue
        code, title = cells[0], cells[1]
        if not title:
            continue
        area = cells[2] if len(cells) > 2 else ""
        yield Record(
            siape=siape, family="project", form=form, title=title, year=year or _year(code[-4:]),
            areas=[area] if area and area.lower() != "detalhes" else [],
            source=SOURCE, source_ref=f"{url}#{code}:{i}",
            payload={"code": code, "registry": "sigaa"},
        )


def _mentoring(siape: str, url: str, rows: list[list[str]]) -> Iterator[Record]:
    year = None
    for i, cells in enumerate(rows):
        if len(cells) == 1 and _TERM.match(cells[0]):
            year = _year(cells[0])
            continue
        if not cells or cells[0].lower().startswith(("título", "titulo")):
            continue
        title = cells[0]
        coordinator = ""
        m = _COORD.search(title)
        if m:
            coordinator = m.group(1).strip()
            title = title[:m.start()].strip()
        if not title:
            continue
        yield Record(
            siape=siape, family="project", form="mentoring", title=title, year=year,
            org=cells[1] if len(cells) > 1 else "", counterpart=coordinator,
            source=SOURCE, source_ref=f"{url}#{i}",
            payload={"registry": "sigaa", "coordinator": coordinator},
        )


_READERS = {
    "disciplinas": lambda s, u, r: _courses(s, u, r),
    "pesquisa": lambda s, u, r: _projects(s, u, r, "research"),
    "extensao": lambda s, u, r: _projects(s, u, r, "extension"),
    "monitoria": lambda s, u, r: _mentoring(s, u, r),
}


def sigaa_records(rows: Iterable[tuple[str, str, str, int, int, str]]) -> list[Record]:
    """Records from `(siape, page_type, url, table_index, row_index, cell_texts_json)`
    rows, which must arrive grouped by page and ordered by row."""
    out: list[Record] = []
    current: tuple[str, str, str] | None = None
    buffer: list[list[str]] = []

    def flush() -> None:
        if current is None or not buffer:
            return
        siape, page_type, url = current
        reader = _READERS.get(page_type)
        if reader:
            out.extend(reader(siape, url, buffer))

    for siape, page_type, url, table_index, row_index, cell_texts_json in rows:
        key = (str(siape), str(page_type), str(url))
        if key != current:
            flush()
            current, buffer = key, []
        buffer.append(_cells(cell_texts_json))
    flush()
    return out
