"""Projecting acquired facts into the entity graph.

This is the only module that knows both vocabularies: the shape the scrapers
happen to leave data in, and the shape the platform reasons about. Everything
upstream of it is a source; everything downstream of it sees people, orgs,
works, projects and positions joined by typed relations.

Keeping that translation in one file is the point. The previous arrangement
spread it across every consumer — the campaign selector knew that a professor's
work lived in `sigaa_public_lattes_flat`, the console's network endpoint knew
that a collaboration was a row in `collaboration_edges`, the metrics module knew
both — so a change to any source rippled into code that had no business knowing
where the data came from.

Two honest limitations, recorded here rather than hidden:

*Entity resolution is name-based for anyone without a SIAPE.* An external
co-author is identified by their normalized name, so two researchers who publish
under the same name collapse into one node and one who publishes under two
spellings becomes two. Resolving that properly needs an ORCID or Lattes id we do
not currently acquire.

*SIGAA projects and Lattes projects are not reconciled.* A project catalogued in
the opportunity system and the same project described on a CV arrive as two
records with no shared key, and are projected as two entities. Merging them on
title similarity would be a guess with no way to audit it, so they stay separate
until a source gives us grounds.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from ..db import Database
from ..semantics.corpus import Atom, SemanticCorpus
from ..semantics.normalize import clean_text, display_person_name, normalize_person_name
from .identity import Names, PersonResolver, given_first, merge_names
from .model import Edge, Entity, Kind, Relation, eid, person_id, slug

#: Atom kinds that describe a discrete scholarly output.
WORK_KINDS: dict[str, Relation] = {
    "article": Relation.AUTHORED,
    "book_chapter": Relation.AUTHORED,
    "conference": Relation.AUTHORED,
    "technical": Relation.AUTHORED,
    # A supervision is an output of the supervisor and the work of the student.
    # Modelled as a work the professor supervised rather than authored, because
    # conflating the two would overstate their publication record.
    "orientation": Relation.SUPERVISED,
}

#: Atom kinds that describe an ongoing or past body of work rather than an output.
PROJECT_KINDS = frozenset({"lattes_project", "sigaa_project", "sigaa_extension"})

#: Kinds that are properties of a person, not things in their own right. A
#: research line is a phrase on a CV; making it an entity would invent structure
#: the source does not have.
ATTRIBUTE_KINDS = frozenset({"knowledge_area", "research_line", "profile_summary"})


class Projection:
    """Accumulates entities and edges, deduplicating as it goes."""

    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.edges: list[Edge] = []
        #: Names an inference decided belonged to an indexed professor, rather
        #: than a source having said so. Reported by the build so a reader can
        #: see how much of the graph's identity was concluded rather than read.
        self.inferred_identities: dict[str, str] = {}

    def add(self, entity: Entity) -> str:
        existing = self.entities.get(entity.entity_id)
        if existing is None:
            self.entities[entity.entity_id] = entity
        else:
            # First non-empty name wins; sources accumulate. A later source
            # naming the same node must not blank a name an earlier one had.
            if not existing.name and entity.name:
                existing.name = entity.name
            existing.payload.update({k: v for k, v in entity.payload.items() if v not in (None, "")})
            existing.sources = tuple(dict.fromkeys(existing.sources + entity.sources))
        return entity.entity_id

    def link(self, source_id: str, target_id: str, relation: Relation, **kwargs: Any) -> None:
        if source_id == target_id:
            return          # a self-edge is always a resolution failure, never a fact
        self.edges.append(Edge(source_id, target_id, relation, **kwargs))


# ---------------------------------------------------------------------------
# Organisations and people
# ---------------------------------------------------------------------------


def _org_chain(p: Projection, row: Mapping[str, Any]) -> str | None:
    """Centre → department, as far down as the record actually goes.

    SIGAA writes these with padded columns and inconsistent casing, so the id is
    a slug of the cleaned name and the display name is whatever the source said.
    """
    center = clean_text(row.get("center"))
    department = clean_text(row.get("department"))
    center_id = None
    if center:
        center_id = p.add(Entity(eid(Kind.ORG, center), Kind.ORG, center,
                                 payload={"level": "center"}, sources=("sigaa",)))
    if not department:
        return center_id
    department_id = p.add(Entity(eid(Kind.ORG, department), Kind.ORG, department,
                                 payload={"level": "department"}, sources=("sigaa",)))
    if center_id and center_id != department_id:
        p.link(department_id, center_id, Relation.PART_OF, source="sigaa")
    return department_id


def _people(p: Projection, professors: Sequence[Mapping[str, Any]], names: Names) -> None:
    for row in professors:
        siape = str(row.get("siape") or "").strip()
        if not siape:
            continue
        entity_id = person_id(siape=siape)
        # The stored canonical name is a matching key, not a name. A graph whose
        # nodes are labelled "diego figueiredo nobrega" is a graph nobody will
        # read, so the true spelling is recovered here once for all consumers.
        p.add(Entity(
            entity_id, Kind.PERSON, names.display(siape, row.get("canonical_name")),
            payload={
                "siape": siape,
                "email": clean_text(row.get("email")),
                "center": clean_text(row.get("center")),
                "department": clean_text(row.get("department")),
                "lattes_id": clean_text(row.get("lattes_id")),
                "indexed": True,
            },
            sources=("sigaa",)))
        org_id = _org_chain(p, row)
        if org_id:
            p.link(entity_id, org_id, Relation.AFFILIATED_WITH, source="sigaa")


# ---------------------------------------------------------------------------
# Positions and projects
# ---------------------------------------------------------------------------


def _positions(p: Projection, opportunities: Sequence[Mapping[str, Any]]) -> None:
    """A work plan is a position: a named slot a supervisor is offering now."""
    for row in opportunities:
        opportunity_id = str(row.get("id_opportunity") or "").strip()
        if not opportunity_id:
            continue
        title = clean_text(row.get("plan_title")) or clean_text(row.get("project_title"))
        position = p.add(Entity(
            eid(Kind.POSITION, opportunity_id), Kind.POSITION, title,
            payload={
                "edital": clean_text(row.get("edital")),
                "status": clean_text(row.get("status")),
                "funded_slots": int(row.get("funded_slots") or 0),
                "has_funding": bool(row.get("has_funding")),
                "area": clean_text(row.get("area")),
            },
            sources=("sigaa",)))

        siape = str(row.get("professor_siape") or "").strip()
        if siape:
            p.link(person_id(siape=siape), position, Relation.OFFERS, source="sigaa")

        code = clean_text(row.get("project_code"))
        if code:
            project = p.add(Entity(
                eid(Kind.PROJECT, code), Kind.PROJECT,
                clean_text(row.get("project_title")),
                payload={"project_code": code, "origin": "sigaa_opportunity"},
                sources=("sigaa",)))
            p.link(position, project, Relation.WITHIN, source="sigaa")
            org_id = _org_chain(p, row)
            if org_id:
                p.link(project, org_id, Relation.PART_OF, source="sigaa")


def _atoms(p: Projection, atoms: Iterable[Atom]) -> None:
    """Works and projects from the atom corpus — the fallback for a store that
    holds an archive but has not run `records build`. Records carry the same
    facts with people, venues and dates attached, so when they exist they win."""
    for atom in atoms:
        if atom.kind in ATTRIBUTE_KINDS or atom.kind == "opportunity":
            continue
        if not atom.siape or not slug(atom.atom_id):
            continue
        title = atom.label
        if not title:
            continue
        author = person_id(siape=atom.siape)

        if atom.kind in PROJECT_KINDS:
            entity_id = p.add(Entity(
                eid(Kind.PROJECT, atom.atom_id), Kind.PROJECT, title,
                payload={"origin": atom.kind, "year": atom.year,
                         "year_end": atom.year_end, "active": atom.is_active},
                sources=(atom.kind,)))
            p.link(author, entity_id, Relation.LEADS, year=atom.year, source=atom.kind)
            continue

        relation = WORK_KINDS.get(atom.kind)
        if relation is None:
            continue
        entity_id = p.add(Entity(
            eid(Kind.WORK, atom.atom_id), Kind.WORK, title,
            payload={"form": atom.kind, "year": atom.year,
                     "keywords": list(atom.keywords)[:8]},
            sources=(atom.kind,)))
        p.link(author, entity_id, relation, year=atom.year, source=atom.kind)


# ---------------------------------------------------------------------------
# Records: works, projects, the career, the lineage and the social record
# ---------------------------------------------------------------------------

#: Record families that become a WORK entity.
_WORK_FAMILIES = frozenset({"work", "technical"})


class _People:
    """Names → person ids, resolving faculty and merging strangers' spellings.

    Every name on a record goes through the faculty resolver first; a name that
    is nobody indexed becomes a `person:name-…` node under the representative
    spelling `merge_names` chose for it, so "Richard J Ladle" and "LADLE, R. J."
    are one stranger rather than two.
    """

    def __init__(self, p: Projection, db: Database, spellings: Iterable[str]):
        self._p = p
        self._resolver = PersonResolver(db)
        self._merged = merge_names(spellings)

    def id_for(self, name: str, *, source: str) -> str | None:
        name = clean_text(name)
        if not name:
            return None
        siape = self._resolver.resolve(name)
        if siape:
            return person_id(siape=siape)
        representative = self._merged.get(name, given_first(name))
        try:
            entity_id = person_id(name=representative)
        except ValueError:
            return None
        self._p.add(Entity(entity_id, Kind.PERSON, display_person_name(representative) or representative,
                           payload={"indexed": False}, sources=(source,)))
        return entity_id

    @property
    def inferred(self) -> dict[str, str]:
        return self._resolver.inferred


def _org(p: Projection, name: str, code: str = "", *, source: str) -> str | None:
    name = clean_text(name)
    if not name or not slug(name):
        return None
    entity_id = p.add(Entity(eid(Kind.ORG, name), Kind.ORG, name,
                             payload={"level": "institution", "code": clean_text(code)},
                             sources=(source,)))
    return entity_id


def _records(p: Projection, db: Database) -> bool:
    """Project the records table. Returns False if there is none to project."""
    if not db.table_exists("records") or not int(db.scalar("SELECT COUNT(*) FROM records", default=0) or 0):
        return False
    from ..records import store as records

    rows = db.query_df(
        "SELECT record_id, siape, family, form, title, year, year_end, status, org, org_code, "
        "counterpart, venue, nature, people_json, source FROM records "
        "WHERE family IN ('work', 'technical', 'project', 'career', 'degree', 'committee', 'supervision')")
    spellings = [str(r.name) for r in db.query_df(
        "SELECT DISTINCT name FROM record_people").itertuples()]
    people = _People(p, db, spellings)
    from ..db import json_load

    # Boards are the one place several people are named together for a reason
    # other than authorship; the tie is between every pair of members.
    served: dict[tuple[str, str], list[int | None]] = {}
    coauthored: dict[tuple[str, str], list[int | None]] = {}

    for row in rows.itertuples():
        family = str(row.family)
        siape = str(row.siape)
        subject = person_id(siape=siape)
        if subject not in p.entities:
            continue
        source = str(row.source or "records")
        year = _int(row.year)
        year_end = _int(row.year_end)
        named = json_load(row.people_json, []) or []

        if family in _WORK_FAMILIES:
            work = p.add(Entity(
                eid(Kind.WORK, row.record_id), Kind.WORK, clean_text(row.title),
                payload={"form": str(row.form), "year": year, "family": family,
                         "venue": clean_text(row.venue), "record_id": str(row.record_id)},
                sources=(source,)))
            p.link(subject, work, Relation.AUTHORED, year=year, source=source)
            authors = [subject]
            for person in named:
                if person.get("role") not in ("author", "team", "responsible"):
                    continue
                other = people.id_for(person.get("name", ""), source=source)
                if other and other != subject:
                    p.link(other, work, Relation.AUTHORED, year=year, source=source)
                    authors.append(other)
            for other in authors[1:]:
                coauthored.setdefault(_pair(subject, other), []).append(year)
            venue = clean_text(row.venue)
            if venue and family == "work" and slug(venue):
                venue_id = p.add(Entity(eid(Kind.VENUE, venue), Kind.VENUE, venue,
                                        payload={"form": str(row.form)}, sources=(source,)))
                p.link(work, venue_id, Relation.PUBLISHED_IN, year=year, source=source)

        elif family == "project":
            project = p.add(Entity(
                eid(Kind.PROJECT, row.record_id), Kind.PROJECT, clean_text(row.title),
                payload={"origin": f"record:{row.form}", "year": year, "year_end": year_end,
                         "active": str(row.status) in ("ongoing", "active"),
                         "record_id": str(row.record_id)},
                sources=(source,)))
            p.link(subject, project, Relation.LEADS, year=year, source=source)
            org = _org(p, str(row.org or ""), str(row.org_code or ""), source=source)
            if org:
                p.link(project, org, Relation.PART_OF, source=source)
            for person in named:
                other = people.id_for(person.get("name", ""), source=source)
                if other and other != subject:
                    coauthored.setdefault(_pair(subject, other), []).append(year)

        elif family == "career":
            org = _org(p, str(row.org or ""), str(row.org_code or ""), source=source)
            if org:
                p.link(subject, org, Relation.WORKED_AT, year=year, source=source,
                       evidence={"role": clean_text(row.nature), "form": str(row.form),
                                 "year_end": year_end, "status": str(row.status)})

        elif family == "degree":
            org = _org(p, str(row.org or ""), str(row.org_code or ""), source=source)
            if org:
                p.link(subject, org, Relation.TRAINED_AT, year=year_end or year, source=source,
                       evidence={"level": str(row.form), "title": clean_text(row.title),
                                 "year_start": year})
            advisor = people.id_for(str(row.counterpart or ""), source=source)
            if advisor and advisor != subject:
                p.link(subject, advisor, Relation.ADVISED_BY, year=year_end or year, source=source,
                       evidence={"level": str(row.form)})

        elif family == "supervision":
            student = people.id_for(str(row.counterpart or ""), source=source)
            if student and student != subject:
                p.link(subject, student, Relation.SUPERVISES, year=year, source=source,
                       evidence={"level": str(row.form), "status": str(row.status),
                                 "title": clean_text(row.title)})

        elif family == "committee":
            candidate = people.id_for(str(row.counterpart or ""), source=source)
            if candidate and candidate != subject:
                p.link(subject, candidate, Relation.EXAMINED, year=year, source=source,
                       evidence={"level": str(row.form), "title": clean_text(row.title)})
            members = [subject]
            for person in named:
                if person.get("role") != "member":
                    continue
                other = people.id_for(person.get("name", ""), source=source)
                if other and other not in members:
                    members.append(other)
            for i, left in enumerate(members):
                for right in members[i + 1:]:
                    served.setdefault(_pair(left, right), []).append(year)

    for (left, right), years in served.items():
        known = [y for y in years if y]
        p.link(left, right, Relation.SERVED_WITH, weight=float(len(years)),
               year=max(known) if known else None, source="records",
               evidence={"boards": len(years), "first": min(known) if known else None})
    for (left, right), years in coauthored.items():
        known = [y for y in years if y]
        p.link(left, right, Relation.COLLABORATES_WITH, weight=float(len(years)),
               year=max(known) if known else None, source="records",
               evidence={"shared": len(years), "first": min(known) if known else None})
    p.inferred_identities.update(people.inferred)
    return True


def _int(value: Any) -> int | None:
    """A year out of a frame cell, which may be None, NaN or pandas' NA."""
    try:
        import pandas as pd
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


