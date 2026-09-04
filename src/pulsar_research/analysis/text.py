from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
from scipy import sparse
from sklearn.cluster import KMeans
from sklearn.decomposition import NMF, TruncatedSVD
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize


# scikit-learn ships an English stop list, but not a Portuguese one.  Keep this
# local and explicit so the semantic pipeline remains fully offline/rebuildable.
PORTUGUESE_STOP_WORDS = frozenset(
    """
    a ao aos aquela aquelas aquele aqueles aquilo as ate com como da das de dela delas dele deles
    depois do dos e ela elas ele eles em entre era eram essa essas esse esses esta estas este estes
    eu foi foram ha isso isto ja lhe lhes mais mas me mesmo meu meus minha minhas muito muita muitas
    muitos na nas nao nem no nos nossa nossas nosso nossos num numa o os ou para pela pelas pelo pelos
    por porque qual quais quando que quem se sem seu seus sua suas tambem tem tendo ter teve tinham um
    uma umas uns voce voces sobre sob ser sao sendo sido tinha ainda onde cada contra desde durante
    enquanto esse esta aqui ali la assim entao portanto porem contudo atraves acerca mediante conforme
    segundo todos todas todo toda outro outra outros outras proprio propria proprios proprias tanto
    quanto tal tais somente apenas pode podem poderia poderiam deve devem sendo aos quais cujo cuja
    cujos cujas
    """.split()
)

# Words introduced systematically by our own document scaffolding or by common
# SIGAA/Lattes metadata are bad topic labels even though they can remain useful
# in retrieval.  They are removed only from the NMF topic model.
TOPIC_BOILERPLATE = frozenset(
    {
        "projeto", "projetos", "plano", "planos", "area", "areas", "professor",
        "professora", "pesquisa", "pesquisas", "justificativa", "introducao",
        "objetivo", "objetivos", "metodologia", "metodologias", "habilidade",
        "habilidades", "atividade", "atividades", "trabalho", "trabalhos",
        "curriculo", "lattes", "sigaa", "ufal", "sim", "nao", "brasil",
        "brasileiro", "brasileira", "portugues", "portuguesa",
    }
)
TOPIC_STOP_WORDS = frozenset(PORTUGUESE_STOP_WORDS | TOPIC_BOILERPLATE)


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
    cv = CountVectorizer(
        lowercase=True,
        strip_accents="unicode",
        ngram_range=(1, 2),
        max_features=max_features,
        min_df=1,
        stop_words=list(PORTUGUESE_STOP_WORDS),
    )
    x = cv.fit_transform(documents)
    q = cv.transform([query])
    if q.nnz == 0:
        return np.zeros(len(documents), dtype=float)
    n_docs = x.shape[0]
    df = np.asarray((x > 0).sum(axis=0)).ravel()
    idf = np.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
    doc_len = np.asarray(x.sum(axis=1)).ravel()
    avgdl = max(float(doc_len.mean()) if len(doc_len) else 1.0, 1e-9)
    k1, b = 1.5, 0.75
    scores = np.zeros(n_docs, dtype=float)
    for j in q.indices:
        tf = np.asarray(x[:, j].todense()).ravel()
        denom = tf + k1 * (1.0 - b + b * doc_len / avgdl)
        scores += idf[j] * ((tf * (k1 + 1.0)) / np.where(denom == 0, 1.0, denom))
    maxv = scores.max(initial=0.0)
    return scores / maxv if maxv > 0 else scores


