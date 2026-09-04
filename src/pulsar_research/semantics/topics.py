"""Interpretable topic structure.

NMF is an **interpretability device**, not an ontology. For ``X ≈ W H`` a row of
``H`` is just a non-negative basis vector over terms; calling it a "topic" is a
human reading that happens to be useful because non-negativity makes the factors
additive and parts-based. PULSAR treats them accordingly: topics label and
navigate the landscape, they never define what a research field *is* and they
are not used as a similarity metric.

Two things changed from the previous engine:

* **Model selection is no longer reconstruction-driven.** Reconstruction error
  falls monotonically in k, so any score that rewards it is biased toward more
  topics. Selection now weights coherence, exclusivity, independent support and
  *stability under deterministic perturbation*, then applies a parsimony rule:
  take the smallest k within epsilon of the best score.
* **Structure is hierarchical.** Research is not 14 competing flat labels; it is
  broad fields that subdivide. A two-level tree is both more honest and more
  navigable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from scipy import sparse
from sklearn.decomposition import NMF
from sklearn.feature_extraction.text import TfidfVectorizer

from .normalize import TOKEN_PATTERN, clean_text, stop_words_for


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def npmi_coherence(x: sparse.csr_matrix, components: np.ndarray, top_n: int = 8) -> float:
    """Mean pairwise NPMI of each factor's top terms. Higher is more coherent."""
    binary = (x > 0).astype(np.int8).tocsc()
    n = max(binary.shape[0], 1)
    dfs = np.asarray(binary.sum(axis=0)).ravel().astype(float)
    scores: list[float] = []
    for comp in components:
        idx = np.argsort(comp)[::-1][:top_n]
        pairs: list[float] = []
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                i, j = int(idx[a]), int(idx[b])
                pi, pj = dfs[i] / n, dfs[j] / n
                if pi <= 0 or pj <= 0:
                    continue
                co = float(binary[:, i].multiply(binary[:, j]).sum())
                if co <= 0:
                    pairs.append(-1.0)
                    continue
                pij = co / n
                pmi = math.log(pij / (pi * pj))
                pairs.append(pmi / (-math.log(pij) if pij < 1 else 1.0))
        if pairs:
            scores.append(float(np.mean(pairs)))
    return float(np.mean(scores)) if scores else -1.0


def exclusivity(components: np.ndarray, top_n: int = 10) -> float:
    """How much of each top term's mass belongs to its own factor (FREX-style)."""
    colsum = components.sum(axis=0)
    scores: list[float] = []
    for comp in components:
        idx = np.argsort(comp)[::-1][:top_n]
        share = np.divide(comp[idx], colsum[idx], out=np.zeros(len(idx)), where=colsum[idx] > 0)
        scores.append(float(np.mean(share)))
    return float(np.mean(scores)) if scores else 0.0


def _fit_nmf(x: sparse.csr_matrix, k: int, *, seed: int = 0) -> tuple[NMF, np.ndarray]:
    model = NMF(n_components=k, init="nndsvd", solver="cd", beta_loss="frobenius",
                max_iter=1200, tol=1e-5, random_state=seed)
    w = model.fit_transform(x)
    return model, w


