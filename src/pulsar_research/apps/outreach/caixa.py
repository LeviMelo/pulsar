"""De-shouting for plan titles that SIGAA stores in capitals.

Sixteen of the 187 plan titles are typed entirely in capitals, and quoting one
verbatim makes the second line of a cold email shout at its recipient. Lowering
the whole string is not safe: several of those titles carry technical
identifiers whose casing *is* the name — ``miR-31-5p``, ``microRNAs`` — and a
message that renames the very molecules the professor studies is worse than one
that shouts.

So the transformation is per token, and conservative:

* a token containing any lowercase letter is a deliberate spelling and is left
  exactly as it is (this is what protects ``miR-31-5p``);
* a short all-caps token is assumed to be an acronym and kept, except for the
  closed set of Portuguese function words, which have no acronym reading;
* longer all-caps tokens are lowered, unless they are a known sigla.

`render._siglas` runs afterwards and restores the acronyms it knows.
"""

from __future__ import annotations

# Closed set: these have no acronym reading in a research title, so an all-caps
# occurrence is always shouting. Without them a de-shouted title still reads
# "...na epileptogênese EM modelo experimental DE epilepsia".
_FUNCAO = {
    "A", "AS", "AO", "AOS", "À", "ÀS", "COM", "DA", "DAS", "DE", "DO", "DOS",
    "E", "EM", "ENTRE", "NA", "NAS", "NO", "NOS", "O", "OS", "OU", "PARA",
    "PELA", "PELAS", "PELO", "PELOS", "POR", "SOB", "SOBRE", "UM", "UMA",
}
# Below this length an all-caps token is far more likely to be an acronym (DNA,
# PCR, HIV, SUS) than a shouted word.
_ACRONIMO_MAX = 3
# How much of a title must be capitals before it counts as shouted at all. A
# normally-cased title with one acronym in it must not be touched.
_LIMIAR = 0.6


def esta_gritando(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) <= 12:
        return False
    return sum(1 for c in letters if c.isupper()) / len(letters) >= _LIMIAR


def desgritar(value, siglas: frozenset[str] = frozenset()) -> str:
    text = str(value)
    if not esta_gritando(text):
        return text

    out = []
    for token in text.split(" "):
        stripped = token.strip(".,;:()[]\"'“”—-")
        if any(c.islower() for c in stripped):
            out.append(token)                      # a deliberate spelling
        elif stripped.upper() in _FUNCAO:
            out.append(token.lower())
        elif stripped.upper() in siglas or len(stripped) <= _ACRONIMO_MAX:
            out.append(token)                      # acronym, kept
        else:
            out.append(token.lower())
        continue
    result = " ".join(out)
    # The title is quoted as a sentence, so it starts with a capital.
    for index, char in enumerate(result):
        if char.isalpha():
            return result[:index] + char.upper() + result[index + 1:]
    return result
