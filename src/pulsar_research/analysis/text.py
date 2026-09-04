from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Iterable

import numpy as np
from scipy import sparse
from sklearn.cluster import KMeans
from sklearn.decomposition import NMF, TruncatedSVD
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize


def clean_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value or " ").strip()
    return value


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass
class SparseSemanticResult:
    tfidf_similarity: np.ndarray
    lsa_similarity: np.ndarray
    bm25_similarity: np.ndarray
    combined_score: np.ndarray
    clusters: np.ndarray
    coords: np.ndarray
    topic_weights: np.ndarray
    topic_terms: list[list[str]]


def _bm25_scores(documents: list[str], query: str, max_features: int = 30000) -> np.ndarray:
    if not documents:
        return np.array([], dtype=float)
    cv = CountVectorizer(lowercase=True, strip_accents="unicode", ngram_range=(1, 2), max_features=max_features, min_df=1)
    x = cv.fit_transform(documents)
    q = cv.transform([query])
    if q.nnz == 0:
        return np.zeros(len(documents), dtype=float)
    n_docs = x.shape[0]
    df = np.asarray((x > 0).sum(axis=0)).ravel()
    idf = np.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
    doc_len = np.asarray(x.sum(axis=1)).ravel()
    avgdl = float(doc_len.mean()) if len(doc_len) else 1.0
    avgdl = max(avgdl, 1e-9)
    k1, b = 1.5, 0.75
    q_terms = q.indices
    scores = np.zeros(n_docs, dtype=float)
    for j in q_terms:
        tf = np.asarray(x[:, j].todense()).ravel()
        denom = tf + k1 * (1.0 - b + b * doc_len / avgdl)
        scores += idf[j] * ((tf * (k1 + 1.0)) / np.where(denom == 0, 1.0, denom))
    maxv = scores.max(initial=0.0)
    return scores / maxv if maxv > 0 else scores


def analyze_corpus(
    documents: list[str],
    query: str,
    *,
    word_max_features: int = 18000,
    char_max_features: int = 18000,
    lsa_components: int = 64,
    nmf_topics: int = 8,
    clusters: int = 8,
    weight_lsa: float = 0.55,
    weight_tfidf: float = 0.25,
    weight_bm25: float = 0.20,
) -> SparseSemanticResult:
    if not documents:
        raise ValueError("No documents to analyze")
    docs = [clean_text(x) or "vazio" for x in documents]
    query = clean_text(query) or "pesquisa"

    word = TfidfVectorizer(
        lowercase=True, strip_accents="unicode", ngram_range=(1, 2),
        max_features=word_max_features, sublinear_tf=True, min_df=1,
    )
    char = TfidfVectorizer(
        analyzer="char_wb", lowercase=True, strip_accents="unicode",
        ngram_range=(3, 5), max_features=char_max_features, sublinear_tf=True, min_df=1,
    )
    word_all = word.fit_transform(docs + [query])
    char_all = char.fit_transform(docs + [query])
    combined = sparse.hstack([word_all, char_all], format="csr")
    combined = normalize(combined)
    x = combined[:-1]
    q = combined[-1]
    tfidf_sim = cosine_similarity(x, q).ravel()

    max_components = max(1, min(lsa_components, x.shape[0] - 1 if x.shape[0] > 1 else 1, x.shape[1] - 1 if x.shape[1] > 1 else 1))
    if max_components >= 2 and x.shape[0] >= 2:
        svd = TruncatedSVD(n_components=max_components, random_state=42)
        latent_all = svd.fit_transform(combined)
        latent_all = normalize(latent_all)
        latent_x, latent_q = latent_all[:-1], latent_all[-1:]
        lsa_sim = cosine_similarity(latent_x, latent_q).ravel()
        coords = latent_x[:, :2] if latent_x.shape[1] >= 2 else np.column_stack([latent_x[:, 0], np.zeros(len(latent_x))])
    else:
        lsa_sim = tfidf_sim.copy()
        coords = np.column_stack([tfidf_sim, np.zeros(len(tfidf_sim))])
        latent_x = coords

    bm25 = _bm25_scores(docs, query)
    combined_score = weight_lsa * lsa_sim + weight_tfidf * tfidf_sim + weight_bm25 * bm25

    n_clusters = max(1, min(clusters, len(docs)))
    if n_clusters > 1 and len(docs) >= n_clusters:
        km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        cluster_ids = km.fit_predict(latent_x)
    else:
        cluster_ids = np.zeros(len(docs), dtype=int)

    word_x = word_all[:-1]
    n_topics = max(1, min(nmf_topics, word_x.shape[0], word_x.shape[1]))
    topic_terms: list[list[str]] = []
    if n_topics >= 1 and word_x.nnz:
        nmf = NMF(n_components=n_topics, init="nndsvda" if n_topics <= min(word_x.shape) else "random", random_state=42, max_iter=500)
        weights = nmf.fit_transform(word_x)
        terms = np.asarray(word.get_feature_names_out())
        for component in nmf.components_:
            idx = component.argsort()[::-1][:8]
            topic_terms.append([str(terms[i]) for i in idx])
    else:
        weights = np.zeros((len(docs), 1))
        topic_terms = [["sem tema"]]

    return SparseSemanticResult(
        tfidf_similarity=tfidf_sim,
        lsa_similarity=lsa_sim,
        bm25_similarity=bm25,
        combined_score=combined_score,
        clusters=cluster_ids,
        coords=np.asarray(coords),
        topic_weights=np.asarray(weights),
        topic_terms=topic_terms,
    )


def quick_query_scores(
    documents: list[str],
    query: str,
    *,
    word_max_features: int = 12000,
    char_max_features: int = 12000,
    lsa_components: int = 32,
    weight_lsa: float = 0.55,
    weight_tfidf: float = 0.25,
    weight_bm25: float = 0.20,
) -> np.ndarray:
    """Cheap ad-hoc semantic relevance for campaign/research queries.

    Fits only sparse corpus-native representations. No model download or persisted
    embedding index is involved, so arbitrary new campaign queries remain cheap.
    """
    if not documents:
        return np.array([], dtype=float)
    docs = [clean_text(x) or "vazio" for x in documents]
    query = clean_text(query) or "pesquisa"
    word = TfidfVectorizer(lowercase=True, strip_accents="unicode", ngram_range=(1,2), max_features=word_max_features, sublinear_tf=True)
    char = TfidfVectorizer(analyzer="char_wb", lowercase=True, strip_accents="unicode", ngram_range=(3,5), max_features=char_max_features, sublinear_tf=True)
    wa = word.fit_transform(docs + [query]); ca = char.fit_transform(docs + [query])
    mat = normalize(sparse.hstack([wa, ca], format="csr"))
    x, q = mat[:-1], mat[-1]
    tfidf = cosine_similarity(x, q).ravel()
    max_components = max(1, min(lsa_components, mat.shape[0]-1 if mat.shape[0]>1 else 1, mat.shape[1]-1 if mat.shape[1]>1 else 1))
    if max_components >= 2 and len(docs) >= 2:
        svd = TruncatedSVD(n_components=max_components, random_state=42)
        latent = normalize(svd.fit_transform(mat))
        lsa = cosine_similarity(latent[:-1], latent[-1:]).ravel()
    else:
        lsa = tfidf.copy()
    bm25 = _bm25_scores(docs, query)
    return weight_lsa*lsa + weight_tfidf*tfidf + weight_bm25*bm25
