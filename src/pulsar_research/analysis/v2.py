from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from scipy import sparse
from sklearn.cluster import AgglomerativeClustering
from sklearn.decomposition import NMF
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.metrics import silhouette_score
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize

from .text import PORTUGUESE_STOP_WORDS, TOPIC_STOP_WORDS, clean_text


# ---------------------------------------------------------------------------
# Normalization / views
# ---------------------------------------------------------------------------

_CITATION_PAREN = re.compile(r"\([^()]{0,140}\b(?:19|20)\d{2}[a-z]?\b[^()]{0,140}\)", re.I)
_CITATION_ETAL = re.compile(
    r"\b[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]+(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]+){0,3}\s+et\s+al\.?\s*,?\s*\(?\s*(?:19|20)\d{2}[a-z]?\s*\)?",
    re.I,
)
_CITATION_BRACKET = re.compile(r"\[(?:\s*\d+\s*[,;]?\s*)+\]")
_YEAR_AUTHOR = re.compile(r"\b[A-ZÀ-Ý]{2,}(?:\s+[A-ZÀ-Ý]{2,}){0,3}\s*,?\s*(?:19|20)\d{2}[a-z]?\b")

# Frequent academic scaffolding that is useful in prose but actively harms
# unsupervised topic discovery. Retrieval keeps most of it; topic views do not.
V2_TOPIC_BOILERPLATE = frozenset(
    set(TOPIC_STOP_WORDS)
    | {
        "serao", "sera", "realizado", "realizada", "realizados", "realizadas",
        "estudo", "estudos", "dados", "analise", "avaliacao", "resultados",
        "desenvolvimento", "desenvolver", "realizar", "avaliar", "investigar",
        "cientifico", "cientifica", "cientificos", "cientificas", "estudante",
        "aluno", "aluna", "et", "al", "medicina", "ciencias",
        "referencias", "bibliograficas", "bibliografica", "disponivel",
    }
)

SPACE_TOPIC_BOILERPLATE = {
    "domain": frozenset(),
    "methods": frozenset({
        "participara", "participar", "participacao", "tarefas", "tarefa", "enumeradas",
        "enumerada", "seguir", "descrita", "descrito", "elaboracao", "relatorios", "relatorio",
        "etapas", "etapa", "procedimentos", "procedimento",
    }),
    "skills": frozenset({
        "desenvolvera", "desenvolver", "competencias", "competencia", "bolsista", "tera",
        "acesso", "ira", "aprender", "aprendizado", "formacao", "profissional", "graduacao",
        "pos-graduacao", "ingresso", "apresentacao", "academicos", "academico", "ambito",
        "oportunidade", "proporcionara", "permitira", "capacitacao",
    }),
}


def scrub_citations(text: str) -> str:
    """Remove citation syntax from an analytical *view* without touching raw data."""
    text = clean_text(text)
    text = _CITATION_PAREN.sub(" ", text)
    text = _CITATION_ETAL.sub(" ", text)
    text = _CITATION_BRACKET.sub(" ", text)
    text = _YEAR_AUTHOR.sub(" ", text)
    return clean_text(text)


def _join_unique(parts: Iterable[str], *, limit: int | None = None) -> str:
    out: list[str] = []
    seen: set[str] = set()
    size = 0
    for value in parts:
        value = clean_text(str(value or ""))
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        if limit is not None and size + len(value) > limit:
            remaining = max(0, limit - size)
            if remaining:
                out.append(value[:remaining])
            break
        out.append(value)
        size += len(value) + 1
    return "\n".join(out)


def opportunity_views(row: Mapping[str, Any], *, topic_clean: bool = False) -> dict[str, str]:
    """Build field-aware semantic views for one opportunity.

    We intentionally do not concatenate arbitrary field labels. This avoids
    synthetic cross-boundary bigrams such as ``saude medicina``.
    """
    cleaner = scrub_citations if topic_clean else clean_text
    title = _join_unique([row.get("project_title", ""), row.get("plan_title", "")])
    area = _join_unique([row.get("large_area", ""), row.get("area", "")])
    intro = cleaner(str(row.get("introduction_justification") or ""))
    objectives = cleaner(str(row.get("objectives") or ""))
    methodology = cleaner(str(row.get("methodology") or ""))
    skills = cleaner(str(row.get("acquired_skills") or ""))

    return {
        "domain_title": clean_text(title),
        "domain_area": clean_text(area),
        "domain_intro": intro,
        "domain_objectives": objectives,
        "method_title": clean_text(row.get("plan_title") or row.get("project_title") or ""),
        "method_objectives": objectives,
        "method_methodology": methodology,
        "skill_title": clean_text(row.get("plan_title") or ""),
        "skill_skills": skills,
        "skill_methodology": methodology,
    }


