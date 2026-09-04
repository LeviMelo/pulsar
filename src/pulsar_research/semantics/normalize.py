"""Deterministic text normalization shared by every semantic representation.

Everything in this module is pure, offline and reproducible. Two PULSAR runs
over identical raw data must produce byte-identical normalized text, because the
semantic space identity is a hash over exactly these outputs.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable


# ---------------------------------------------------------------------------
# Lexicons
# ---------------------------------------------------------------------------

# scikit-learn ships an English stop list but not a Portuguese one. Keep this
# local and explicit so the pipeline stays fully offline and rebuildable.
PORTUGUESE_STOP_WORDS = frozenset(
    """
    a ao aos aquela aquelas aquele aqueles aquilo as ate com como da das de dela delas dele deles
    depois do dos e ela elas ele eles em entre era eram essa essas esse esses esta estas este estes
    eu foi foram ha isso isto ja lhe lhes mais mas me mesmo meu meus minha minhas muito muita muitas
    muitos na nas nao nem no nos nossa nossas nosso nossos num numa o os ou para pela pelas pelo pelos
    por porque qual quais quando que quem se sem seu seus sua suas tambem tem tendo ter teve tinham um
    uma umas uns voce voces sobre sob ser sao sendo sido tinha ainda onde cada contra desde durante
    enquanto aqui ali la assim entao portanto porem contudo atraves acerca mediante conforme
    segundo todos todas todo toda outro outra outros outras proprio propria proprios proprias tanto
    quanto tal tais somente apenas pode podem poderia poderiam deve devem cujo cuja cujos cujas
    """.split()
)

# Scaffolding produced by our own document assembly or by SIGAA/Lattes metadata.
# Useful for retrieval, actively harmful for unsupervised topic discovery.
TOPIC_BOILERPLATE = frozenset(
    """
    projeto projetos plano planos area areas professor professora pesquisa pesquisas
    justificativa introducao objetivo objetivos metodologia metodologias habilidade
    habilidades atividade atividades trabalho trabalhos curriculo lattes sigaa ufal
    sim nao brasil brasileiro brasileira portugues portuguesa
    serao sera realizado realizada realizados realizadas estudo estudos dados analise
    avaliacao resultados desenvolvimento desenvolver realizar avaliar investigar
    cientifico cientifica cientificos cientificas estudante aluno aluna et al
    medicina ciencias referencias bibliograficas bibliografica disponivel
    """.split()
)

# Facet-specific scaffolding. A "methods" factor should not be defined by
# "o bolsista participara das tarefas enumeradas a seguir".
FACET_BOILERPLATE: dict[str, frozenset[str]] = {
    "domain": frozenset(),
    "methods": frozenset(
        """
        participara participar participacao tarefas tarefa enumeradas enumerada seguir
        descrita descrito elaboracao relatorios relatorio etapas etapa procedimentos
        procedimento cronograma previstas previsto
        """.split()
    ),
    "skills": frozenset(
        """
        desenvolvera desenvolver competencias competencia bolsista tera acesso ira
        aprender aprendizado formacao profissional graduacao pos-graduacao ingresso
        apresentacao academicos academico ambito oportunidade proporcionara permitira
        capacitacao adquirir adquiridas adquiridos aprimorar aprimoramento
        """.split()
    ),
}

TOPIC_STOP_WORDS = frozenset(PORTUGUESE_STOP_WORDS | TOPIC_BOILERPLATE)

# Token pattern that keeps technical identifiers intact: R, SIH/SUS, PCR, C++,
# scikit-learn, REDCap. Single letters are kept only when uppercase in source,
# which the vectorizers cannot see, so the minimum length stays 2 here and the
# skill extractor handles single-letter languages separately.
TOKEN_PATTERN = r"(?u)\b[a-zA-ZÀ-ÿ][a-zA-ZÀ-ÿ0-9_+.#/-]+\b"


# ---------------------------------------------------------------------------
# Citation scrubbing
# ---------------------------------------------------------------------------

_CITATION_PAREN = re.compile(r"\([^()]{0,140}\b(?:19|20)\d{2}[a-z]?\b[^()]{0,140}\)", re.I)
_CITATION_ETAL = re.compile(
    r"\b[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]+(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]+){0,3}\s+et\s+al\.?\s*,?\s*\(?\s*(?:19|20)\d{2}[a-z]?\s*\)?",
    re.I,
)
_CITATION_BRACKET = re.compile(r"\[(?:\s*\d+\s*[,;]?\s*)+\]")
_YEAR_AUTHOR = re.compile(r"\b[A-ZÀ-Ý]{2,}(?:\s+[A-ZÀ-Ý]{2,}){0,3}\s*,?\s*(?:19|20)\d{2}[a-z]?\b")
_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_DOI = re.compile(r"\b10\.\d{4,9}/\S+\b", re.I)
_BULLET = re.compile(r"[●▪•‣◦⁃·]+")


def clean_text(value: object) -> str:
    """Whitespace-normalize a value into a single-line analytical string."""
    text = _BULLET.sub(" ", str(value or " "))
    return re.sub(r"\s+", " ", text).strip()


def scrub_citations(value: object) -> str:
    """Strip citation syntax from an analytical *view*; raw data is untouched.

    Author-year noise is lexically dense and highly repetitive across unrelated
    projects, so it inflates similarity between documents that merely share a
    reference style.
    """
    text = clean_text(value)
    text = _URL.sub(" ", text)
    text = _DOI.sub(" ", text)
    text = _CITATION_PAREN.sub(" ", text)
    text = _CITATION_ETAL.sub(" ", text)
    text = _CITATION_BRACKET.sub(" ", text)
    text = _YEAR_AUTHOR.sub(" ", text)
    return clean_text(text)


def fold(value: object) -> str:
    """Accent-folded, lowercased form used for gazetteer and alias matching."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().lower()