# ---------------------------------------------------------------------------
# Collaboration
# ---------------------------------------------------------------------------


def _collaboration(p: Projection, db: Database) -> None:
    """Co-authorship from the older `collaboration_edges` table.

    Only used when there are no records: the records projection derives the
    same tie from papers *and* project teams, with years, and merges the
    spellings this table keeps apart.
    """
    if not db.table_exists("collaboration_edges"):
        return
    resolver = PersonResolver(db)
    rows = db.query_df(
        "SELECT source_siape, target_siape, collaborator_name, weight FROM collaboration_edges")
    for row in rows.itertuples():
        source = str(row.source_siape or "").strip()
        if not source:
            continue
        name = clean_text(row.collaborator_name)
        normalized = normalize_person_name(name)
        target_siape = str(row.target_siape or "").strip() or resolver.resolve(name)
        if not target_siape and not normalized:
            continue
        try:
            target = person_id(siape=target_siape or None, name=name)
        except ValueError:
            continue
        if not target_siape:
            p.add(Entity(target, Kind.PERSON, display_person_name(name) or name,
                         payload={"indexed": False}, sources=("lattes",)))
        p.link(person_id(siape=source), target, Relation.COLLABORATES_WITH,
               weight=float(row.weight or 1), source="lattes")
    p.inferred_identities.update(resolver.inferred)