def _topic_model(
    documents: list[str],
    reference_mask: Sequence[bool] | None,
    *,
    max_features: int,
    n_topics_requested: int,
) -> tuple[np.ndarray, list[list[str]]]:
    """Fit interpretable NMF themes on a reference corpus, then project all docs.

    PULSAR uses opportunities as the reference corpus.  Professor documents are
    much longer and contain Lattes/SIGAA boilerplate; allowing them to define the
    factors makes the opportunity landscape mostly a document-length/metadata
    model.  Projection keeps professor topic profiles comparable to opportunity
    themes without allowing them to define the vocabulary/factors.
    """
    n_docs = len(documents)
    if reference_mask is None:
        reference_mask = [True] * n_docs
    if len(reference_mask) != n_docs:
        raise ValueError("topic_reference_mask must match documents length")

    ref_docs = [doc for doc, keep in zip(documents, reference_mask) if keep]
    if not ref_docs:
        ref_docs = list(documents)

    min_df = 2 if len(ref_docs) >= 20 else 1
    max_df = 0.90 if len(ref_docs) >= 20 else 1.0
    vectorizer = TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        ngram_range=(1, 2),
        max_features=max_features,
        sublinear_tf=True,
        min_df=min_df,
        max_df=max_df,
        stop_words=list(TOPIC_STOP_WORDS),
        token_pattern=r"(?u)\b[a-zA-ZÀ-ÿ][a-zA-ZÀ-ÿ0-9_-]+\b",
    )

    try:
        ref_x = vectorizer.fit_transform(ref_docs)
    except ValueError:
        # Tiny or degenerate corpora: be permissive rather than failing analysis.
        vectorizer.set_params(min_df=1, max_df=1.0)
        ref_x = vectorizer.fit_transform(ref_docs)

    all_x = vectorizer.transform(documents)
    if not ref_x.nnz or ref_x.shape[1] == 0:
        return np.zeros((n_docs, 1)), [["sem tema"]]

    n_topics = max(1, min(n_topics_requested, ref_x.shape[0], ref_x.shape[1]))
    nmf = NMF(
        n_components=n_topics,
        init="nndsvda" if n_topics <= min(ref_x.shape) else "random",
        random_state=42,
        max_iter=800,
    )
    nmf.fit(ref_x)
    weights = nmf.transform(all_x)

    # Convert arbitrary NMF magnitudes into per-document topic shares.  This
    # makes a weight of 0.35 interpretable and comparable across topics/entities.
    row_sum = weights.sum(axis=1, keepdims=True)
    weights = np.divide(weights, row_sum, out=np.zeros_like(weights), where=row_sum > 0)

    terms = np.asarray(vectorizer.get_feature_names_out())
    topic_terms: list[list[str]] = []
    for component in nmf.components_:
        idx = component.argsort()[::-1][:10]
        topic_terms.append([str(terms[i]) for i in idx])
    return weights, topic_terms


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
    topic_reference_mask: Sequence[bool] | None = None,
) -> SparseSemanticResult:
    if not documents:
        raise ValueError("No documents to analyze")
    docs = [clean_text(x) or "vazio" for x in documents]
    query = clean_text(query) or "pesquisa"

    word = TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        ngram_range=(1, 2),
        max_features=word_max_features,
        sublinear_tf=True,
        min_df=1,
        stop_words=list(PORTUGUESE_STOP_WORDS),
    )
    char = TfidfVectorizer(
        analyzer="char_wb",
        lowercase=True,
        strip_accents="unicode",
        ngram_range=(3, 5),
        max_features=char_max_features,
        sublinear_tf=True,
        min_df=1,
    )
    word_all = word.fit_transform(docs + [query])
    char_all = char.fit_transform(docs + [query])
    combined = normalize(sparse.hstack([word_all, char_all], format="csr"))
    x = combined[:-1]
    q = combined[-1]
    tfidf_sim = cosine_similarity(x, q).ravel()

    max_components = max(
        1,
        min(
            lsa_components,
            x.shape[0] - 1 if x.shape[0] > 1 else 1,
            x.shape[1] - 1 if x.shape[1] > 1 else 1,
        ),
    )
    if max_components >= 2 and x.shape[0] >= 2:
        svd = TruncatedSVD(n_components=max_components, random_state=42)
        latent_all = normalize(svd.fit_transform(combined))
        latent_x, latent_q = latent_all[:-1], latent_all[-1:]
        # Negative cosine means anti-alignment, not "negative relevance".
        lsa_sim = np.clip(cosine_similarity(latent_x, latent_q).ravel(), 0.0, 1.0)
        coords = (
            latent_x[:, :2]
            if latent_x.shape[1] >= 2
            else np.column_stack([latent_x[:, 0], np.zeros(len(latent_x))])
        )
    else:
        lsa_sim = tfidf_sim.copy()
        coords = np.column_stack([tfidf_sim, np.zeros(len(tfidf_sim))])
        latent_x = coords

    bm25 = _bm25_scores(docs, query)
    combined_score = np.clip(
        weight_lsa * lsa_sim + weight_tfidf * tfidf_sim + weight_bm25 * bm25,
        0.0,
        1.0,
    )

    n_clusters = max(1, min(clusters, len(docs)))
    if n_clusters > 1 and len(docs) >= n_clusters:
        km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        cluster_ids = km.fit_predict(latent_x)
    else:
        cluster_ids = np.zeros(len(docs), dtype=int)

    weights, topic_terms = _topic_model(
        docs,
        topic_reference_mask,
        max_features=word_max_features,
        n_topics_requested=nmf_topics,
    )

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
    """Cheap ad-hoc semantic relevance using only local sparse representations."""
    if not documents:
        return np.array([], dtype=float)
    docs = [clean_text(x) or "vazio" for x in documents]
    query = clean_text(query) or "pesquisa"
    word = TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        ngram_range=(1, 2),
        max_features=word_max_features,
        sublinear_tf=True,
        stop_words=list(PORTUGUESE_STOP_WORDS),
    )
    char = TfidfVectorizer(
        analyzer="char_wb",
        lowercase=True,
        strip_accents="unicode",
        ngram_range=(3, 5),
        max_features=char_max_features,
        sublinear_tf=True,
    )
    wa = word.fit_transform(docs + [query])
    ca = char.fit_transform(docs + [query])
    mat = normalize(sparse.hstack([wa, ca], format="csr"))
    x, q = mat[:-1], mat[-1]
    tfidf = cosine_similarity(x, q).ravel()
    max_components = max(
        1,
        min(
            lsa_components,
            mat.shape[0] - 1 if mat.shape[0] > 1 else 1,
            mat.shape[1] - 1 if mat.shape[1] > 1 else 1,
        ),
    )
    if max_components >= 2 and len(docs) >= 2:
        svd = TruncatedSVD(n_components=max_components, random_state=42)
        latent = normalize(svd.fit_transform(mat))
        lsa = np.clip(cosine_similarity(latent[:-1], latent[-1:]).ravel(), 0.0, 1.0)
    else:
        lsa = tfidf.copy()
    bm25 = _bm25_scores(docs, query)
    return np.clip(weight_lsa * lsa + weight_tfidf * tfidf + weight_bm25 * bm25, 0.0, 1.0)