def normalize_person_name(value: object) -> str:
    """Canonical comparison key for a human name (accent- and case-insensitive)."""
    return re.sub(r"[^A-Z0-9]+", " ", fold(value).upper()).strip()


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def join_unique(parts: Iterable[object], *, limit: int | None = None, sep: str = "\n") -> str:
    """Concatenate parts, dropping repeats, without creating cross-field bigrams.

    Sibling work plans copy large blocks of project prose verbatim; de-duplicating
    at assembly time keeps one project from being modelled as five copies of the
    same paragraph.
    """
    out: list[str] = []
    seen: set[str] = set()
    size = 0
    for value in parts:
        text = clean_text(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        if limit is not None and size + len(text) > limit:
            remaining = max(0, limit - size)
            if remaining:
                out.append(text[:remaining])
            break
        out.append(text)
        size += len(text) + 1
    return sep.join(out)


def stop_words_for(facet: str | None = None) -> list[str]:
    """Stop list for topic modelling in a given facet."""
    base = set(TOPIC_STOP_WORDS)
    if facet:
        base |= set(FACET_BOILERPLATE.get(facet, frozenset()))
    return sorted(base)

# Portuguese name particles stay lowercase when a stored name is title-cased for
# display. Names are stored folded and lowercase because they are join keys; a
# salutation must not read "Olá, diego figueiredo nobrega".
NAME_PARTICLES = frozenset({"de", "da", "do", "das", "dos", "e", "di", "del", "van", "von", "y"})


def display_person_name(name: str) -> str:
    """Title-case a stored person name for human-facing text."""
    parts = [p for p in str(name or "").split() if p]
    if not parts:
        return ""
    out = []
    for i, part in enumerate(parts):
        lower = part.lower()
        out.append(lower if i and lower in NAME_PARTICLES else lower[:1].upper() + lower[1:])
    return " ".join(out)
