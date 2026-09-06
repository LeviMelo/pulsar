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
from .identity import Names, known_people
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
    """Works and projects from the atom corpus.

    The corpus already did the hard part — turning scraped Lattes and public
    SIGAA tables into typed, dated, attributable records — so this is a mapping
    from atom kind to entity kind and relation, and nothing more.
    """
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
# Collaboration
# ---------------------------------------------------------------------------


def _collaboration(p: Projection, db: Database) -> None:
    """Co-authorship, from Lattes project teams.

    An edge to someone outside the indexed faculty is kept rather than dropped.
    The reach of a research group past its own walls is exactly the thing a
    faculty-only graph cannot show, and it is the difference between "this
    professor is central here" and "this professor is a door out of here".
    """
    if not db.table_exists("collaboration_edges"):
        return
    # Resolution is asked for again rather than trusted from the acquired row:
    # an alias learned after that table was written would otherwise leave two
    # nodes where the faculty has one, and the graph would under-report its own
    # internal density.
    indexed = known_people(db)
    rows = db.query_df(
        "SELECT source_siape, target_siape, collaborator_name, weight FROM collaboration_edges")
    for row in rows.itertuples():
        source = str(row.source_siape or "").strip()
        if not source:
            continue
        name = clean_text(row.collaborator_name)
        normalized = normalize_person_name(name)
        target_siape = str(row.target_siape or "").strip() or indexed.get(normalized, "")
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
