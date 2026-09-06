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
from typing import Iterable, Sequence

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


class PersonResolver:
    """External co-author name → SIAPE, when it is demonstrably the same person.

    Lattes writes a team member's name however the person who filled the form
    wrote it. "Ana Malhado" and "Ana Cláudia Mendes Malhado" are one professor;
    exact matching makes them two, and the graph then attributes half her
    collaboration to a ghost — which does not merely lose an edge, it moves
    every degree, centrality and bridging value she and her neighbours have.
    Fifteen of these were sitting in the live store.

    Three attempts, in decreasing order of confidence:

    1. the exact normalized name, which is what the faculty index holds;
    2. the alias table, because acquisition already decided that
       "M. G. M. M. Taveira" on a project team is a particular SIAPE, and
       re-deriving that here would be a second, weaker answer to a settled
       question;
    3. first name, last name, and every token in between appearing in order
       inside one indexed name — and *only* if exactly one professor matches.

    Rule 3 is deliberately narrow. It will not merge "Ana Malhado" into
    "Ana Cláudia Mendes Malhado" if a second professor could also be meant, and
    it refuses a bare surname outright. What it cannot do is tell two people who
    genuinely share a first and last name apart; that is the same weakness
    `person:name-…` ids already carry, and the uniqueness requirement is what
    keeps it from compounding it silently. Every match it makes is counted, so a
    build reports how much of its own identity it inferred.
    """

    def __init__(self, db: Database):
        self._exact = known_people(db)
        self._by_ends: dict[tuple[str, str], list[tuple[str, list[str]]]] = {}
        for row in db.query_df("SELECT siape, canonical_name FROM professors").itertuples():
            tokens = normalize_person_name(row.canonical_name).split()
            if len(tokens) >= 2:
                self._by_ends.setdefault((tokens[0], tokens[-1]), []).append(
                    (str(row.siape), tokens))
        self.inferred: dict[str, str] = {}

    def resolve(self, name: str) -> str:
        """The SIAPE this name belongs to, or an empty string."""
        normalized = normalize_person_name(given_first(name))
        if not normalized:
            return ""
        exact = self._exact.get(normalized)
        if exact:
            return exact
        tokens = normalized.split()
        if len(tokens) < 2:
            return ""
        first, last = tokens[0], tokens[-1]
        # A first name written as an initial is looked up by that initial: the
        # index is keyed on full first names, so every professor with that
        # initial and surname is a candidate, and uniqueness still decides.
        if len(first) == 1:
            pools = [v for (f, l), v in self._by_ends.items() if l == last and f[0] == first]
            candidates = [c for pool in pools for c in pool if _ordered_subset(tokens, c[1])]
        else:
            candidates = [(siape, full) for siape, full in self._by_ends.get((first, last), [])
                          if _ordered_subset(tokens, full)]
        if len(candidates) != 1:
            return ""
        siape = candidates[0][0]
        self.inferred[normalized] = siape
        return siape


def _ordered_subset(short: Sequence[str], full: Sequence[str]) -> bool:
    """Does every token of `short` appear in `full`, in the same order?

    Order matters: "Silva Ana" is not evidence for "Ana … Silva". Dropped middle
    names are ordinary in Brazilian academic naming; reordered ones are not. A
    single-letter token is an initial and matches any full token it begins.
    """
    remaining = iter(full)
    return all(any(_token_matches(token, candidate) for candidate in remaining) for token in short)


def _token_matches(short: str, full: str) -> bool:
    return short == full or (len(short) == 1 and full.startswith(short))


# ---------------------------------------------------------------------------
# Strangers: people we hold only a name for
# ---------------------------------------------------------------------------

_PARTICLES = frozenset({"de", "da", "do", "das", "dos", "e", "di", "del", "van", "von", "y", "la", "le"})


def given_first(name: str) -> str:
    """`"LADLE, Richard J."` → `"Richard J. Ladle"`; anything else unchanged.

    CNPq stores a citation name next to the full one and people paste either
    into a team list, so the same co-author arrives both ways. Reordering the
    comma form is safe: a comma in a personal name means exactly this.
    """
    text = " ".join(str(name or "").split())
    if text.count(",") != 1:
        return text
    surname, given = (part.strip() for part in text.split(","))
    if not surname or not given:
        return text
    return f"{given} {surname}"


def _tokens(name: str) -> list[str]:
    return [t for t in normalize_person_name(given_first(name)).split() if t.lower() not in _PARTICLES]


def name_key(name: str) -> tuple[str, str]:
    """`(first initial, last surname)` — the coarsest key two spellings of one
    person will share, and the bucket `merge_names` refines within."""
    tokens = _tokens(name)
    if not tokens:
        return ("", "")
    if len(tokens) == 1:
        return (tokens[0][0], tokens[0])
    return (tokens[0][0], tokens[-1])


def _compatible(a: Sequence[str], b: Sequence[str]) -> bool:
    """Two token lists that could be the same person: same surname, first
    names equal or one an initial of the other, and the shorter one's tokens
    appearing in order (as tokens or initials) in the longer."""
    if not a or not b or a[-1] != b[-1]:
        return False
    if not (_token_matches(a[0], b[0]) or _token_matches(b[0], a[0])):
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return _ordered_subset(short, long)


def merge_names(names: Iterable[str]) -> dict[str, str]:
    """``spelling -> representative spelling`` for names that are one person.

    "Richard James Ladle", "Richard J Ladle", "LADLE, R. J." and "R. Ladle"
    are one co-author on one professor's CV. Nothing here can prove that, and
    the merge is deliberately confined to spellings that share a surname and a
    compatible first name — which is why "Ricardo Ladle" stays apart — but a
    social record that lists one collaborator four times is not a record, it is
    a spelling inventory. The representative is the longest spelling, because it
    carries the most of the name.
    """
    buckets: dict[tuple[str, str], list[str]] = {}
    for name in names:
        key = name_key(name)
        if key[1]:
            buckets.setdefault(key, []).append(name)

    out: dict[str, str] = {}
    for spellings in buckets.values():
        tokens = {sp: _tokens(sp) for sp in spellings}
        groups: list[list[str]] = []
        for spelling in sorted(set(spellings), key=lambda sp: (-len(tokens[sp]), -len(sp))):
            for group in groups:
                if all(_compatible(tokens[spelling], tokens[member]) for member in group):
                    group.append(spelling)
                    break
            else:
                groups.append([spelling])
        for group in groups:
            representative = given_first(max(
                group, key=lambda sp: (len(tokens[sp]), len(given_first(sp)))))
            for spelling in group:
                out[spelling] = representative
    return out
