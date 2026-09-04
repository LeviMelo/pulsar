"""Semantic representations: things that induce a document geometry.

Every class here answers the same question — *given a text, where does it sit in
semantic space* — and therefore supports both query→document retrieval and
document↔document structure (maps, clustering, nearest neighbours).

This is deliberately separate from `retrieval.py`. BM25F answers relevance but
has no embedding; forcing it into the same interface is what produced the old
"average five scores into one mystical number" design.

All models are fitted **once on the whole corpus** and then used to transform
arbitrary text. A candidate's score therefore never changes because a different
candidate was filtered out.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
import re
from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence, runtime_checkable

import numpy as np
from scipy import sparse
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from .normalize import PORTUGUESE_STOP_WORDS, TOKEN_PATTERN, clean_text, fold


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


@runtime_checkable
class Representation(Protocol):
    """A fitted model mapping text to L2-normalized vectors."""

    name: str

    def encode(self, texts: Sequence[str]) -> np.ndarray | sparse.csr_matrix:
        """Row-normalized vectors for arbitrary texts."""

    @property
    def dimension(self) -> int: ...

    def signature(self) -> dict[str, object]:
        """Everything that identifies this fitted model, for provenance hashing."""


def cosine(a: np.ndarray | sparse.csr_matrix, b: np.ndarray | sparse.csr_matrix) -> np.ndarray:
    """Cosine similarity between already-normalized row matrices."""
    if sparse.issparse(a) or sparse.issparse(b):
        out = (a @ b.T)
        return np.asarray(out.todense() if sparse.issparse(out) else out, dtype=float)
    return np.asarray(a @ b.T, dtype=float)


def _sign_stabilize(components: np.ndarray) -> np.ndarray:
    """Fix arbitrary SVD sign flips so two runs are byte-identical."""
    for i in range(components.shape[0]):
        row = components[i]
        j = int(np.argmax(np.abs(row)))
        if row[j] < 0:
            components[i] = -row
    return components


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_+.#/-]*")


def _tokens(text: str) -> list[str]:
    """Fold, split, and strip trailing punctuation.

    Keeping ``sia/sus`` and ``c++`` intact while dropping the sentence-final dot
    matters: otherwise ``espacial`` and ``espacial.`` become two unrelated terms
    and the distributional model wastes probability mass rediscovering them.
    """
    out: list[str] = []
    for raw in _TOKEN_RE.findall(fold(text)):
        token = raw.rstrip("._-/")
        if len(token) > 1:
            out.append(token)
    return out


# ---------------------------------------------------------------------------
# Lexical (word / character TF-IDF)
# ---------------------------------------------------------------------------


class LexicalRepresentation:
    """Sparse TF-IDF. Precise, literal, and the strongest channel for exact recall.

    ``analyzer='word'`` matches vocabulary; ``'char_wb'`` matches morphology and
    survives Portuguese inflection and typos. They are correlated but not
    identical, so PULSAR keeps them as separate named channels instead of
    averaging them into a single "lexical" score.
    """

    def __init__(
        self,
        *,
        analyzer: str = "word",
        max_features: int = 40000,
        min_df: int = 1,
        name: str | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.max_features = int(max_features)
        self.min_df = int(min_df)
        self.name = name or f"tfidf_{analyzer}"
        if analyzer == "word":
            self.vectorizer = TfidfVectorizer(
                lowercase=True, strip_accents="unicode", ngram_range=(1, 2),
                max_features=self.max_features, sublinear_tf=True, min_df=self.min_df,
                stop_words=list(PORTUGUESE_STOP_WORDS), token_pattern=TOKEN_PATTERN,
            )
        else:
            self.vectorizer = TfidfVectorizer(
                analyzer="char_wb", lowercase=True, strip_accents="unicode",
                ngram_range=(3, 5), max_features=self.max_features, sublinear_tf=True,
                min_df=self.min_df,
            )
        self._fitted = False

    def fit(self, documents: Sequence[str]) -> "LexicalRepresentation":
        docs = [clean_text(d) or "vazio" for d in documents]
        self.vectorizer.fit(docs)
        self._fitted = True
        return self

    def encode(self, texts: Sequence[str]) -> sparse.csr_matrix:
        if not self._fitted:
            raise RuntimeError(f"{self.name} is not fitted")
        docs = [clean_text(t) or "vazio" for t in texts]
        return normalize(self.vectorizer.transform(docs).tocsr(), norm="l2", copy=False)

    @property
    def dimension(self) -> int:
        return len(self.vectorizer.vocabulary_) if self._fitted else 0

    def signature(self) -> dict[str, object]:
        return {"kind": "tfidf", "analyzer": self.analyzer, "max_features": self.max_features,
                "min_df": self.min_df, "dimension": self.dimension}


# ---------------------------------------------------------------------------
# LSA (truncated SVD of the TF-IDF document-term matrix)
# ---------------------------------------------------------------------------


class LSARepresentation:
    """Classical latent semantic analysis: ``X ≈ U_k Σ_k V_kᵀ``.

    The right singular vectors diagonalize the term-term covariance induced by
    corpus usage, so terms that co-occur across documents collapse onto shared
    latent directions. That is why LSA beats literal TF-IDF on *thematic*
    affinity (our sibling-plan task) while losing on *literal* recall.

    Fitted on the full atom corpus rather than the 187 opportunities: latent
    quality is bounded by how much domain language the training matrix contains.
    """

    def __init__(self, *, n_components: int = 128, base: LexicalRepresentation | None = None,
                 name: str | None = None) -> None:
        self.n_components = int(n_components)
        self.base = base or LexicalRepresentation(analyzer="word", min_df=2)
        self.name = name or f"lsa{self.n_components}"
        self.svd: TruncatedSVD | None = None
        self._scale: np.ndarray | None = None

    def fit(self, documents: Sequence[str], *, base_fitted: bool = False) -> "LSARepresentation":
        if not base_fitted:
            self.base.fit(documents)
        x = self.base.encode(documents)
        k = max(2, min(self.n_components, min(x.shape) - 1))
        self.svd = TruncatedSVD(n_components=k, random_state=0, algorithm="randomized", n_iter=12)
        self.svd.fit(x)
        self.svd.components_ = _sign_stabilize(self.svd.components_)
        # Whitening by singular value is optional; keeping Σ preserves the
        # relative importance of strong latent directions, which benchmarks
        # consistently prefer over full whitening at this corpus size.
        self._scale = np.ones(k)
        return self

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if self.svd is None:
            raise RuntimeError(f"{self.name} is not fitted")
        x = self.base.encode(texts)
        latent = np.asarray(x @ self.svd.components_.T, dtype=float) * self._scale
        return normalize(latent, norm="l2")

    @property
    def dimension(self) -> int:
        return int(self.svd.n_components) if self.svd is not None else 0

    @property
    def explained_variance(self) -> float:
        return float(self.svd.explained_variance_ratio_.sum()) if self.svd is not None else 0.0

    def signature(self) -> dict[str, object]:
        return {"kind": "lsa", "n_components": self.dimension, "base": self.base.signature()}


# ---------------------------------------------------------------------------
# PPMI / SPPMI + SVD  (corpus-native distributional term semantics)
# ---------------------------------------------------------------------------


class PPMIRepresentation:
    """Shifted positive PMI over term-context counts, factorized by SVD.

    LSA asks "which terms appear in similar *documents*". PPMI asks the sharper
    question "which terms appear in similar *lexical neighbourhoods*", which is
    what actually ties ``geoprocessamento`` to ``análise espacial``, ``município``
    and ``Moran``. Levy & Goldberg showed SGNS implicitly factorizes shifted PMI,
    so this is a deterministic, inspectable relative of word2vec — no sampling,
    no training schedule, identical output every run.

    Document vectors are SIF-weighted averages of term vectors: rare terms carry
    the signal, and the common-component removal strips the "academic Portuguese"
    direction that otherwise dominates every document.
    """

    def __init__(
        self,
        *,
        n_components: int = 200,
        window: int = 6,
        min_count: int = 4,
        max_vocab: int = 60000,
        shift: float = 1.0,
        context_smoothing: float = 0.75,
        eigenvalue_power: float = 0.5,
        sif_a: float = 1e-3,
        remove_common: int = 1,
        name: str = "ppmi",
    ) -> None:
        self.n_components = int(n_components)
        self.window = int(window)
        self.min_count = int(min_count)
        self.max_vocab = int(max_vocab)
        self.shift = float(shift)
        self.context_smoothing = float(context_smoothing)
        self.eigenvalue_power = float(eigenvalue_power)
        self.sif_a = float(sif_a)
        self.remove_common = int(remove_common)
        self.name = name
        self.vocab: dict[str, int] = {}
        self.term_vectors: np.ndarray | None = None
        self.term_weight: np.ndarray | None = None
        self._common: np.ndarray | None = None

    # -- fitting -------------------------------------------------------------

    def _build_vocab(self, tokenized: Sequence[Sequence[str]]) -> None:
        counts: Counter[str] = Counter()
        for toks in tokenized:
            counts.update(t for t in toks if t not in PORTUGUESE_STOP_WORDS and len(t) > 2)
        kept = [(t, c) for t, c in counts.items() if c >= self.min_count]
        kept.sort(key=lambda kv: (-kv[1], kv[0]))
        kept = kept[: self.max_vocab]
        self.vocab = {t: i for i, (t, _) in enumerate(kept)}
        total = float(sum(c for _, c in kept)) or 1.0
        freq = np.array([c for _, c in kept], dtype=float) / total
        # Smooth inverse frequency (Arora et al.): a / (a + p(w)).
        self.term_weight = self.sif_a / (self.sif_a + freq)
        self._freq = freq

    def _cooccurrence(self, tokenized: Sequence[Sequence[str]]) -> sparse.csr_matrix:
        v = len(self.vocab)
        rows: list[int] = []
        cols: list[int] = []
        vals: list[float] = []
        for toks in tokenized:
            ids = [self.vocab[t] for t in toks if t in self.vocab]
            n = len(ids)
            for i, wi in enumerate(ids):
                lo = max(0, i - self.window)
                hi = min(n, i + self.window + 1)
                for j in range(lo, hi):
                    if j == i:
                        continue
                    # Harmonic distance weighting: adjacent words are stronger
                    # evidence of a shared concept than window-edge words.
                    rows.append(wi)
                    cols.append(ids[j])
                    vals.append(1.0 / abs(i - j))
        if not rows:
            return sparse.csr_matrix((v, v))
        return sparse.coo_matrix((vals, (rows, cols)), shape=(v, v)).tocsr()

    def fit(self, documents: Sequence[str]) -> "PPMIRepresentation":
        tokenized = [_tokens(d) for d in documents]
        self._build_vocab(tokenized)
        if not self.vocab:
            self.term_vectors = np.zeros((0, self.n_components))
            return self
        c = self._cooccurrence(tokenized)
        total = float(c.sum()) or 1.0
        row_sum = np.asarray(c.sum(axis=1)).ravel()
        col_sum = np.asarray(c.sum(axis=0)).ravel()
        # Context distribution smoothing (α=0.75) damps the PMI bias toward rare
        # contexts, which is the single largest practical fix to raw PPMI.
        col_smooth = np.power(np.maximum(col_sum, 0.0), self.context_smoothing)
        col_smooth_total = float(col_smooth.sum()) or 1.0

        c = c.tocoo()
        p_wc = c.data / total
        p_w = np.maximum(row_sum[c.row] / total, 1e-12)
        p_c = np.maximum(col_smooth[c.col] / col_smooth_total, 1e-12)
        pmi = np.log(p_wc / (p_w * p_c)) - math.log(max(self.shift, 1e-12))
        keep = pmi > 0
        sppmi = sparse.coo_matrix(
            (pmi[keep], (c.row[keep], c.col[keep])), shape=c.shape
        ).tocsr()

        k = max(2, min(self.n_components, min(sppmi.shape) - 1))
        svd = TruncatedSVD(n_components=k, random_state=0, algorithm="randomized", n_iter=10)
        u = svd.fit_transform(sppmi)
        svd.components_ = _sign_stabilize(svd.components_)
        sigma = np.maximum(svd.singular_values_, 1e-12)
        # W = U Σ^p. p=0.5 (symmetric) is the standard choice and empirically
        # beats p=1 for similarity tasks.
        self.term_vectors = normalize(u / sigma[None, :] * np.power(sigma, self.eigenvalue_power)[None, :], norm="l2")
        self._fit_common(documents)
        return self

    def _fit_common(self, documents: Sequence[str]) -> None:
        if not self.remove_common:
            self._common = None
            return
        vecs = self._raw_encode(documents)
        if vecs.shape[0] < 4:
            self._common = None
            return
        svd = TruncatedSVD(n_components=min(self.remove_common, min(vecs.shape) - 1),
                           random_state=0, algorithm="randomized")
        svd.fit(vecs)
        self._common = _sign_stabilize(svd.components_)

    # -- encoding ------------------------------------------------------------

    def _raw_encode(self, texts: Sequence[str]) -> np.ndarray:
        if self.term_vectors is None or not len(self.vocab):
            return np.zeros((len(texts), max(self.n_components, 1)))
        dim = self.term_vectors.shape[1]
        out = np.zeros((len(texts), dim), dtype=float)
        for i, text in enumerate(texts):
            acc = np.zeros(dim)
            weight_sum = 0.0
            for tok in _tokens(text):
                idx = self.vocab.get(tok)
                if idx is None:
                    continue
                w = float(self.term_weight[idx])
                acc += w * self.term_vectors[idx]
                weight_sum += w
            if weight_sum > 0:
                out[i] = acc / weight_sum
        return out

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        vecs = self._raw_encode(texts)
        if self._common is not None and vecs.size:
            vecs = vecs - (vecs @ self._common.T) @ self._common
        return normalize(vecs, norm="l2")

    @property
    def dimension(self) -> int:
        return int(self.term_vectors.shape[1]) if self.term_vectors is not None else 0

    def nearest_terms(self, term: str, k: int = 10) -> list[tuple[str, float]]:
        """Inspect the learned term geometry. Invaluable for sanity-checking."""
        if self.term_vectors is None:
            return []
        idx = self.vocab.get(fold(term))
        if idx is None:
            return []
        sims = self.term_vectors @ self.term_vectors[idx]
        order = np.argsort(-sims)[: k + 1]
        inverse = {i: t for t, i in self.vocab.items()}
        return [(inverse[int(i)], float(sims[int(i)])) for i in order if int(i) != idx][:k]

    def signature(self) -> dict[str, object]:
        return {"kind": "sppmi_svd", "n_components": self.dimension, "window": self.window,
                "min_count": self.min_count, "shift": self.shift,
                "context_smoothing": self.context_smoothing,
                "eigenvalue_power": self.eigenvalue_power, "remove_common": self.remove_common,
                "vocabulary": len(self.vocab)}


# ---------------------------------------------------------------------------
# Pretrained neural embeddings
# ---------------------------------------------------------------------------


class NeuralRepresentation:
    """Wraps an :class:`~.embedding.EmbeddingProvider` as a Representation.

    Pretrained models know medical synonymy the local corpus is far too small to
    infer. They are *added to* the ensemble, never assumed to replace it: MTEB
    leadership is not evidence about Portuguese biomedical UFAL work plans.
    """

    def __init__(self, provider, *, dimension: int | None = None, instruction: str | None = None,
                 name: str | None = None) -> None:
        self.provider = provider
        self.truncate_to = dimension
        self.instruction = instruction
        self.name = name or f"neural_{provider.model}"
        self._dim = 0

    def fit(self, documents: Sequence[str]) -> "NeuralRepresentation":  # nothing to fit
        self._dim = self.truncate_to or self.provider.dimension
        return self

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        vecs = self.provider.embed([self._prepare(t) for t in texts])
        if self.truncate_to and vecs.shape[1] > self.truncate_to:
            # Matryoshka truncation: leading dimensions carry the coarse signal.
            vecs = vecs[:, : self.truncate_to]
        self._dim = vecs.shape[1]
        return normalize(vecs, norm="l2")

    def _prepare(self, text: str) -> str:
        text = clean_text(text) or "pesquisa"
        return f"Instruct: {self.instruction}\nQuery: {text}" if self.instruction else text

    @property
    def dimension(self) -> int:
        return self._dim or (self.truncate_to or self.provider.dimension)

    def signature(self) -> dict[str, object]:
        return {"kind": "neural", **self.provider.identity(), "truncate_to": self.truncate_to,
                "instruction": self.instruction or ""}


# ---------------------------------------------------------------------------
# Multi-view fusion
# ---------------------------------------------------------------------------


@dataclass
class ViewSpec:
    representation: object
    weight: float = 1.0


class BlockFusion:
    """Weighted concatenation of L2-normalized views.

    Because each block is unit-norm, ``cos(z_a, z_b)`` is exactly the
    weight-normalized average of per-view cosines — a genuine joint metric
    space, not an arbitrary score blend, and trivially explainable.
    """

    def __init__(self, views: dict[str, ViewSpec], *, name: str = "block") -> None:
        self.views = views
        self.name = name

    def fit(self, documents: Sequence[str]) -> "BlockFusion":
        return self

    def _normalized_weights(self) -> dict[str, float]:
        total = sum(max(v.weight, 0.0) for v in self.views.values()) or 1.0
        return {k: max(v.weight, 0.0) / total for k, v in self.views.items()}

    def stack(self, texts: Sequence[str]):
        """The joint matrix, sparse whenever any view is sparse."""
        weights = self._normalized_weights()
        blocks = []
        any_sparse = False
        for key, spec in self.views.items():
            vec = spec.representation.encode(texts)
            scale = math.sqrt(weights[key])
            if sparse.issparse(vec):
                any_sparse = True
                blocks.append(vec.multiply(scale).tocsr())
            else:
                blocks.append(np.asarray(vec, dtype=float) * scale)
        if any_sparse:
            joint = sparse.hstack([b if sparse.issparse(b) else sparse.csr_matrix(b)
                                   for b in blocks], format="csr")
        else:
            joint = np.hstack(blocks)
        return normalize(joint, norm="l2")

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """Materialize the joint vectors as a dense array.

        Densifying a sparse lexical view costs ~10^5 columns per row, so this is
        for geometry over a bounded entity set (projects, opportunities), not for
        scoring the whole corpus. Use :meth:`similarity` for scoring — it needs
        no joint matrix at all.
        """
        joint = self.stack(texts)
        return np.asarray(joint.todense()) if sparse.issparse(joint) else joint

    def similarity(self, queries: Sequence[str], documents: Sequence[str]) -> np.ndarray:
        """Fused cosine, computed per view and averaged.

        Identical to ``cosine(encode(queries), encode(documents))`` because each
        block is unit-norm — but it never forms the concatenation, so a sparse
        lexical view costs its own sparse dot product instead of a dense matrix
        with 10^5 columns. This is also the fusion's explanation made executable:
        the fused score *is* the weighted mean of the per-view cosines.
        """
        weights = self._normalized_weights()
        out: np.ndarray | None = None
        for key, spec in self.views.items():
            weight = weights[key]
            if weight <= 0.0:
                continue
            rep = spec.representation
            part = cosine(rep.encode(queries), rep.encode(documents)) * weight
            out = part if out is None else out + part
        if out is None:
            raise ValueError("block fusion has no positively weighted view")
        return out

    @property
    def dimension(self) -> int:
        return sum(v.representation.dimension for v in self.views.values())

    def signature(self) -> dict[str, object]:
        return {"kind": "block_fusion",
                "views": {k: {"weight": v.weight, **v.representation.signature()} for k, v in self.views.items()}}


class JointSVDFusion:
    """Consensus embedding: concatenate whitened views, then re-factorize.

    ``X_joint = [α·X₁ | β·X₂ | …] ≈ U_r Σ_r V_rᵀ``. Directions on which the views
    *agree* accumulate variance and survive truncation; view-specific noise does
    not. The cost is that view-specific *signal* is also discarded, which is why
    PULSAR benchmarks this against plain block concatenation rather than assuming
    consensus is better.
    """

    def __init__(self, views: dict[str, ViewSpec], *, n_components: int = 160, name: str = "joint_svd") -> None:
        self.views = views
        self.n_components = int(n_components)
        self.name = name
        self.svd: TruncatedSVD | None = None
        self._block = BlockFusion(views, name=name + "_block")

    def fit(self, documents: Sequence[str]) -> "JointSVDFusion":
        x = self._block.stack(documents)  # sparse-preserving; TruncatedSVD handles both
        k = max(2, min(self.n_components, min(x.shape) - 1))
        self.svd = TruncatedSVD(n_components=k, random_state=0, algorithm="randomized", n_iter=10)
        self.svd.fit(x)
        self.svd.components_ = _sign_stabilize(self.svd.components_)
        return self

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if self.svd is None:
            raise RuntimeError("joint SVD is not fitted")
        return normalize(self._block.stack(texts) @ self.svd.components_.T, norm="l2")

    @property
    def dimension(self) -> int:
        return int(self.svd.n_components) if self.svd is not None else 0

    def signature(self) -> dict[str, object]:
        return {"kind": "joint_svd", "n_components": self.dimension, **self._block.signature()}


def representation_hash(rep) -> str:
    import json
    payload = json.dumps(rep.signature(), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
