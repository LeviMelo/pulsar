"""The vocabulary: what an entity is, and what one entity can be to another.

PULSAR began as an outreach tool, and its store still shows it: people live in
`professors` keyed by SIAPE, offered positions in `opportunities` keyed by
`id_opportunity`, projects in `projects` keyed by `project_code`. Three key
spaces, so every query downstream had to know which kind of thing it was holding
before it could join anything to it. The derived tables already pretended
otherwise — `entity_scores`, `entity_geometry`, `entity_topics` and
`entity_skills` are all `(entity_type, entity_id)` — but there was no registry
for them to point at and no relation store at all. The only edge in the system
was `collaboration_edges(source_siape, target_siape)`: one relation, hardcoded
to one kind of entity.

That is the shape of an application, not of a platform. An academic network is
not professors-with-attributes; it is people, the organisations they sit in, the
work they produce, the projects that work belongs to and the positions those
projects offer — and, above all, the *relations* between them, because the
relations are where the intelligence is. Who bridges two units, whose
co-authorship reaches outside the faculty, which position sits inside a project
whose team already knows you: none of those are answerable from attributes.

So this module declares one id space and one relation vocabulary, and everything
else in `graph/` is built on them.

Both registries are deliberately closed sets rather than free strings. A typo in
a relation name is otherwise indistinguishable from a new kind of relation, and
the difference only surfaces months later as an edge nobody can explain.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class Kind(str, Enum):
    """What sort of thing an entity is.

    Deliberately small. A kind earns its place by being something you would
    want to rank, relate or draw on a map — not by being a column that exists.
    """

    PERSON = "person"       #: a researcher, indexed faculty or external co-author
    ORG = "org"             #: a centre, unit or department
    WORK = "work"           #: an article, chapter, conference paper, technical output
    PROJECT = "project"     #: a research or extension project
    POSITION = "position"   #: an offered supervision slot — a work plan
    TOPIC = "topic"         #: a factor of the semantic space
    SKILL = "skill"         #: an extracted technique


class Relation(str, Enum):
    """What one entity is to another.

    Direction is meaningful for all of these except `COLLABORATES_WITH`, which
    is stored once in canonical id order and expanded in both directions on
    read — see `graph.store`. Storing a symmetric relation twice is how
    degree counts quietly double.
    """

    AFFILIATED_WITH = "affiliated_with"      #: person → org
    PART_OF = "part_of"                      #: org → org, project → org
    AUTHORED = "authored"                    #: person → work
    SUPERVISED = "supervised"                #: person → work (an orientation)
    LEADS = "leads"                          #: person → project
    OFFERS = "offers"                        #: person → position
    WITHIN = "within"                        #: position → project
    COLLABORATES_WITH = "collaborates_with"  #: person ↔ person (symmetric)
    ABOUT = "about"                          #: anything → topic
    USES = "uses"                            #: anything → skill


#: Relations whose direction carries no information.
SYMMETRIC = frozenset({Relation.COLLABORATES_WITH})

#: What each relation is allowed to join, so a projection bug is a loud failure
#: rather than an edge that merely looks odd on a drawing three screens later.
ENDPOINTS: dict[Relation, tuple[frozenset[Kind], frozenset[Kind]]] = {
    Relation.AFFILIATED_WITH: (frozenset({Kind.PERSON}), frozenset({Kind.ORG})),
    Relation.PART_OF: (frozenset({Kind.ORG, Kind.PROJECT}), frozenset({Kind.ORG})),
    Relation.AUTHORED: (frozenset({Kind.PERSON}), frozenset({Kind.WORK})),
    Relation.SUPERVISED: (frozenset({Kind.PERSON}), frozenset({Kind.WORK})),
    Relation.LEADS: (frozenset({Kind.PERSON}), frozenset({Kind.PROJECT})),
    Relation.OFFERS: (frozenset({Kind.PERSON}), frozenset({Kind.POSITION})),
    Relation.WITHIN: (frozenset({Kind.POSITION}), frozenset({Kind.PROJECT})),
    Relation.COLLABORATES_WITH: (frozenset({Kind.PERSON}), frozenset({Kind.PERSON})),
    Relation.ABOUT: (frozenset(Kind), frozenset({Kind.TOPIC})),
    Relation.USES: (frozenset(Kind), frozenset({Kind.SKILL})),
}


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slug(value: object) -> str:
    """A stable, ASCII, lowercase key fragment.

    Accents are folded rather than dropped: "Nóbrega" and "Nobrega" are the same
    person written twice, and an id space that disagrees with itself about that
    produces two nodes where the network has one.
    """
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _SLUG_STRIP.sub("-", text.lower()).strip("-")


def eid(kind: Kind, key: object) -> str:
    """Mint an entity id. `person:1157495`, `org:icbs`, `position:12345`."""
    cleaned = slug(key)
    if not cleaned:
        raise ValueError(f"cannot mint a {kind.value} id from {key!r}")
    return f"{kind.value}:{cleaned}"


def kind_of(entity_id: str) -> Kind:
    """The kind encoded in an id, so a caller never has to carry it alongside."""
    head = str(entity_id).split(":", 1)[0]
    return Kind(head)


#: External co-authors have no SIAPE and no registry number anywhere we can
#: reach, so their identity is their name. That is genuinely weak — two people
#: who publish under the same name become one node, and one person who publishes
#: under two spellings becomes two. It is recorded here rather than hidden
#: because the alternative is pretending the ambiguity does not exist.
def person_id(*, siape: object = None, name: object = None) -> str:
    if siape not in (None, "", "None"):
        return eid(Kind.PERSON, siape)
    if not slug(name):
        raise ValueError("a person needs either a SIAPE or a name")
    return f"{Kind.PERSON.value}:name-{slug(name)}"


def is_indexed_person(entity_id: str) -> bool:
    """True for faculty we hold a full record for, false for a name-only node."""
    return entity_id.startswith("person:") and not entity_id.startswith("person:name-")


@dataclass(slots=True)
class Entity:
    """A node. `payload` is kind-specific and never queried structurally."""

    entity_id: str
    kind: Kind
    name: str
    key: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.key:
            self.key = self.entity_id.split(":", 1)[1]


@dataclass(slots=True)
class Edge:
    """A relation, with the provenance that makes it auditable.

    `weight` means whatever the relation means — co-authored papers, mentions,
    topic share — and is never comparable across relations. `source` names the
    acquisition that produced it, so an edge can be traced back to a page.
    """

    source_id: str
    target_id: str
    relation: Relation
    weight: float = 1.0
    source: str = ""
    year: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.relation in SYMMETRIC and self.source_id > self.target_id:
            self.source_id, self.target_id = self.target_id, self.source_id

    @property
    def directed(self) -> bool:
        return self.relation not in SYMMETRIC

    def validate(self, kinds: Mapping[str, Kind]) -> None:
        """Raise if this edge joins two things the relation does not join."""
        allowed_source, allowed_target = ENDPOINTS[self.relation]
        source_kind = kinds.get(self.source_id) or kind_of(self.source_id)
        target_kind = kinds.get(self.target_id) or kind_of(self.target_id)
        if source_kind not in allowed_source or target_kind not in allowed_target:
            raise ValueError(
                f"{self.relation.value} cannot join {source_kind.value} → {target_kind.value} "
                f"({self.source_id} → {self.target_id})")
