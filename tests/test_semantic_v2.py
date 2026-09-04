from __future__ import annotations

import numpy as np

from pulsar_research.analysis.v2 import (
    FixedSparseSpace,
    benchmark_opportunity_retrieval,
    project_records,
    rrf,
    scrub_citations,
    score_ad_hoc_opportunities,
)


def _rows():
    return [
        {
            "id_opportunity": "1", "project_code": "P1", "project_title": "Epidemiologia espacial da dengue",
            "plan_title": "Análise espacial e modelagem", "large_area": "Saúde", "area": "Epidemiologia",
            "introduction_justification": "Dengue e distribuição espacial (SILVA et al., 2024).",
            "objectives": "Mapear incidência e fatores associados.",
            "methodology": "Geoprocessamento, regressão espacial e análise temporal.",
            "acquired_skills": "R, Python, GIS e bioestatística.", "professor_siape": "10",
        },
        {
            "id_opportunity": "2", "project_code": "P1", "project_title": "Epidemiologia espacial da dengue",
            "plan_title": "Organização do banco e mapas", "large_area": "Saúde", "area": "Epidemiologia",
            "introduction_justification": "Distribuição territorial da dengue.",
            "objectives": "Construir banco e mapas.",
            "methodology": "Limpeza de dados, geocodificação e mapas temáticos.",
            "acquired_skills": "R, bases de dados e visualização.", "professor_siape": "10",
        },
        {
            "id_opportunity": "3", "project_code": "P2", "project_title": "Cultura celular tumoral",
            "plan_title": "Ensaios in vitro", "large_area": "Biologia", "area": "Biologia celular",
            "introduction_justification": "Modelo experimental de células tumorais.",
            "objectives": "Avaliar viabilidade celular.",
            "methodology": "Cultura celular, ensaio MTT e microscopia.",
            "acquired_skills": "Cultura celular, pipetagem e microscopia.", "professor_siape": "20",
        },
    ]


def test_citation_scrubber_removes_author_year_noise():
    out = scrub_citations("Segundo SILVA et al., 2024, o método é robusto (PEREIRA et al., 2021).")
    assert "2024" not in out and "2021" not in out
    assert "método" in out


def test_project_records_group_sibling_plans():
    projects = project_records(_rows())
    assert len(projects) == 2
    p1 = next(p for p in projects if p["project_code"] == "P1")
    assert set(p1["opportunity_ids"]) == {"1", "2"}


def test_fixed_space_is_repeatable_and_query_independent():
    docs = ["epidemiologia espacial dengue", "cultura celular microscopia", "revisao sistematica prisma"]
    a = FixedSparseSpace(lsa_components=2).fit(docs).score("análise espacial")
    b = FixedSparseSpace(lsa_components=2).fit(docs).score("análise espacial")
    np.testing.assert_allclose(a.word, b.word)
    np.testing.assert_allclose(a.char, b.char)
    np.testing.assert_allclose(a.lsa, b.lsa)


def test_rrf_and_ad_hoc_scores_are_bounded():
    fused = rrf([np.array([3.0, 1.0]), np.array([2.0, 4.0])])
    assert np.all((fused >= 0) & (fused <= 1))
    result = score_ad_hoc_opportunities(_rows(), "epidemiologia espacial R Python")
    assert len(result.ids) == 3
    assert np.all((result.combined >= 0) & (result.combined <= 1))


def test_benchmark_contract():
    out = benchmark_opportunity_retrieval(_rows())
    assert "title_to_body" in out and "sibling_plan" in out
    assert 0 <= out["title_to_body"]["word"]["mrr"] <= 1
