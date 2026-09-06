"""The store, opened: records, portfolios, entities and one search over all of it.

The console used to show a ranked table of 65 people and 187 plans over a
store holding a million and a half rows about them. Everything else — where a
professor trained and under whom, the boards they sit on and with whom, the
students they are supervising this year, the journals they actually publish
in, the courses on their timetable — was acquired, archived and never served.

These payloads are the serving side of `records/` and `graph/`. Four shapes:

* a **search** over every record, with the facets needed to narrow it;
* a **portfolio**: everything on file for one person, plus the people around
  them, in a form a timeline and a filtered list can be drawn from directly;
* an **entity** page for anything the graph holds a node for — an institution,
  a venue, a co-author who is not faculty — built from that node's edges and
  from the records that name it;
* a **global search** that returns professors, records and entities together,
  so "Ladle" finds the person, "Arquivos Brasileiros" finds the journal and
  "sepse" finds the papers from one box.

Rows are compact on purpose: a portfolio can be twelve hundred records and the
browser holds all of them, so each carries what a list needs and the full
record is fetched on open.
"""

from __future__ import annotations

from typing import Any, Callable

from ..db import Database
from ..graph.model import Kind, Relation, eid, is_indexed_person
from ..records import FAMILIES
from ..records import store as records
from ..semantics.normalize import display_person_name

#: What a list row carries. The full record has the payload and every person.
_COMPACT = ("record_id", "siape", "family", "form", "title", "year", "year_end", "status",
            "org", "counterpart", "venue", "nature", "doi", "source")

#: How to read each relation from the pivot's side, for an entity page.
RELATION_LABELS: dict[tuple[str, str], str] = {
    ("authored", "in"): "Authors",
    ("authored", "out"): "Works authored",
    ("published_in", "in"): "Works published here",
    ("published_in", "out"): "Published in",
    ("worked_at", "in"): "People who worked here",
    ("worked_at", "out"): "Appointments",
    ("trained_at", "in"): "People trained here",
    ("trained_at", "out"): "Degrees from",
    ("advised_by", "in"): "Advised",
    ("advised_by", "out"): "Advised by",
    ("supervises", "in"): "Supervised by",
    ("supervises", "out"): "Supervises",
    ("examined", "in"): "Examined by",
    ("examined", "out"): "Candidates examined",
    ("served_with", "in"): "Shared a board with",
    ("served_with", "out"): "Shared a board with",
    ("collaborates_with", "in"): "Co-authors",
    ("collaborates_with", "out"): "Co-authors",
    ("affiliated_with", "in"): "Faculty affiliated",
    ("affiliated_with", "out"): "Affiliated with",
    ("part_of", "in"): "Contains",
    ("part_of", "out"): "Part of",
    ("leads", "in"): "Led by",
    ("leads", "out"): "Projects led",
    ("offers", "in"): "Offered by",
    ("offers", "out"): "Positions offered",
    ("within", "in"): "Positions within",
    ("within", "out"): "Within project",
    ("about", "in"): "About this",
    ("about", "out"): "About",
    ("uses", "in"): "Used by",
    ("uses", "out"): "Techniques",
}


