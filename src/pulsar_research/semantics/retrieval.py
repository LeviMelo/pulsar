"""Retrieval operators and rank-space combination.

BM25F is a *retrieval operator*, not a representation: it answers "how relevant
is this document to this query" without inducing a document geometry. Keeping it
in its own module makes that distinction structural rather than a comment.
"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import CountVectorizer

from .normalize import PORTUGUESE_STOP_WORDS, TOKEN_PATTERN, clean_text


class BM25FIndex:
    """Field-weighted BM25 over a fixed record set.

    Field weights are applied to term frequencies *before* saturation (the
    Robertson BM25F formulation), so a term in a title is genuinely worth more
    rather than producing a separately saturated per-field score.
    """

    def __init__(
        self,
        field_weights: Mapping[str, float],
        *,
        k1: float = 1.5,
        b: float = 0.75,
        max_features: int = 30000,
        vocabulary: Sequence[str] | None = None,
    ) -> None:
        self.field_weights = dict(field_weights)
        self.k1 = float(k1)
        self.b = float(b)
        self.vectorizer = CountVectorizer(
            lowercase=True,
            strip_accents="unicode",
            ngram_range=(1, 2),
            max_features=None if vocabulary is not None else max_features,
            min_df=1,
            stop_words=list(PORTUGUESE_STOP_WORDS),
            token_pattern=TOKEN_PATTERN,
            vocabulary=list(vocabulary) if vocabulary is not None else None,
        )
        self.mats: dict[str, sparse.csr_matrix] = {}
        self.lengths: dict[str, np.ndarray] = {}
        self.avg_lengths: dict[str, float] = {}
        self.idf: np.ndarray | None = None
        self.n_docs = 0

    def fit(self, records: Sequence[Mapping[str, str]]) -> "BM25FIndex":
        self.n_docs = len(records)
        if self.vectorizer.vocabulary is None:
            fit_text = [clean_text(rec.get(f, "")) for rec in records for f in self.field_weights] or ["vazio"]
            self.vectorizer.fit(fit_text)
        else:
            self.vectorizer.fit(["vazio"])
        presence: sparse.csr_matrix | None = None
        for field in self.field_weights:
            mat = self.vectorizer.transform([clean_text(rec.get(field, "")) for rec in records]).tocsr()
            self.mats[field] = mat
            lens = np.asarray(mat.sum(axis=1)).ravel().astype(float)
            self.lengths[field] = lens
            self.avg_lengths[field] = max(float(lens.mean()) if len(lens) else 1.0, 1e-9)
            p = (mat > 0).astype(np.int8)
            presence = p if presence is None else ((presence + p) > 0).astype(np.int8)
        n_terms = len(self.vectorizer.get_feature_names_out())
        if presence is None:
            self.idf = np.zeros(n_terms)
        else:
            df = np.asarray(presence.sum(axis=0)).ravel().astype(float)
            n = max(self.n_docs, 1)
            self.idf = np.log(1.0 + (n - df + 0.5) / (df + 0.5))
        return self

    def score(self, query: str) -> np.ndarray:
        if self.idf is None:
            raise RuntimeError("BM25F index is not fitted")
        q = self.vectorizer.transform([clean_text(query) or "pesquisa"])
        if q.nnz == 0 or self.n_docs == 0:
            return np.zeros(self.n_docs, dtype=float)
        score = np.zeros(self.n_docs, dtype=float)
        for j in q.indices:
            weighted_tf = np.zeros(self.n_docs, dtype=float)
            for field, weight in self.field_weights.items():
                tf = np.asarray(self.mats[field][:, j].todense()).ravel().astype(float)
                norm = 1.0 - self.b + self.b * self.lengths[field] / self.avg_lengths[field]
                weighted_tf += float(weight) * tf / np.where(norm > 0, norm, 1.0)
            denom = self.k1 + weighted_tf
            score += self.idf[j] * ((self.k1 + 1.0) * weighted_tf / np.where(denom > 0, denom, 1.0))
        return score


def ordinal_ranks_desc(values: np.ndarray) -> np.ndarray:
    """1-based ranks, highest score first, ties broken by original order."""
    values = np.nan_to_num(np.asarray(values, dtype=float), nan=-np.inf)
    order = np.argsort(-values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(1, len(values) + 1, dtype=float)
    return ranks


def rrf(scores: Sequence[np.ndarray], *, weights: Sequence[float] | None = None, k: int = 60) -> np.ndarray:
    """Reciprocal rank fusion, normalized so the theoretical maximum is 1.

    RRF is only appropriate for combining *decorrelated* rankings. Fusing word
    TF-IDF, char TF-IDF and BM25F triple-counts one lexical signal and drowns
    out a genuinely independent latent one, which is why PULSAR now exposes the
    channels separately instead of pre-fusing them.
    """
    if not scores:
        return np.array([], dtype=float)
    weights = list(weights or [1.0] * len(scores))
    if len(weights) != len(scores):
        raise ValueError("weights and scores length differ")
    out = np.zeros(len(scores[0]), dtype=float)
    theoretical = 0.0
    for values, weight in zip(scores, weights):
        out += float(weight) / (float(k) + ordinal_ranks_desc(np.asarray(values)))
        theoretical += float(weight) / (float(k) + 1.0)
    return out / theoretical if theoretical > 0 else out


def percentile_rank(values: np.ndarray) -> np.ndarray:
    """Within-corpus percentile (0-100). The only honest reading of a rank score."""
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n == 0:
        return values
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(1, n + 1, dtype=float)
    # Average ties so equal scores get equal percentiles.
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inverse, ranks)
    return (sums / counts)[inverse] / n * 100.0
