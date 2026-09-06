"""Recovering the accented spelling of a professor's name for the salutation.

`professors.canonical_name` is normalized for matching: lowercased, with the
accents stripped. Title-casing it back gives "Diego Figueiredo Nobrega", and
misspelling the recipient's own name in the first line is the most expensive
small error a cold email can make.

The Lattes record kept by the same scrape carries the real spelling under
``$['dadosgerais']['nomecompleto']``, so that is the source of truth here. It is
accepted only when it is the *same name* — equal once accents, case, and
punctuation are folded away. Two of the 65 professors have a Lattes name that is
genuinely different from the SIGAA one (a dropped surname), and those must keep
the SIGAA spelling rather than be addressed as somebody slightly else.
"""

from __future__ import annotations

import unicodedata

from ..db import Database

_LATTES_PATH = "$['dadosgerais']['nomecompleto']"


def dobrar(text: str) -> str:
    """Fold to comparable letters: no accents, no case, no punctuation."""
    decomposed = unicodedata.normalize("NFD", str(text).lower())
    return "".join(c for c in decomposed if c.isalnum() and not unicodedata.combining(c))


def accented_names(db: Database) -> dict[str, str]:
    """``siape -> properly accented full name``, for the names that check out."""
    rows = db.query_df(
        "SELECT p.siape, p.canonical_name, f.value_text AS lattes_name "
        "FROM professors p JOIN sigaa_public_lattes_flat f "
        "  ON f.siape = p.siape AND f.path = ? "
        "WHERE f.value_text IS NOT NULL",
        [_LATTES_PATH],
    )
    out: dict[str, str] = {}
    for row in rows.itertuples():
        name = " ".join(str(row.lattes_name).split())
        if name and dobrar(name) == dobrar(row.canonical_name):
            out[str(row.siape)] = name
    return out
