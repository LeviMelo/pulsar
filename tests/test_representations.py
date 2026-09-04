from __future__ import annotations

import numpy as np
import pytest

from pulsar_research.semantics.representations import (BlockFusion, JointSVDFusion,
                                                       LSARepresentation, LexicalRepresentation,
                                                       PPMIRepresentation, ViewSpec, cosine)
from pulsar_research.semantics.retrieval import BM25FIndex, percentile_rank, rrf

DOCS = [
    "epidemiologia espacial da dengue com geoprocessamento e análise espacial por município",
    "análise espacial de arboviroses e índice de Moran em municípios de Alagoas",
    "dados do DATASUS e do SINAN para vigilância epidemiológica com R e Python",
    "cultura celular tumoral com ensaio MTT e microscopia óptica",
    "citometria de fluxo e viabilidade celular em linhagens tumorais",
    "revisão sistemática e metanálise seguindo o protocolo PRISMA",
] * 6


@pytest.fixture(scope="module")
def word():
    return LexicalRepresentation(analyzer="word", min_df=1).fit(DOCS)


def test_lexical_is_deterministic_and_normalized(word):
    a = word.encode(["análise espacial"])
    b = LexicalRepresentation(analyzer="word", min_df=1).fit(DOCS).encode(["análise espacial"])
    np.testing.assert_allclose(a.toarray(), b.toarray())
    assert np.allclose(np.sqrt(a.multiply(a).sum()), 1.0)


def test_lsa_is_deterministic_and_reduces_dimension(word):
    a = LSARepresentation(n_components=8, base=word).fit(DOCS, base_fitted=True)
    b = LSARepresentation(n_components=8, base=word).fit(DOCS, base_fitted=True)
    np.testing.assert_allclose(a.encode(DOCS[:3]), b.encode(DOCS[:3]), atol=1e-10)
    assert a.dimension == 8 < word.dimension


def test_ppmi_learns_corpus_native_term_neighbourhoods():
    model = PPMIRepresentation(n_components=8, min_count=1, window=6, remove_common=0).fit(DOCS)
    assert model.dimension == 8
    neighbours = [t for t, _ in model.nearest_terms("geoprocessamento", 8)]
    assert any(t in neighbours for t in ("espacial", "dengue", "municipio", "epidemiologia")), neighbours
    # Trailing punctuation must not create a second token for the same word.
    assert not any(t.endswith(".") for t in model.vocab)


def test_ppmi_is_deterministic():
    a = PPMIRepresentation(n_components=6, min_count=1, remove_common=0).fit(DOCS).encode(DOCS[:4])
    b = PPMIRepresentation(n_components=6, min_count=1, remove_common=0).fit(DOCS).encode(DOCS[:4])
    np.testing.assert_allclose(a, b, atol=1e-10)


def test_representations_separate_the_two_research_fields(word):
    vectors = word.encode(DOCS[:6])
    sim = cosine(vectors, vectors)
    # Doc 0 (spatial epidemiology) is closer to doc 1 than to doc 3 (cell culture).
    assert sim[0, 1] > sim[0, 3]


def test_block_fusion_is_the_weighted_mean_of_view_cosines(word):
    lsa = LSARepresentation(n_components=6, base=word).fit(DOCS, base_fitted=True)
    fusion = BlockFusion({"a": ViewSpec(word, 1.0), "b": ViewSpec(lsa, 1.0)})
    texts = ["análise espacial da dengue", "cultura celular tumoral"]
    joint = fusion.encode(texts)
    expected = 0.5 * (cosine(word.encode(texts[:1]), word.encode(texts[1:]))[0, 0]
                      + cosine(lsa.encode(texts[:1]), lsa.encode(texts[1:]))[0, 0])
    assert cosine(joint[:1], joint[1:])[0, 0] == pytest.approx(expected, abs=1e-9)


def test_block_fusion_similarity_matches_the_materialized_cosine(word):
    """`similarity` is the memory-safe path; it must agree with the joint vectors."""
    lsa = LSARepresentation(n_components=6, base=word).fit(DOCS, base_fitted=True)
    fusion = BlockFusion({"word": ViewSpec(word, 1.0), "lsa": ViewSpec(lsa, 0.5)})
    queries = ["análise espacial da dengue", "citometria de fluxo"]
    docs = DOCS[:6]
    joint = cosine(fusion.encode(queries), fusion.encode(docs))
    np.testing.assert_allclose(fusion.similarity(queries, docs), joint, atol=1e-9)


def test_block_fusion_keeps_a_sparse_view_sparse(word):
    """A lexical view has ~10^5 columns; the joint matrix must not densify it."""
    from scipy import sparse as sp
    lsa = LSARepresentation(n_components=6, base=word).fit(DOCS, base_fitted=True)
    fusion = BlockFusion({"word": ViewSpec(word, 1.0), "lsa": ViewSpec(lsa, 1.0)})
    assert sp.issparse(fusion.stack(DOCS[:4]))
    assert not sp.issparse(BlockFusion({"lsa": ViewSpec(lsa, 1.0)}).stack(DOCS[:4]))


def test_joint_svd_fusion_produces_a_lower_rank_consensus(word):
    lsa = LSARepresentation(n_components=6, base=word).fit(DOCS, base_fitted=True)
    fused = JointSVDFusion({"a": ViewSpec(word, 1.0), "b": ViewSpec(lsa, 1.0)}, n_components=5).fit(DOCS)
    assert fused.dimension == 5
    assert fused.encode(DOCS[:3]).shape == (3, 5)


def test_bm25f_weights_fields():
    records = [
        {"title": "análise espacial", "body": "texto irrelevante sobre outra coisa"},
        {"title": "outro assunto", "body": "análise espacial mencionada apenas no corpo"},
    ]
    index = BM25FIndex({"title": 3.0, "body": 1.0}).fit(records)
    scores = index.score("análise espacial")
    assert scores[0] > scores[1], "a title hit must outweigh a body hit"


def test_rrf_is_bounded_and_percentiles_are_scale_free():
    fused = rrf([np.array([3.0, 1.0, 2.0]), np.array([2.0, 4.0, 1.0])])
    assert np.all((fused >= 0) & (fused <= 1))
    pct = percentile_rank(np.array([0.1, 0.5, 0.9]))
    np.testing.assert_allclose(pct, percentile_rank(np.array([10.0, 50.0, 90.0])))
    assert pct[-1] == pytest.approx(100.0)


def test_map_fidelity_agrees_with_the_map_it_measures(word):
    """Both must use the metric the map was optimized against, not two metrics."""
    from pulsar_research.semantics.benchmark import map_fidelity
    from pulsar_research.semantics.landscape import build_map

    vectors = word.encode(DOCS).toarray()
    result = build_map(vectors, refine=True)
    checked = map_fidelity(vectors, result.coords)
    assert checked["stress"] == pytest.approx(result.diagnostics["stress"], abs=1e-9)
    assert checked["pearson"] == pytest.approx(result.diagnostics["pearson"], abs=1e-9)
