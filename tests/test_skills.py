from __future__ import annotations

from pulsar_research.semantics.skills import (SKILLS_BY_ID, discover_candidate_skills,
                                              extract_skills, skill_match, skill_profile)


def test_extracts_multiple_independent_skills():
    text = ("O bolsista utilizará Python e R para análise de dados do DATASUS e do SINAN, "
            "com geoprocessamento no QGIS e regressão logística.")
    found = {h.skill_id for h in extract_skills(text)}
    assert {"python", "r_lang", "datasus", "sinan", "geoprocessing", "regression"} <= found


def test_skills_are_a_set_not_a_partition():
    """The failure mode of the old NMF: one factor absorbing every project."""
    wet = skill_profile(["Cultura celular, ensaio MTT e citometria de fluxo."])
    dry = skill_profile(["Análise em R de dados do SIH/SUS com séries temporais."])
    assert "cell_culture" in wet and "flow_cytometry" in wet
    assert "r_lang" in dry and "sih_sia" in dry and "time_series" in dry
    assert not set(wet) & set(dry), "unrelated projects must not share technical labels"


def test_generic_competencies_are_recorded_but_never_discriminative():
    hits = {h.skill_id: h for h in extract_skills(
        "O aluno desenvolverá leitura crítica e redação científica, além de PCR e eletroforese.")}
    assert hits["critical_reading"].generic and hits["scientific_writing"].generic
    assert not hits["pcr"].generic
    score, shared = skill_match({"critical_reading": 5, "scientific_writing": 4},
                                ["python", "critical_reading"])
    assert score == 0.0 and shared == []


def test_skill_match_is_fraction_of_operator_skills_present():
    score, shared = skill_match({"python": 2, "datasus": 1, "pcr": 1},
                                ["python", "datasus", "meta_analysis", "r_lang"])
    assert shared == ["datasus", "python"]
    assert score == 0.5


def test_r_is_not_matched_by_every_stray_letter():
    assert not any(h.skill_id == "r_lang" for h in extract_skills(
        "O projeto será realizado no laboratório de microbiologia."))
    assert any(h.skill_id == "r_lang" for h in extract_skills(
        "As análises serão feitas no software R com o pacote tidyverse."))


def test_discovery_surfaces_uncovered_terms():
    texts = ["espectrometria de massas aplicada a proteínas"] * 5 + ["outro assunto"] * 20
    terms = {t for t, _ in discover_candidate_skills(texts, min_docs=3, max_doc_fraction=0.5)}
    assert "espectrometria" in terms


def test_taxonomy_ids_are_unique_and_well_formed():
    assert len(SKILLS_BY_ID) > 40
    for skill_id, skill in SKILLS_BY_ID.items():
        assert skill_id == skill.skill_id
        assert skill.patterns and skill.label and skill.category
