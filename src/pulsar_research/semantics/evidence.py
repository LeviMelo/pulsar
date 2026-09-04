"""Professor intelligence: a portfolio, ranked by evidence that explains itself.

Two defects in the previous design are fixed here.

**Generic evidence.** A Lattes knowledge area of "Medicina" used to surface as a
professor's top reason for matching. It is true, it is uninformative, and it
makes the system look stupid. Every atom now carries a weight built from
*specificity* (what kind of record is it), *recency* (when), and *corpus rarity*
(does its vocabulary distinguish it from everyone else). "Medicina" scores badly
on all three.

**One conflated ranking.** "Who researches things like me" and "who can supervise
me next semester" are different questions. PULSAR answers both:

``current``
    restricted to open work plans, active SIGAA projects and active research
    lines — the actionable question.
``trajectory``
    the whole portfolio — the intellectual-compatibility question.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import numpy as np

from .corpus import Atom
from .normalize import fold

#: Years for an item's recency weight to halve. Six years keeps a 2019 project
#: clearly relevant and a 2005 one clearly historical.
RECENCY_HALF_LIFE = 6.0
NO_YEAR_RECENCY = 0.70


def recency_weight(year: int | None, *, now: int | None = None) -> float:
    if not year:
        return NO_YEAR_RECENCY
    current = now or datetime.now(timezone.utc).year
    age = max(0.0, float(current - year))
    return float(2.0 ** (-age / RECENCY_HALF_LIFE))


def rarity_weights(texts: Sequence[str]) -> np.ndarray:
    """Mean inverse document frequency of each text's tokens, scaled to (0, 1].

    An atom whose every word is common across the whole corpus ("Medicina",
    "Saúde Coletiva") is a poor explanation regardless of how well it matches;
    an atom full of corpus-rare terms ("Galleria mellonella", "SIH/SUS") is a
    good one. This is computed over the atom corpus, so it adapts to whatever
    UFAL actually researches instead of a hand-written blacklist.
    """
    import re

    token_re = re.compile(r"[a-z][a-z0-9+./-]{2,}")
    n = max(len(texts), 1)
    df: dict[str, int] = defaultdict(int)
    token_sets: list[set[str]] = []
    for text in texts:
        toks = {t.rstrip("._-/") for t in token_re.findall(fold(text))}
        token_sets.append(toks)
        for t in toks:
            df[t] += 1
    max_idf = math.log((n + 1) / 1.0)
    out = np.zeros(len(texts), dtype=float)
    for i, toks in enumerate(token_sets):
        if not toks:
            continue
        idfs = [math.log((n + 1) / (df[t] + 1)) for t in toks]
        out[i] = float(np.mean(idfs)) / max(max_idf, 1e-9)
    # A short, very rare title should not beat a rich one purely on rarity.
    return np.clip(out, 0.05, 1.0)


@dataclass(slots=True)
class EvidenceRow:
    siape: str
    scope: str
    rank: int
    atom: Atom
    score: float
    weight: float
    channels: dict[str, float]

    def payload(self) -> dict[str, Any]:
        return {
            "channels": {k: round(float(v), 6) for k, v in self.channels.items()},
            "specificity": round(self.atom.specificity, 3),
            "recency": round(recency_weight(self.atom.year), 3),
            "is_current": self.atom.is_current,
            "source_ref": self.atom.source_ref,
            "excerpt": self.atom.text[:400],
        }


@dataclass(slots=True)
class PortfolioResult:
    siapes: list[str]
    #: scope -> channel -> score array aligned with ``siapes``
    scores: dict[str, dict[str, np.ndarray]]
    evidence: list[EvidenceRow]
    atom_counts: dict[str, int]


def _top_weighted_mean(values: Sequence[float], weights: Sequence[float], k: int) -> float:
    """Mean of the k best weighted contributions.

    A top-k mean rather than a portfolio mean, because breadth must not dilute a
    strong match: a professor with one perfect project and forty unrelated ones
    is still a strong match for that project.
    """
    pairs = sorted(zip(values, weights), key=lambda vw: -(vw[0] * vw[1]))[: max(1, k)]
    if not pairs:
        return 0.0
    total_w = sum(w for _, w in pairs)
    if total_w <= 0:
        return 0.0
    return float(sum(v * w for v, w in pairs) / total_w)


def score_portfolios(
    atoms: Sequence[Atom],
    siapes: Sequence[str],
    channel_scores: Mapping[str, np.ndarray],
    *,
    top_k: int = 3,
    evidence_per_scope: int = 6,
) -> PortfolioResult:
    """Aggregate atom-level channel scores into professor-level scores + evidence.

    ``channel_scores`` maps a channel name (``lexical_word``, ``latent``,
    ``neural`` …) to an array aligned with ``atoms``. Channels stay separate all
    the way through; nothing is averaged into a single number here.
    """
    index = {s: i for i, s in enumerate(siapes)}
    n = len(siapes)
    weights = rarity_weights([a.text for a in atoms]) if atoms else np.zeros(0)
    evidence_weight = np.array([
        atoms[i].specificity * recency_weight(atoms[i].year) * weights[i]
        for i in range(len(atoms))
    ]) if atoms else np.zeros(0)

    by_prof: dict[str, list[int]] = defaultdict(list)
    for i, atom in enumerate(atoms):
        if atom.siape in index:
            by_prof[atom.siape].append(i)

    channels = list(channel_scores)
    # The channel used to *order* evidence. Latent affinity generalizes past
    # exact wording, which is what an explanation should surface; it falls back
    # to whatever is available.
    ranking_channel = next((c for c in ("fused", "neural", "latent", "lexical_word") if c in channel_scores),
                           channels[0] if channels else "")

    # An opportunity is the thing being matched *against*, not part of the
    # professor's scholarly record. Scoring trajectory over it is circular — it
    # says "this career fits you because their open call fits you" — and because
    # opportunities carry the top specificity and perfect recency they crowded
    # every other kind out of the trajectory evidence (85% overlap with current).
    scopes = {
        "trajectory": lambda a: a.kind != "opportunity",
        "current": lambda a: a.is_current,
    }
    scores: dict[str, dict[str, np.ndarray]] = {
        scope: {ch: np.zeros(n) for ch in channels} for scope in scopes
    }
    evidence: list[EvidenceRow] = []

    for siape, indices in by_prof.items():
        pi = index[siape]
        for scope, predicate in scopes.items():
            subset = [i for i in indices if predicate(atoms[i])]
            if not subset:
                continue
            ranked = sorted(
                subset,
                key=lambda i: (-(float(channel_scores[ranking_channel][i]) * float(evidence_weight[i])),
                               atoms[i].atom_id),
            )
            chosen = ranked[: max(1, top_k)]
            for ch in channels:
                scores[scope][ch][pi] = _top_weighted_mean(
                    [float(channel_scores[ch][i]) for i in chosen],
                    [float(evidence_weight[i]) for i in chosen],
                    top_k,
                )
            for rank, i in enumerate(ranked[:evidence_per_scope], start=1):
                evidence.append(EvidenceRow(
                    siape=siape, scope=scope, rank=rank, atom=atoms[i],
                    score=float(channel_scores[ranking_channel][i]),
                    weight=float(evidence_weight[i]),
                    channels={ch: float(channel_scores[ch][i]) for ch in channels},
                ))

    return PortfolioResult(
        siapes=list(siapes),
        scores=scores,
        evidence=evidence,
        atom_counts={s: len(v) for s, v in by_prof.items()},
    )
