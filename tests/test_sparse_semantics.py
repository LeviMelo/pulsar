from pulsar_research.analysis.text import analyze_corpus


def test_relevant_document_ranks_higher():
    docs = [
        "epidemiologia DATASUS análise espacial SINAN saúde pública",
        "genômica molecular sequenciamento DNA PCR transcriptoma",
        "história da arte e literatura brasileira",
    ]
    r = analyze_corpus(docs, "epidemiologia dados SUS análise espacial", lsa_components=2, nmf_topics=2, clusters=2)
    assert int(r.combined_score.argmax()) == 0
    assert r.combined_score[0] > r.combined_score[2]


def test_ad_hoc_query_is_rebuildable_and_local():
    from pulsar_research.analysis.text import quick_query_scores
    docs = ["revisão sistemática metanálise PRISMA", "cultura celular western blot", "epidemiologia DATASUS"]
    scores = quick_query_scores(docs, "meta-análise revisão sistemática")
    assert int(scores.argmax()) == 0
