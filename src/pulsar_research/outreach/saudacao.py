"""The opening line of the message.

"Prezado(a) Prof(a). Diego Figueiredo Nóbrega" leaks the template into the
prose: the whole point of generating each message individually is to produce
correspondence that reads as though it were written to one person, not to
advertise that a placeholder was filled. What a person writes is "Prezado
professor Diego,".

Portuguese forces a gendered adjective in that greeting, and neither SIGAA nor
the Lattes records carry a gender field. The rule below is the ordinary
Portuguese one — a first name ending in -a is feminine — with an explicit list
for the names it gets wrong. The list is meant to be read and corrected: it is
about real people, and a misgendered salutation is worse than a stiff one.
"""

from __future__ import annotations

import unicodedata

# First names the -a rule misclassifies. Everything here is treated as feminine.
FEMININO = {
    "carolinne", "elaine", "jamylle", "josineide", "michelle", "regianne",
    "tami", "ines", "isabel", "raquel", "beatriz", "ester", "miriam", "solange",
}
# ...and the reverse: names ending in -a that are masculine.
MASCULINO = {"joca", "juca", "nicola", "luca", "sasha", "attila", "elia"}


def _dobrar(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if c.isalpha() and not unicodedata.combining(c))


def primeiro_nome(full_name: str) -> str:
    parts = [p for p in str(full_name).split() if p]
    return parts[0] if parts else ""


def e_feminino(full_name: str) -> bool:
    first = _dobrar(primeiro_nome(full_name))
    if first in FEMININO:
        return True
    if first in MASCULINO:
        return False
    return first.endswith("a")


def saudacao(full_name: str) -> str:
    """``"Diego Figueiredo Nóbrega" -> "Prezado professor Diego"``."""
    first = primeiro_nome(full_name)
    if not first:
        return "Prezado(a) professor(a)"
    return f"{'Prezada professora' if e_feminino(full_name) else 'Prezado professor'} {first}"