class Explorer:
    def __init__(self, db: Database, display_name: Callable[[Any, Any], str]):
        self.db = db
        self._display = display_name
        self._names: dict[str, str] | None = None

    # -- names ----------------------------------------------------------------

    def _name(self, siape: Any) -> str:
        if self._names is None:
            frame = self.db.query_df("SELECT siape, canonical_name FROM professors")
            self._names = {str(r.siape): self._display(str(r.siape), r.canonical_name)
                           for r in frame.itertuples()}
        return self._names.get(str(siape), str(siape))

    def _compact(self, row: dict[str, Any]) -> dict[str, Any]:
        out = {k: row.get(k) for k in _COMPACT}
        out["subject"] = self._name(row.get("siape"))
        out["people"] = len(row.get("people") or [])
        out["keywords"] = list(row.get("keywords") or [])[:5]
        return out

    # -- records ----------------------------------------------------------------

    def search(self, query: dict[str, list[str]]) -> dict[str, Any]:
        def one(key: str, default: str = "") -> str:
            return (query.get(key) or [default])[0]

        def number(key: str) -> int | None:
            raw = one(key)
            return int(raw) if raw.strip().lstrip("-").isdigit() else None

        limit = min(max(number("limit") or 200, 1), 1000)
        offset = max(number("offset") or 0, 0)
        result = records.search(
            self.db, q=one("q"), family=one("family"), form=one("form"), siape=one("siape"),
            year_from=number("since"), year_to=number("until"), org=one("org"),
            person=one("person"), limit=limit, offset=offset)
        return {
            "rows": [self._compact(r) for r in result["rows"]],
            "total": result["total"],
            "limit": limit,
            "offset": offset,
        }

    def facets(self, siape: str = "") -> dict[str, Any]:
        out = records.facets(self.db, siape=siape)
        out["meanings"] = dict(FAMILIES)
        out["summary"] = records.summary(self.db)
        return out

    def record(self, record_id: str) -> dict[str, Any] | None:
        row = records.get(self.db, record_id)
        if row is None:
            return None
        row["subject"] = self._name(row["siape"])
        # The people named here, resolved the way the graph resolved them, so a
        # co-author who is faculty opens as faculty and a stranger as a node.
        row["people"] = [self._person_link(p) for p in row.get("people") or []]
        entity_id = None
        if row["family"] in ("work", "technical"):
            entity_id = eid(Kind.WORK, record_id)
        elif row["family"] == "project":
            entity_id = eid(Kind.PROJECT, record_id)
        row["entity_id"] = entity_id if entity_id and self._exists(entity_id) else None
        return row

    def _exists(self, entity_id: str) -> bool:
        if not self.db.table_exists("graph_entities"):
            return False
        return bool(self.db.scalar("SELECT COUNT(*) FROM graph_entities WHERE entity_id=?",
                                   [entity_id], default=0))

    def _person_link(self, person: dict[str, Any]) -> dict[str, Any]:
        """A named person with, where the graph knows them, the node to open."""
        from ..graph.identity import PersonResolver, given_first, merge_names
        from ..graph.model import person_id
        name = str(person.get("name") or "")
        out = dict(person)
        out["siape"] = None
        out["entity_id"] = None
        if not name:
            return out
        resolver = self._resolver()
        siape = resolver.resolve(name)
        if siape:
            out["siape"] = siape
            out["entity_id"] = person_id(siape=siape)
            out["display"] = self._name(siape)
            return out
        # The graph stored strangers under a merged spelling; try the given
        # form first, then whatever the merge table holds for it.
        for candidate in (given_first(name), self._merged().get(name, "")):
            if not candidate:
                continue
            try:
                entity_id = person_id(name=candidate)
            except ValueError:
                continue
            if self._exists(entity_id):
                out["entity_id"] = entity_id
                out["display"] = display_person_name(candidate) or candidate
                break
        return out

    _resolver_cache: Any = None
    _merged_cache: dict[str, str] | None = None

    def _resolver(self):
        if self._resolver_cache is None:
            from ..graph.identity import PersonResolver
            self._resolver_cache = PersonResolver(self.db)
        return self._resolver_cache

    def _merged(self) -> dict[str, str]:
        if self._merged_cache is None:
            from ..graph.identity import merge_names
            if self.db.table_exists("record_people"):
                names = [str(r.name) for r in self.db.query_df(
                    "SELECT DISTINCT name FROM record_people").itertuples()]
                self._merged_cache = merge_names(names)
            else:
                self._merged_cache = {}
        return self._merged_cache

    # -- one person -------------------------------------------------------------

    def portfolio(self, siape: str) -> dict[str, Any]:
        rows = records.for_person(self.db, siape)
        people = records.people_around(self.db, siape, limit=80)
        merged = self._merged()
        # Fold spelling variants the way the graph does, so the list says
        # "Richard James Ladle · 80" and not four rows adding up to it.
        folded: dict[str, dict[str, Any]] = {}
        for person in people:
            key = merged.get(person["name"], person["name"])
            slot = folded.get(key)
            if slot is None:
                slot = folded[key] = {**person, "name": key, "spellings": []}
            else:
                slot["count"] += person["count"]
                slot["first_year"] = _min(slot["first_year"], person["first_year"])
                slot["last_year"] = _max(slot["last_year"], person["last_year"])
                slot["families"] = sorted(set(slot["families"]) | set(person["families"]))
                slot["roles"] = sorted(set(slot["roles"]) | set(person["roles"]))
            if person["name"] != key:
                slot["spellings"].append(person["name"])
        company = sorted(folded.values(), key=lambda p: (-p["count"], p["name"]))
        for person in company:
            link = self._person_link({"name": person["name"]})
            person["siape"] = link["siape"]
            person["entity_id"] = link["entity_id"]
            person["display"] = link.get("display") or display_person_name(person["name"]) or person["name"]
        return {
            "siape": siape,
            "name": self._name(siape),
            "facets": records.facets(self.db, siape=siape),
            "records": [self._compact(r) for r in rows],
            "people": company,
            "career": [r for r in rows if r["family"] in ("career", "degree", "training", "activity")],
        }

    # -- entities -----------------------------------------------------------------

    def entity(self, entity_id: str) -> dict[str, Any] | None:
        from .. import graph as g
        node = g.entity(self.db, entity_id)
        if node is None:
            return None
        if node["kind"] == "person" and is_indexed_person(entity_id):
            node["siape"] = entity_id.split(":", 1)[1]
            node["name"] = self._name(node["siape"])
        metrics = g.metrics_for(self.db, entity_id)
        grouped: dict[str, dict[str, Any]] = {}
        for row in g.neighbours(self.db, entity_id, limit=400):
            relation = str(row["relation"])
            direction = str(row["direction"])
            label = RELATION_LABELS.get((relation, direction), relation)
            bucket = grouped.setdefault(label, {"relation": relation, "direction": direction,
                                                "label": label, "rows": []})
            other = str(row["entity_id"])
            bucket["rows"].append({
                "entity_id": other,
                "kind": row["kind"],
                "name": self._name(other.split(":", 1)[1]) if is_indexed_person(other) else row["name"],
                "indexed": is_indexed_person(other),
                "weight": float(row["weight"] or 0),
                "year": None if row["year"] is None or row["year"] != row["year"] else int(row["year"]),
            })
        groups = sorted(grouped.values(), key=lambda b: -len(b["rows"]))
        for bucket in groups:
            bucket["count"] = len(bucket["rows"])
            bucket["rows"] = bucket["rows"][:120]
        return {
            "entity": node,
            "metrics": metrics,
            "groups": groups,
            "records": self._records_naming(node),
        }

    def _records_naming(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        """Records that mention this entity, for the kinds records name directly."""
        kind = node["kind"]
        name = str(node.get("name") or "")
        if kind == "org":
            result = records.search(self.db, org=name, limit=150)
        elif kind == "venue":
            result = records.search(self.db, q=name, family="work", limit=150)
            result["rows"] = [r for r in result["rows"] if r.get("venue") == name]
        elif kind == "person" and not is_indexed_person(node["entity_id"]):
            result = records.search(self.db, person=name, limit=150)
        elif kind in ("work", "project") and (node.get("payload") or {}).get("record_id"):
            row = records.get(self.db, node["payload"]["record_id"])
            result = {"rows": [row] if row else []}
        else:
            return []
        return [self._compact(r) for r in result["rows"]]

    # -- everything at once ---------------------------------------------------------

    def search_all(self, q: str, *, limit: int = 12) -> dict[str, Any]:
        needle = (q or "").strip().lower()
        if len(needle) < 2:
            return {"professors": [], "records": [], "entities": []}
        from ..graph.identity import fold
        professors = []
        for row in self.db.query_df("SELECT siape, canonical_name, department, center FROM professors").itertuples():
            if fold(needle) and fold(needle) in fold(row.canonical_name):
                professors.append({"siape": str(row.siape), "name": self._name(row.siape),
                                   "department": row.department, "center": row.center})
        entities: list[dict[str, Any]] = []
        if self.db.table_exists("graph_entities"):
            frame = self.db.query_df(
                "SELECT entity_id, kind, name FROM graph_entities "
                "WHERE kind IN ('org', 'venue', 'person', 'project') "
                "AND lower(strip_accents(name)) LIKE ? "
                "ORDER BY CASE kind WHEN 'org' THEN 0 WHEN 'venue' THEN 1 WHEN 'person' THEN 2 ELSE 3 END, "
                "length(name) LIMIT ?", [f"%{_plain(needle)}%", limit * 2])
            for row in frame.itertuples():
                if is_indexed_person(str(row.entity_id)):
                    continue        # faculty are listed above, as professors
                entities.append({"entity_id": str(row.entity_id), "kind": str(row.kind),
                                 "name": str(row.name)})
        found = records.search(self.db, q=needle, limit=limit)
        return {
            "professors": professors[:limit],
            "records": [self._compact(r) for r in found["rows"]],
            "records_total": found["total"],
            "entities": entities[:limit],
        }


def _plain(text: str) -> str:
    """Lower-case, accents stripped, spaces kept: what `strip_accents` gives SQL."""
    import unicodedata
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _min(a: int | None, b: int | None) -> int | None:
    return b if a is None else a if b is None else min(a, b)


def _max(a: int | None, b: int | None) -> int | None:
    return b if a is None else a if b is None else max(a, b)
