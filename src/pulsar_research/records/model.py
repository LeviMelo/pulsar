"""A record: one dated, attributable fact about a person's academic life.

The store holds 1.2 million flattened Lattes leaves and sixty thousand scraped
table cells, and until now the only things read out of them were titles for the
semantic corpus. A professor's career appointments, the degrees they hold and
who supervised them, the thesis committees they have sat on and with whom, the
students they are supervising by name, the venues they publish in, the courses
they teach this term — all of it was on disk and none of it was in the system.

A record is the unit that fixes that. It is deliberately one shape for every
family, because the questions people ask cut across families: *everything this
person did in 2023*, *everyone this person has shared a committee with*, *every
record mentioning "microbioma"*. One table, one search, one timeline. What is
specific to a family goes in `payload`; what is common — who, what, when, where,
with whom — is a column.

`family` is a closed set. A new family is a new extractor and a new entry here,
never a free string, because a family nobody declared is a family nobody can
filter by.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


#: What kind of fact a record is. The comment is what the console shows.
FAMILIES: dict[str, str] = {
    "work": "Publications: articles, conference papers, books and chapters",
    "technical": "Technical production: patents, software, reports, talks, events organised",
    "project": "Research and extension projects",
    "line": "Research lines declared on the CV",
    "career": "Appointments: where the person has worked, and as what",
    "activity": "Duties within an appointment: teaching, administration, committees, consultancy",
    "degree": "Degrees held, with institution, course, dates and advisor",
    "training": "Short courses and complementary training",
    "committee": "Examination boards sat on: theses, dissertations, qualifying exams, hiring",
    "supervision": "Students supervised, ongoing and concluded, by name",
    "event": "Conferences and meetings attended",
    "award": "Prizes and titles",
    "course": "Courses taught, by term, from the public SIGAA timetable",
    "language": "Languages and proficiency",
    "area": "Declared areas of expertise",
}


@dataclass(slots=True)
class Person:
    """Someone named on a record who is not its subject: a co-author, a
    committee member, a project team member, a student, an advisor."""

    name: str
    cnpq_id: str = ""
    ordinal: int = 0
    role: str = ""       #: "author", "member", "team", "responsible", "student", "advisor"


@dataclass(slots=True)
class Record:
    siape: str
    family: str
    form: str = ""               #: the family-specific kind: "article", "doutorado", "mestrado"…
    title: str = ""
    year: int | None = None
    year_end: int | None = None
    status: str = ""             #: "ongoing", "concluded", "active", "in progress"…
    org: str = ""                #: institution, employer, granting body, publisher
    org_code: str = ""
    counterpart: str = ""        #: the one other person the record is about: student, advisor, candidate
    counterpart_id: str = ""
    venue: str = ""              #: journal, event, book, series
    doi: str = ""
    language: str = ""
    nature: str = ""             #: the source's own qualifier: COMPLETO, Orientador principal…
    keywords: list[str] = field(default_factory=list)
    areas: list[str] = field(default_factory=list)
    people: list[Person] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""             #: the source id that produced it
    source_ref: str = ""         #: where in that source: a Lattes path prefix, a page URL

    def __post_init__(self) -> None:
        if self.family not in FAMILIES:
            raise ValueError(f"unknown record family {self.family!r}")

    @property
    def record_id(self) -> str:
        """Stable across builds as long as the source keeps the record where it was."""
        key = f"{self.siape}|{self.family}|{self.source}|{self.source_ref}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    @property
    def search_text(self) -> str:
        """Everything a text search should hit, lower-cased once at write time."""
        parts = [self.title, self.venue, self.org, self.counterpart, self.form, self.nature,
                 " ".join(self.keywords), " ".join(self.areas),
                 " ".join(p.name for p in self.people)]
        return " ".join(part for part in parts if part).lower()