def _match_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Mean best-match cosine between two factor bases (topic-matching score)."""
    from sklearn.preprocessing import normalize
    an, bn = normalize(a), normalize(b)
    sim = an @ bn.T
    return float(np.mean(sim.max(axis=1))) if sim.size else 0.0


def topic_stability(x: sparse.csr_matrix, k: int, *, trials: int = 4, keep: float = 0.85) -> float:
    """Refit on deterministic subsamples and measure factor reproducibility.

    A factor that only exists because of four particular documents is an
    artifact. Persisting this number next to the model is the difference between
    "we found 14 topics" and "we found 14 topics, 5 of which are noise".
    """
    n = x.shape[0]
    if n < 12 or k >= n:
        return 0.0
    reference, _ = _fit_nmf(x, k, seed=0)
    scores: list[float] = []
    for t in range(trials):
        rng = np.random.default_rng(1000 + t)
        idx = np.sort(rng.choice(n, size=max(k + 2, int(n * keep)), replace=False))
        try:
            model, _ = _fit_nmf(x[idx], k, seed=0)
        except Exception:
            continue
        scores.append(_match_similarity(reference.components_, model.components_))
    return float(np.mean(scores)) if scores else 0.0


def frex_labels(components: np.ndarray, terms: np.ndarray, top_n: int = 10) -> tuple[list[str], list[list[str]]]:
    """Rank terms by frequency × sqrt(exclusivity); the top three become a label."""
    colsum = components.sum(axis=0)
    labels: list[str] = []
    all_terms: list[list[str]] = []
    for comp in components:
        maxv = max(float(comp.max(initial=0.0)), 1e-12)
        freq = comp / maxv
        excl = np.divide(comp, colsum, out=np.zeros_like(comp), where=colsum > 0)
        score = freq * np.sqrt(excl)
        idx = np.argsort(score)[::-1][:top_n]
        chosen = [str(terms[i]) for i in idx if score[i] > 0] or ["sem tema"]
        all_terms.append(chosen)
        labels.append(" / ".join(chosen[:3]))
    return labels, all_terms


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TopicSelection:
    k: int
    candidates: list[dict[str, Any]]
    reason: str


def select_k(
    x: sparse.csr_matrix,
    *,
    min_k: int = 3,
    max_k: int = 12,
    min_support: int = 3,
    stability_trials: int = 4,
    epsilon: float = 0.02,
) -> TopicSelection:
    """Choose the number of factors, biased toward parsimony.

    Reconstruction is deliberately absent from the score. It improves with every
    added factor and therefore only measures capacity, not structure.
    """
    n_docs, n_terms = x.shape
    upper = max(2, min(max_k, n_docs - 1, n_terms))
    lower = max(2, min(min_k, upper))
    candidates: list[dict[str, Any]] = []
    for k in range(lower, upper + 1):
        model, w = _fit_nmf(x, k)
        row_sum = w.sum(axis=1, keepdims=True)
        shares = np.divide(w, row_sum, out=np.zeros_like(w), where=row_sum > 0)
        dominant = shares.argmax(axis=1) if shares.shape[1] else np.zeros(n_docs, dtype=int)
        support_counts = np.bincount(dominant, minlength=k)
        support = float(np.mean(support_counts >= min_support))
        coherence = npmi_coherence(x, model.components_)
        excl = exclusivity(model.components_)
        stab = topic_stability(x, k, trials=stability_trials)
        score = (0.35 * float(np.clip((coherence + 1.0) / 2.0, 0.0, 1.0))
                 + 0.25 * stab + 0.20 * support + 0.20 * excl)
        candidates.append({
            "k": k, "score": score, "npmi": coherence, "stability": stab,
            "support_fraction": support, "exclusivity": excl,
            "empty_topics": int(np.sum(support_counts < min_support)),
            "dominant_support": support_counts.tolist(),
        })
    if not candidates:
        return TopicSelection(1, [], "degenerate corpus")
    best = max(c["score"] for c in candidates)
    within = [c for c in candidates if c["score"] >= best - epsilon]
    chosen = min(within, key=lambda c: c["k"])
    return TopicSelection(
        chosen["k"], candidates,
        f"smallest k within {epsilon} of best composite score ({chosen['score']:.4f} vs {best:.4f})",
    )


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TopicNode:
    topic_id: str
    parent_id: str | None
    depth: int
    label: str
    terms: list[str]
    members: list[int]
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TopicModel:
    facet: str
    nodes: list[TopicNode]
    vectorizer: TfidfVectorizer
    root_components: np.ndarray
    root_weights: np.ndarray            # (n_docs, k_root) shares
    assignment: list[str]               # per document: deepest topic id
    diagnostics: dict[str, Any]

    @property
    def n_root(self) -> int:
        return int(self.root_components.shape[0])

    def transform(self, texts: Sequence[str]) -> np.ndarray:
        """Project unseen documents onto the root factor shares."""
        if not texts:
            return np.zeros((0, self.n_root))
        x = self.vectorizer.transform([clean_text(t) or "vazio" for t in texts])
        # Non-negative least squares against a fixed basis, via a few
        # multiplicative updates — the standard NMF transform, but explicit so it
        # stays deterministic across scikit-learn versions.
        h = self.root_components
        w = np.full((x.shape[0], h.shape[0]), 1e-6)
        hht = h @ h.T
        xht = np.asarray(x @ h.T)
        for _ in range(200):
            denom = w @ hht
            w = w * np.divide(xht, denom + 1e-12, out=np.zeros_like(w), where=True)
            w = np.clip(w, 0.0, None)
        row = w.sum(axis=1, keepdims=True)
        return np.divide(w, row, out=np.zeros_like(w), where=row > 0)

    def node(self, topic_id: str) -> TopicNode | None:
        return next((n for n in self.nodes if n.topic_id == topic_id), None)


def fit_topic_hierarchy(
    documents: Sequence[str],
    *,
    facet: str = "domain",
    min_k: int = 3,
    max_k: int = 8,
    min_support: int = 3,
    max_features: int = 24000,
    split_min_members: int = 10,
    split_max_k: int = 4,
    max_depth: int = 2,
) -> TopicModel:
    """Fit a two-level NMF topic tree over project-level documents."""
    docs = [clean_text(d) or "vazio" for d in documents]
    min_df = 2 if len(docs) >= 20 else 1
    vectorizer = TfidfVectorizer(
        lowercase=True, strip_accents="unicode", ngram_range=(1, 2),
        max_features=max_features, sublinear_tf=True, min_df=min_df,
        max_df=0.85 if len(docs) >= 20 else 1.0,
        stop_words=stop_words_for(facet), token_pattern=TOKEN_PATTERN,
    )
    try:
        x = vectorizer.fit_transform(docs).tocsr()
    except ValueError:
        vectorizer.set_params(min_df=1, max_df=1.0)
        x = vectorizer.fit_transform(docs).tocsr()
    terms = np.asarray(vectorizer.get_feature_names_out())

    if x.shape[1] == 0 or x.nnz == 0:
        node = TopicNode("t0", None, 0, "sem tema", ["sem tema"], list(range(len(docs))))
        return TopicModel(facet, [node], vectorizer, np.zeros((1, max(x.shape[1], 1))),
                          np.ones((len(docs), 1)), ["t0"] * len(docs),
                          {"reason": "degenerate corpus"})

    selection = select_k(x, min_k=min_k, max_k=max_k, min_support=min_support)
    model, w = _fit_nmf(x, selection.k)
    row = w.sum(axis=1, keepdims=True)
    shares = np.divide(w, row, out=np.zeros_like(w), where=row > 0)
    labels, top_terms = frex_labels(model.components_, terms)
    dominant = shares.argmax(axis=1)

    nodes: list[TopicNode] = []
    assignment = [""] * len(docs)
    for t in range(selection.k):
        members = [i for i in range(len(docs)) if int(dominant[i]) == t]
        topic_id = f"t{t}"
        nodes.append(TopicNode(topic_id, None, 0, labels[t], top_terms[t], members))
        for i in members:
            assignment[i] = topic_id

    if max_depth > 1:
        for parent in list(nodes):
            if parent.depth != 0 or len(parent.members) < split_min_members:
                continue
            sub_x = x[parent.members]
            sub_k = max(2, min(split_max_k, len(parent.members) // 4))
            if sub_k < 2 or sub_k >= sub_x.shape[0]:
                continue
            try:
                sub_model, sub_w = _fit_nmf(sub_x, sub_k)
            except Exception:
                continue
            sub_coherence = npmi_coherence(sub_x, sub_model.components_)
            # Only keep a split that actually finds coherent substructure.
            if sub_coherence < 0.0:
                parent.diagnostics["split"] = {"rejected": True, "npmi": sub_coherence}
                continue
            sub_labels, sub_terms = frex_labels(sub_model.components_, terms)
            sub_dominant = sub_w.argmax(axis=1)
            parent.diagnostics["split"] = {"rejected": False, "npmi": sub_coherence, "k": sub_k}
            for s in range(sub_k):
                members = [parent.members[i] for i in range(len(parent.members)) if int(sub_dominant[i]) == s]
                if not members:
                    continue
                topic_id = f"{parent.topic_id}.{s}"
                nodes.append(TopicNode(topic_id, parent.topic_id, 1, sub_labels[s], sub_terms[s], members))
                for i in members:
                    assignment[i] = topic_id

    diagnostics = {
        "selected_k": selection.k,
        "selection_reason": selection.reason,
        "candidates": selection.candidates,
        "vocabulary": int(x.shape[1]),
        "documents": len(docs),
        "npmi": npmi_coherence(x, model.components_),
        "exclusivity": exclusivity(model.components_),
        "stability": topic_stability(x, selection.k),
        "levels": 1 + int(any(n.depth == 1 for n in nodes)),
    }
    return TopicModel(facet, nodes, vectorizer, model.components_, shares, assignment, diagnostics)
