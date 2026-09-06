"""Who is who: resolving names to entities, and entities back to real names.

Identity is the first thing a network platform has to get right and the last
thing anyone remembers to put somewhere sensible. In PULSAR it had ended up
inside the email app — an `outreach/nomes.py` owned the only function that could
recover a professor's true spelling, and the console's serving layer imported it
from there. A page rendering a ranking was reaching into the outreach package to
find out what to call someone.

It belongs here, next to the graph, because every one of these questions is
about entity identity rather than about writing to anybody:

* `professors.canonical_name` is a *matching key* — lowercased, accents
  stripped — so displaying it addresses people as "diego figueiredo nobrega".
  The real spelling has to be recovered from a source that kept it.
* An external co-author is a name and nothing else, so resolving one to an
  indexed person, when they are one, is what decides whether the graph has an
  internal edge or a dangling leaf.

Both were already being done, in two places, by two packages, with two slightly
different name-folding rules.
"""

from __future__ import annotations

import unicodedata

from ..db import Database
from ..semantics.normalize import display_person_name, normalize_person_name

#: Where the public Lattes scrape keeps the spelling the person uses themselves.
LATTES_NAME_PATH = "$['dadosgerais']['nomecompleto']"


def fold(text: object) -> str:
    """Fold to comparable letters: no accents, no case, no punctuation.

    Used only to decide whether two spellings are *the same name*, never to
    store or display one.
    """
    decomposed = unicodedata.normalize("NFD", str(text or "").lower())
    return "".join(c for c in decomposed if c.isalnum() and not unicodedata.combining(c))


def accented_names(db: Database) -> dict[str, str]:
    """``siape -> the spelling the person uses``, for the names that check out.

    Accepted only when it is demonstrably the same name — equal once accents,
    case and punctuation are folded away. Two of the sixty-five professors have
    a Lattes name genuinely different from the SIGAA one (a dropped surname),
    and those must keep the SIGAA spelling rather than become somebody slightly
    else.
    """
    if not db.table_exists("sigaa_public_lattes_flat"):
        return {}
    rows = db.query_df(
        "SELECT p.siape, p.canonical_name, f.value_text AS lattes_name "
        "FROM professors p JOIN sigaa_public_lattes_flat f "
        "  ON f.siape = p.siape AND f.path = ? "
        "WHERE f.value_text IS NOT NULL",
        [LATTES_NAME_PATH],
    )
    out: dict[str, str] = {}
    for row in rows.itertuples():
        name = " ".join(str(row.lattes_name).split())
        if name and fold(name) == fold(row.canonical_name):
            out[str(row.siape)] = name
    return out


class Names:
    """A display-name resolver, built once and asked many times.

    The recovery query joins the whole Lattes archive, so a caller that resolves
    a name per row — a table of 187 work plans, say — must not re-run it per row.
    """

    def __init__(self, db: Database):
        self._db = db
        self._by_siape: dict[str, str] | None = None

    def _loaded(self) -> dict[str, str]:
        if self._by_siape is None:
            try:
                self._by_siape = accented_names(self._db)
            except Exception:
                # A missing or half-written Lattes archive must degrade to
                # title-casing, never take a page down.
                self._by_siape = {}
        return self._by_siape

    def display(self, siape: object, fallback: object = "") -> str:
        recovered = self._loaded().get(str(siape))
        return recovered or display_person_name(str(fallback or ""))


def known_people(db: Database) -> dict[str, str]:
    """``normalized name -> siape`` for everyone the faculty index holds.

    Includes the alias table, because acquisition already did the work of
    deciding that "M. G. M. M. Taveira" on a project team is the professor whose
    SIAPE we hold, and re-deriving that here would be a second, weaker answer to
    a question already settled.
    """
    out: dict[str, str] = {}
    for row in db.query_df("SELECT siape, canonical_name FROM professors").itertuples():
        key = normalize_person_name(row.canonical_name)
        if key:
            out[key] = str(row.siape)
    if db.table_exists("professor_aliases"):
        for row in db.query_df(
            "SELECT normalized_alias, siape FROM professor_aliases "
            "WHERE COALESCE(siape,'') <> ''"
        ).itertuples():
            if row.normalized_alias:
                out.setdefault(str(row.normalized_alias), str(row.siape))
    return out