def project_records(opportunities: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate sibling work plans into one independent project document."""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in opportunities:
        key = str(row.get("project_code") or row.get("project_title") or row.get("id_opportunity") or "")
        grouped.setdefault(key, []).append(row)

    projects: list[dict[str, Any]] = []
    for key in sorted(grouped):
        rows = grouped[key]
        first = rows[0]
        rec: dict[str, Any] = {
            "project_code": str(first.get("project_code") or key),
            "project_title": clean_text(str(first.get("project_title") or "")),
            "professor_siape": str(first.get("professor_siape") or ""),
            "professor_name": clean_text(str(first.get("professor_name") or "")),
            "opportunity_ids": [str(r.get("id_opportunity") or "") for r in rows],
        }
        rec["domain"] = _join_unique(
            [first.get("project_title", "")]
            + [r.get("plan_title", "") for r in rows]
            + [r.get("large_area", "") for r in rows]
            + [r.get("area", "") for r in rows]
            + [scrub_citations(str(r.get("introduction_justification") or "")) for r in rows]
            + [scrub_citations(str(r.get("objectives") or "")) for r in rows],
            limit=50000,
        )
        rec["methods"] = _join_unique(
            [r.get("plan_title", "") for r in rows]
            + [scrub_citations(str(r.get("methodology") or "")) for r in rows],
            limit=50000,
        )
        rec["skills"] = _join_unique(
            [scrub_citations(str(r.get("acquired_skills") or "")) for r in rows],
            limit=30000,
        )
        projects.append(rec)
    return projects


def profile_queries(profile: Mapping[str, Any]) -> dict[str, str]:
    fallback = clean_text(str(profile.get("semantic_profile") or "pesquisa científica saúde"))

    def section(*names: str) -> str:
        values: list[str] = []
        for name in names:
            raw = profile.get(name)
            if isinstance(raw, str):
                values.append(raw)
            elif isinstance(raw, (list, tuple)):
                values.extend(str(x) for x in raw if x)
        return clean_text("; ".join(values)) or fallback

    return {
        "domain": section("domain_interests", "domains", "research_interests"),
        "methods": section("method_interests", "methods", "methodology_interests"),
        "skills": section("technical_skills", "skills", "competencies"),
        "fallback": fallback,
    }


# ---------------------------------------------------------------------------
# Deterministic sparse semantic spaces
# ---------------------------------------------------------------------------


def _safe_normalize_rows(x: sparse.spmatrix) -> sparse.csr_matrix:
    return normalize(x.tocsr(), norm="l2", axis=1, copy=False)


def _exact_lsa_from_sparse(x: sparse.csr_matrix, n_components: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Exact document LSA using eigendecomposition of X X^T.

    Returns ``doc_latent, U, singular_values``. Because the number of PULSAR
    documents is small, exact linear algebra is cheaper and more reproducible
    than randomized SVD.
    """
    n = x.shape[0]
    if n == 0:
        return np.empty((0, 0)), np.empty((0, 0)), np.empty((0,))
    kernel = np.asarray((x @ x.T).todense(), dtype=float)
    kernel = (kernel + kernel.T) / 2.0
    vals, vecs = np.linalg.eigh(kernel)
    order = np.argsort(vals)[::-1]
    vals = np.clip(vals[order], 0.0, None)
    vecs = vecs[:, order]
    keep = vals > 1e-10
    vals = vals[keep]
    vecs = vecs[:, keep]
    k = max(1, min(int(n_components), len(vals))) if len(vals) else 0
    if k == 0:
        return np.zeros((n, 1)), np.zeros((n, 1)), np.ones(1)
    vals = vals[:k]
    vecs = vecs[:, :k]
    singular = np.sqrt(vals)
    latent = vecs * singular[None, :]
    return latent, vecs, singular


def _latent_query(q: sparse.csr_matrix, x: sparse.csr_matrix, u: np.ndarray, s: np.ndarray) -> np.ndarray:
    if not len(s):
        return np.zeros((1, 1))
    cross = np.asarray((q @ x.T).todense(), dtype=float)
    return (cross @ u) / np.where(s > 1e-12, s, 1.0)[None, :]


@dataclass
class SparseScores:
    word: np.ndarray
    char: np.ndarray
    lsa: np.ndarray


class FixedSparseSpace:
    """A query-independent, deterministic sparse lexical + LSA space."""

    def __init__(
        self,
        *,
        word_max_features: int = 24000,
        char_max_features: int = 24000,
        lsa_components: int = 48,
        min_df: int = 1,
    ) -> None:
        self.word = TfidfVectorizer(
            lowercase=True,
            strip_accents="unicode",
            ngram_range=(1, 2),
            max_features=word_max_features,
            sublinear_tf=True,
            min_df=min_df,
            stop_words=list(PORTUGUESE_STOP_WORDS),
            token_pattern=r"(?u)\b[a-zA-ZÀ-ÿ][a-zA-ZÀ-ÿ0-9_+.#/-]+\b",
        )
        self.char = TfidfVectorizer(
            analyzer="char_wb",
            lowercase=True,
            strip_accents="unicode",
            ngram_range=(3, 5),
            max_features=char_max_features,
            sublinear_tf=True,
            min_df=min_df,
        )
        self.lsa_components = int(lsa_components)
        self.documents: list[str] = []
        self.x_word: sparse.csr_matrix | None = None
        self.x_char: sparse.csr_matrix | None = None
        self.x_combined: sparse.csr_matrix | None = None
        self.doc_latent: np.ndarray | None = None
        self.u: np.ndarray | None = None
        self.s: np.ndarray | None = None

    def fit(self, documents: Sequence[str]) -> "FixedSparseSpace":
        self.documents = [clean_text(str(x or "")) or "vazio" for x in documents]
        self.x_word = _safe_normalize_rows(self.word.fit_transform(self.documents))
        self.x_char = _safe_normalize_rows(self.char.fit_transform(self.documents))
        self.x_combined = _safe_normalize_rows(sparse.hstack([self.x_word, self.x_char], format="csr"))
        self.doc_latent, self.u, self.s = _exact_lsa_from_sparse(self.x_combined, self.lsa_components)
        return self

    def score(self, query: str) -> SparseScores:
        if self.x_word is None or self.x_char is None or self.x_combined is None:
            raise RuntimeError("space is not fitted")
        query = clean_text(query) or "pesquisa"
        qw = _safe_normalize_rows(self.word.transform([query]))
        qc = _safe_normalize_rows(self.char.transform([query]))
        qx = _safe_normalize_rows(sparse.hstack([qw, qc], format="csr"))
        word = cosine_similarity(self.x_word, qw).ravel()
        char = cosine_similarity(self.x_char, qc).ravel()
        qlatent = _latent_query(qx, self.x_combined, self.u, self.s)
        dl = normalize(np.asarray(self.doc_latent), norm="l2")
        ql = normalize(np.asarray(qlatent), norm="l2")
        lsa = np.clip(cosine_similarity(dl, ql).ravel(), 0.0, 1.0)
        return SparseScores(word=word, char=char, lsa=lsa)

    def cosine_matrix(self) -> np.ndarray:
        if self.x_combined is None:
            raise RuntimeError("space is not fitted")
        k = np.asarray((self.x_combined @ self.x_combined.T).todense(), dtype=float)
        k = np.clip((k + k.T) / 2.0, -1.0, 1.0)
        np.fill_diagonal(k, 1.0)
        return k


# ---------------------------------------------------------------------------
# BM25F and rank fusion
# ---------------------------------------------------------------------------


class BM25FIndex:
    def __init__(
        self,
        field_weights: Mapping[str, float],
        *,
        k1: float = 1.5,
        b: float = 0.75,
        max_features: int = 30000,
    ) -> None:
        self.field_weights = dict(field_weights)
        self.k1 = float(k1)
        self.b = float(b)
        self.vectorizer = CountVectorizer(
            lowercase=True,
            strip_accents="unicode",
            ngram_range=(1, 2),
            max_features=max_features,
            min_df=1,
            stop_words=list(PORTUGUESE_STOP_WORDS),
            token_pattern=r"(?u)\b[a-zA-ZÀ-ÿ][a-zA-ZÀ-ÿ0-9_+.#/-]+\b",
        )
        self.mats: dict[str, sparse.csr_matrix] = {}
        self.lengths: dict[str, np.ndarray] = {}
        self.avg_lengths: dict[str, float] = {}
        self.idf: np.ndarray | None = None
        self.n_docs = 0

    def fit(self, records: Sequence[Mapping[str, str]]) -> "BM25FIndex":
        self.n_docs = len(records)
        fit_text = [clean_text(str(rec.get(f, "") or "")) for rec in records for f in self.field_weights]
        if not any(fit_text):
            fit_text = ["vazio"]
        self.vectorizer.fit(fit_text)
        presence: sparse.csr_matrix | None = None
        for field in self.field_weights:
            mat = self.vectorizer.transform([clean_text(str(rec.get(field, "") or "")) for rec in records]).tocsr()
            self.mats[field] = mat
            lens = np.asarray(mat.sum(axis=1)).ravel().astype(float)
            self.lengths[field] = lens
            self.avg_lengths[field] = max(float(lens.mean()) if len(lens) else 1.0, 1e-9)
            p = (mat > 0).astype(np.int8)
            presence = p if presence is None else ((presence + p) > 0).astype(np.int8)
        if presence is None:
            self.idf = np.zeros(len(self.vectorizer.get_feature_names_out()))
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


def _ordinal_ranks_desc(values: np.ndarray) -> np.ndarray:
    values = np.nan_to_num(np.asarray(values, dtype=float), nan=-np.inf)
    order = np.argsort(-values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(1, len(values) + 1, dtype=float)
    return ranks


def rrf(scores: Sequence[np.ndarray], *, weights: Sequence[float] | None = None, k: int = 60) -> np.ndarray:
    if not scores:
        return np.array([], dtype=float)
    weights = list(weights or [1.0] * len(scores))
    if len(weights) != len(scores):
        raise ValueError("weights and scores length differ")
    out = np.zeros(len(scores[0]), dtype=float)
    theoretical = 0.0
    for values, weight in zip(scores, weights):
        ranks = _ordinal_ranks_desc(np.asarray(values))
        out += float(weight) / (float(k) + ranks)
        theoretical += float(weight) / (float(k) + 1.0)
    return out / theoretical if theoretical > 0 else out


@dataclass
class FacetResult:
    word: np.ndarray
    char: np.ndarray
    lsa: np.ndarray
    bm25f: np.ndarray
    fit: np.ndarray


class FacetIndex:
    def __init__(
        self,
        records: Sequence[Mapping[str, str]],
        text_fields: Sequence[str],
        bm25_fields: Mapping[str, float],
        *,
        word_max_features: int = 24000,
        char_max_features: int = 24000,
        lsa_components: int = 48,
        rrf_k: int = 60,
    ) -> None:
        self.records = list(records)
        self.text_fields = list(text_fields)
        self.rrf_k = int(rrf_k)
        docs = [_join_unique([str(rec.get(f, "") or "") for f in self.text_fields]) for rec in self.records]
        self.space = FixedSparseSpace(
            word_max_features=word_max_features,
            char_max_features=char_max_features,
            lsa_components=lsa_components,
        ).fit(docs)
        self.bm25 = BM25FIndex(bm25_fields, max_features=word_max_features).fit(self.records)

    def score(self, query: str) -> FacetResult:
        s = self.space.score(query)
        b = self.bm25.score(query)
        fit = rrf([s.word, s.char, s.lsa, b], weights=[1.0, 1.0, 0.75, 1.0], k=self.rrf_k)
        return FacetResult(word=s.word, char=s.char, lsa=s.lsa, bm25f=b, fit=fit)


@dataclass
class OpportunitySemanticResult:
    ids: list[str]
    domain: FacetResult
    methods: FacetResult
    skills: FacetResult
    combined: np.ndarray


def score_opportunities(
    rows: Sequence[Mapping[str, Any]],
    queries: Mapping[str, str],
    *,
    word_max_features: int = 24000,
    char_max_features: int = 24000,
    lsa_components: int = 48,
    rrf_k: int = 60,
) -> OpportunitySemanticResult:
    records = [opportunity_views(row, topic_clean=False) for row in rows]
    domain_idx = FacetIndex(
        records,
        ["domain_title", "domain_area", "domain_intro", "domain_objectives"],
        {"domain_title": 3.0, "domain_area": 1.2, "domain_intro": 0.65, "domain_objectives": 1.5},
        word_max_features=word_max_features,
        char_max_features=char_max_features,
        lsa_components=lsa_components,
        rrf_k=rrf_k,
    )
    method_idx = FacetIndex(
        records,
        ["method_title", "method_methodology"],
        {"method_title": 1.2, "method_methodology": 3.0},
        word_max_features=word_max_features,
        char_max_features=char_max_features,
        lsa_components=lsa_components,
        rrf_k=rrf_k,
    )
    skill_idx = FacetIndex(
        records,
        ["skill_title", "skill_skills"],
        {"skill_title": 0.6, "skill_skills": 3.5},
        word_max_features=word_max_features,
        char_max_features=char_max_features,
        lsa_components=lsa_components,
        rrf_k=rrf_k,
    )
    domain = domain_idx.score(queries.get("domain") or queries.get("fallback") or "pesquisa")
    methods = method_idx.score(queries.get("methods") or queries.get("fallback") or "pesquisa")
    skills = skill_idx.score(queries.get("skills") or queries.get("fallback") or "pesquisa")
    combined = rrf([domain.fit, methods.fit, skills.fit], weights=[1.0, 1.0, 1.0], k=rrf_k)
    return OpportunitySemanticResult(
        ids=[str(r.get("id_opportunity") or "") for r in rows],
        domain=domain,
        methods=methods,
        skills=skills,
        combined=combined,
    )


def score_ad_hoc_opportunities(
    rows: Sequence[Mapping[str, Any]],
    query: str,
    **kwargs: Any,
) -> OpportunitySemanticResult:
    q = clean_text(query) or "pesquisa"
    return score_opportunities(rows, {"domain": q, "methods": q, "skills": q, "fallback": q}, **kwargs)


# ---------------------------------------------------------------------------
# Topic discovery: one training document per project
# ---------------------------------------------------------------------------


@dataclass
class TopicSpaceResult:
    space: str
    n_topics: int
    project_weights: np.ndarray
    opportunity_weights: np.ndarray
    labels: list[str]
    top_terms: list[list[str]]
    diagnostics: dict[str, Any]


def _npmi_for_topics(x: sparse.csr_matrix, components: np.ndarray, top_n: int = 8) -> float:
    binary = (x > 0).astype(np.int8).tocsr()
    n = max(binary.shape[0], 1)
    dfs = np.asarray(binary.sum(axis=0)).ravel().astype(float)
    topic_scores: list[float] = []
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
                denom = -math.log(pij) if pij < 1 else 1.0
                pairs.append(pmi / denom)
        if pairs:
            topic_scores.append(float(np.mean(pairs)))
    return float(np.mean(topic_scores)) if topic_scores else -1.0


def _frex_like_terms(components: np.ndarray, terms: np.ndarray, top_n: int = 10) -> tuple[list[str], list[list[str]]]:
    colsum = components.sum(axis=0)
    labels: list[str] = []
    all_terms: list[list[str]] = []
    for k, comp in enumerate(components):
        maxv = max(float(comp.max(initial=0.0)), 1e-12)
        freq = comp / maxv
        exclusivity = np.divide(comp, colsum, out=np.zeros_like(comp), where=colsum > 0)
        score = freq * np.sqrt(exclusivity)
        idx = np.argsort(score)[::-1][:top_n]
        chosen = [str(terms[i]) for i in idx if score[i] > 0]
        if not chosen:
            chosen = ["sem tema"]
        all_terms.append(chosen)
        labels.append(" / ".join(chosen[:3]))
    return labels, all_terms


def _select_nmf(
    x: sparse.csr_matrix,
    *,
    min_k: int,
    max_k: int,
    min_support: int,
) -> tuple[NMF, np.ndarray, dict[str, Any]]:
    n_docs, n_terms = x.shape
    upper = max(1, min(max_k, n_docs - 1 if n_docs > 1 else 1, n_terms))
    lower = max(1, min(min_k, upper))
    candidates: list[dict[str, Any]] = []
    norm_x = float(sparse.linalg.norm(x)) or 1.0
    best: tuple[float, NMF, np.ndarray] | None = None

    for k in range(lower, upper + 1):
        model = NMF(
            n_components=k,
            init="nndsvd",
            solver="cd",
            beta_loss="frobenius",
            max_iter=1200,
            tol=1e-5,
            random_state=0,
        )
        w = model.fit_transform(x)
        row_sum = w.sum(axis=1, keepdims=True)
        shares = np.divide(w, row_sum, out=np.zeros_like(w), where=row_sum > 0)
        dominant = shares.argmax(axis=1) if shares.shape[1] else np.zeros(n_docs, dtype=int)
        support_counts = np.bincount(dominant, minlength=k)
        support = float(np.mean(support_counts >= min_support))
        top_indices = [np.argsort(c)[::-1][:8] for c in model.components_]
        unique = len(set(int(i) for arr in top_indices for i in arr))
        diversity = unique / max(k * 8, 1)
        coherence = _npmi_for_topics(x, model.components_, top_n=8)
        coherence01 = float(np.clip((coherence + 1.0) / 2.0, 0.0, 1.0))
        reconstruction = float(np.clip(1.0 - model.reconstruction_err_ / norm_x, 0.0, 1.0))
        score = 0.40 * coherence01 + 0.25 * support + 0.20 * diversity + 0.15 * reconstruction
        rec = {
            "k": k,
            "selection_score": score,
            "npmi": coherence,
            "support_fraction": support,
            "topic_diversity": diversity,
            "reconstruction": reconstruction,
            "dominant_support": support_counts.tolist(),
        }
        candidates.append(rec)
        if best is None or score > best[0] + 1e-12:
            best = (score, model, shares)

    assert best is not None
    return best[1], best[2], {"candidates": candidates, "selected_k": int(best[1].n_components)}


def fit_topic_space(
    projects: Sequence[Mapping[str, Any]],
    opportunities: Sequence[Mapping[str, Any]],
    *,
    space: str,
    min_k: int = 6,
    max_k: int = 14,
    min_support: int = 2,
    max_features: int = 24000,
) -> TopicSpaceResult:
    if space not in {"domain", "methods", "skills"}:
        raise ValueError(f"unsupported topic space: {space}")
    pdocs = [clean_text(str(p.get(space) or "")) or "vazio" for p in projects]
    if space == "domain":
        odocs = [
            _join_unique(opportunity_views(o, topic_clean=True)[f] for f in ["domain_title", "domain_area", "domain_intro", "domain_objectives"])
            for o in opportunities
        ]
    elif space == "methods":
        odocs = [
            _join_unique(opportunity_views(o, topic_clean=True)[f] for f in ["method_title", "method_methodology"])
            for o in opportunities
        ]
    else:
        odocs = [
            _join_unique(opportunity_views(o, topic_clean=True)[f] for f in ["skill_skills"])
            for o in opportunities
        ]

    min_df = 2 if len(pdocs) >= 20 else 1
    vectorizer = TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        ngram_range=(1, 2),
        max_features=max_features,
        sublinear_tf=True,
        min_df=min_df,
        max_df=0.85 if len(pdocs) >= 20 else 1.0,
        stop_words=list(V2_TOPIC_BOILERPLATE | SPACE_TOPIC_BOILERPLATE.get(space, frozenset())),
        token_pattern=r"(?u)\b[a-zA-ZÀ-ÿ][a-zA-ZÀ-ÿ0-9_+.#/-]+\b",
    )
    try:
        px = vectorizer.fit_transform(pdocs).tocsr()
    except ValueError:
        vectorizer.set_params(min_df=1, max_df=1.0)
        px = vectorizer.fit_transform(pdocs).tocsr()
    ox = vectorizer.transform(odocs).tocsr()

    # For method/skill spaces, down-weight vocabulary that is equally at home in
    # the domain narrative. This is an unsupervised field-exclusivity correction:
    # a disease name repeated everywhere should not define a "method" factor.
    if space in {"methods", "skills"}:
        other_spaces = [sp for sp in ["domain", "methods", "skills"] if sp != space]
        other_total = np.zeros(px.shape[1], dtype=float)
        for other in other_spaces:
            other_docs = [clean_text(str(p.get(other) or "")) or "vazio" for p in projects]
            other_x = vectorizer.transform(other_docs)
            other_total += np.asarray(other_x.sum(axis=0)).ravel()
        target_total = np.asarray(px.sum(axis=0)).ravel()
        exclusivity = np.divide(
            target_total, target_total + other_total + 1e-12,
            out=np.ones_like(target_total), where=(target_total + other_total) > 0,
        )
        contrast = np.sqrt(np.clip(exclusivity, 0.05, 1.0))
        px = px.multiply(contrast).tocsr()
        ox = ox.multiply(contrast).tocsr()

    if px.shape[1] == 0 or px.nnz == 0:
        return TopicSpaceResult(
            space=space,
            n_topics=1,
            project_weights=np.ones((len(projects), 1)),
            opportunity_weights=np.ones((len(opportunities), 1)),
            labels=["sem tema"],
            top_terms=[["sem tema"]],
            diagnostics={"selected_k": 1, "reason": "degenerate corpus"},
        )

    model, pshares, diagnostics = _select_nmf(px, min_k=min_k, max_k=max_k, min_support=min_support)
    ow = model.transform(ox)
    row_sum = ow.sum(axis=1, keepdims=True)
    oshares = np.divide(ow, row_sum, out=np.zeros_like(ow), where=row_sum > 0)
    terms = np.asarray(vectorizer.get_feature_names_out())
    labels, top_terms = _frex_like_terms(model.components_, terms, top_n=10)
    diagnostics["project_count"] = len(projects)
    diagnostics["opportunity_count"] = len(opportunities)
    diagnostics["vocabulary_size"] = int(px.shape[1])
    return TopicSpaceResult(
        space=space,
        n_topics=int(model.n_components),
        project_weights=pshares,
        opportunity_weights=oshares,
        labels=labels,
        top_terms=top_terms,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# Deterministic landscape geometry / clustering
# ---------------------------------------------------------------------------


def pcoa_from_cosine(cosine_matrix: np.ndarray, n_components: int = 2) -> np.ndarray:
    k = np.clip(np.asarray(cosine_matrix, dtype=float), -1.0, 1.0)
    d = np.clip(1.0 - k, 0.0, 2.0)
    n = d.shape[0]
    if n == 0:
        return np.empty((0, n_components))
    j = np.eye(n) - np.ones((n, n)) / n
    b = -0.5 * j @ (d ** 2) @ j
    b = (b + b.T) / 2.0
    vals, vecs = np.linalg.eigh(b)
    order = np.argsort(vals)[::-1]
    vals = vals[order]
    vecs = vecs[:, order]
    positive = vals > 1e-10
    vals = vals[positive][:n_components]
    vecs = vecs[:, positive][:, :n_components]
    coords = vecs * np.sqrt(vals)[None, :] if len(vals) else np.zeros((n, 0))
    if coords.shape[1] < n_components:
        coords = np.column_stack([coords, np.zeros((n, n_components - coords.shape[1]))])
    # Eigenvector signs are mathematically arbitrary. Fix them for byte-level reproducibility.
    for col in range(coords.shape[1]):
        idx = int(np.argmax(np.abs(coords[:, col])))
        if coords[idx, col] < 0:
            coords[:, col] *= -1
    return coords


def choose_hierarchical_clusters(cosine_matrix: np.ndarray, min_k: int = 3, max_k: int = 12) -> tuple[np.ndarray, dict[str, Any]]:
    n = cosine_matrix.shape[0]
    if n <= 2:
        return np.zeros(n, dtype=int), {"selected_k": 1, "candidates": []}
    d = np.clip(1.0 - np.asarray(cosine_matrix, dtype=float), 0.0, 2.0)
    np.fill_diagonal(d, 0.0)
    upper = max(2, min(max_k, n - 1))
    lower = max(2, min(min_k, upper))
    candidates: list[dict[str, Any]] = []
    best: tuple[float, int, np.ndarray] | None = None
    for k in range(lower, upper + 1):
        model = AgglomerativeClustering(n_clusters=k, metric="precomputed", linkage="average")
        labels = model.fit_predict(d)
        try:
            score = float(silhouette_score(d, labels, metric="precomputed"))
        except Exception:
            score = -1.0
        candidates.append({"k": k, "silhouette": score})
        if best is None or score > best[0] + 1e-12:
            best = (score, k, labels)
    assert best is not None
    return np.asarray(best[2], dtype=int), {"selected_k": int(best[1]), "silhouette": float(best[0]), "candidates": candidates}


# ---------------------------------------------------------------------------
# Self-supervised benchmarks
# ---------------------------------------------------------------------------


def _metrics_from_ranks(ranks: Sequence[int]) -> dict[str, float]:
    arr = np.asarray(ranks, dtype=float)
    if len(arr) == 0:
        return {"mrr": 0.0, "recall_at_1": 0.0, "recall_at_5": 0.0, "recall_at_10": 0.0}
    return {
        "mrr": float(np.mean(1.0 / arr)),
        "recall_at_1": float(np.mean(arr <= 1)),
        "recall_at_5": float(np.mean(arr <= 5)),
        "recall_at_10": float(np.mean(arr <= 10)),
    }


def benchmark_opportunity_retrieval(rows: Sequence[Mapping[str, Any]], *, max_cases: int | None = None) -> dict[str, Any]:
    """Deterministic title→body and sibling-plan retrieval benchmarks."""
    rows = list(rows)
    if max_cases:
        rows = rows[: int(max_cases)]
    bodies = [
        _join_unique([
            scrub_citations(str(r.get("introduction_justification") or "")),
            scrub_citations(str(r.get("objectives") or "")),
            scrub_citations(str(r.get("methodology") or "")),
            scrub_citations(str(r.get("acquired_skills") or "")),
        ]) or "vazio"
        for r in rows
    ]
    titles = [clean_text(str(r.get("plan_title") or r.get("project_title") or "")) for r in rows]
    space = FixedSparseSpace(word_max_features=24000, char_max_features=24000, lsa_components=48).fit(bodies)

    ranks: dict[str, list[int]] = {"word": [], "char": [], "lsa": [], "rrf": []}
    sibling_ranks: dict[str, list[int]] = {"word": [], "char": [], "lsa": [], "rrf": []}
    projects = [str(r.get("project_code") or "") for r in rows]

    for i, query in enumerate(titles):
        if not query:
            continue
        s = space.score(query)
        fused = rrf([s.word, s.char, s.lsa], weights=[1.0, 1.0, 0.75])
        for name, values in [("word", s.word), ("char", s.char), ("lsa", s.lsa), ("rrf", fused)]:
            order = np.argsort(-np.asarray(values), kind="mergesort")
            pos = int(np.where(order == i)[0][0]) + 1
            ranks[name].append(pos)

            siblings = [j for j, p in enumerate(projects) if p and p == projects[i] and j != i]
            if siblings:
                sibling_positions = [int(np.where(order == j)[0][0]) + 1 for j in siblings]
                sibling_ranks[name].append(min(sibling_positions))

    return {
        "title_to_body": {name: _metrics_from_ranks(v) for name, v in ranks.items()},
        "sibling_plan": {name: _metrics_from_ranks(v) for name, v in sibling_ranks.items()},
        "cases": len(next(iter(ranks.values()))) if ranks else 0,
        "sibling_cases": len(next(iter(sibling_ranks.values()))) if sibling_ranks else 0,
    }


def corpus_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    h = hashlib.sha256()
    for row in rows:
        payload = {
            "id": str(row.get("id_opportunity") or ""),
            "project": str(row.get("project_code") or ""),
            "title": str(row.get("project_title") or ""),
            "plan": str(row.get("plan_title") or ""),
            "intro": str(row.get("introduction_justification") or ""),
            "objectives": str(row.get("objectives") or ""),
            "methodology": str(row.get("methodology") or ""),
            "skills": str(row.get("acquired_skills") or ""),
        }
        h.update(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()