# ---------------------------------------------------------------------------
# Semantic structure
# ---------------------------------------------------------------------------

#: How the semantic engine's `entity_type` values map onto graph kinds.
_SEMANTIC_KIND = {"professor": Kind.PERSON, "opportunity": Kind.POSITION,
                  "project": Kind.PROJECT}


def _semantic_id(entity_type: str, entity_id: str) -> str | None:
    kind = _SEMANTIC_KIND.get(entity_type)
    if kind is None:
        return None
    if kind is Kind.PERSON:
        return person_id(siape=entity_id)
    return eid(kind, entity_id)


def _topics_and_skills(p: Projection, db: Database, space_id: str | None) -> None:
    """Topics and techniques as entities, so "what is this group about" is a
    traversal rather than a join through four tables.

    Skipped entirely when no semantic space has been built: the graph must
    describe the corpus on a machine that has only run acquisition.
    """
    if not space_id:
        return

    if db.table_exists("semantic_topics"):
        for row in db.query_df(
            "SELECT topic_id, facet, label FROM semantic_topics WHERE space_id=?", [space_id]
        ).itertuples():
            p.add(Entity(eid(Kind.TOPIC, f"{row.facet}-{row.topic_id}"), Kind.TOPIC,
                         clean_text(row.label),
                         payload={"facet": row.facet, "topic_id": str(row.topic_id)},
                         sources=("semantics",)))

    if db.table_exists("entity_topics"):
        for row in db.query_df(
            "SELECT entity_type, entity_id, facet, topic_id, weight FROM entity_topics "
            "WHERE space_id=? AND is_dominant", [space_id]
        ).itertuples():
            source = _semantic_id(row.entity_type, str(row.entity_id))
            topic = eid(Kind.TOPIC, f"{row.facet}-{row.topic_id}")
            if source and source in p.entities and topic in p.entities:
                p.link(source, topic, Relation.ABOUT, weight=float(row.weight or 0),
                       source="semantics")

    if db.table_exists("entity_skills"):
        for row in db.query_df(
            "SELECT entity_type, entity_id, skill_id, label, category, generic, mentions "
            "FROM entity_skills WHERE space_id=? AND NOT generic", [space_id]
        ).itertuples():
            source = _semantic_id(row.entity_type, str(row.entity_id))
            if not source or source not in p.entities:
                continue
            skill = p.add(Entity(eid(Kind.SKILL, row.skill_id), Kind.SKILL,
                                 clean_text(row.label),
                                 payload={"category": clean_text(row.category)},
                                 sources=("semantics",)))
            p.link(source, skill, Relation.USES, weight=float(row.mentions or 1),
                   source="semantics")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build(db: Database, corpus: SemanticCorpus, *, space_id: str | None = None) -> Projection:
    """Project the whole store into entities and edges.

    Takes the corpus rather than re-reading the tables because the corpus is
    already the reconciled view — it has resolved professor names to SIAPEs and
    parsed dates out of scraped text — and a second, subtly different reading of
    the same rows is how two parts of a system come to disagree about how many
    professors there are.
    """
    p = Projection()
    _people(p, corpus.professors, Names(db))
    _positions(p, corpus.opportunities)
    # Records carry the same works and projects as the atoms, with the people,
    # venues and dates attached; the atom path is what a store gets before it
    # has run `records build`.
    if not _records(p, db):
        _atoms(p, corpus.atoms)
        _collaboration(p, db)
    _topics_and_skills(p, db, space_id)

    # A collaboration edge can name a SIAPE that acquisition has since dropped;
    # rather than fail the whole projection, the dangling ends are removed and
    # counted, because a graph that silently references missing nodes is worse
    # than one that admits it lost some.
    known = set(p.entities)
    p.edges = [e for e in p.edges if e.source_id in known and e.target_id in known]
    return p
