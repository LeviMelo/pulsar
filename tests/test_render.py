"""A draft must never claim more affinity than the ranking actually found.

The operator applied to 187 opportunities across 65 professors, most of them
outside his field. An email asserting shared research interest to a recipient at
the 7th percentile is a claim two colleagues can disprove by comparing inboxes,
so the assertion is gated on the measured fit rather than written unconditionally
into the template.
"""
from __future__ import annotations

import pytest

from pulsar_research.outreach.panels import benchmark_summary
from pulsar_research.outreach.render import (STRONG_FIT_PERCENTILE, build_context,
                                             read_template, render_message)

PROFILE = {
    "about_lines": ["pesquisador no NEES/UFAL", "direção de pesquisa do IFMSA-UFAL"],
    "work_lines": ["manuscrito sobre leptospirose, em preparação"],
    "contribution_lines": ["construir e curar a base de dados",
                           "conduzir a análise estatística"],
    "annexes": ["Relatório PULSAR (PDF)"],
    "links": [{"label": "Mapa conceitual", "url": "https://levimelo.github.io/mapdoc/"}],
    "campaign_signature": "Levi de Melo Amorim",
}
# Enough of a benchmark for the method panel to draw something.
PULSAR = {"n_opportunities": 187, "n_projects": 89, "n_professors": 65,
          "n_atoms": 2242, "n_pages": 1243, "benchmark": benchmark_summary([
    {"benchmark": b, "channel": c, "metric": "mrr", "value": v}
    for b in ("title_to_body", "cross_project_area")
    for c, v in (("lexical_word", 0.9), ("latent", 0.7), ("fused", 0.8))
])}
SIGNATURE = "Levi de Melo Amorim"
# The gated claim: only a strong fit may tell a recipient where they ranked.
AFFINITY = "ficou no percentil"


def recipient(percentile: float, *, skills=("Séries temporais",), applied=True, funded=1):
    return {
        "siape": "1", "professor_name": "maria das gracas taveira",
        "email": "maria@example.invalid",
        "qualifying_opportunities": [{
            "id_opportunity": "9", "project_title": "Projeto X", "plan_title": "Plano Y",
            "edital": "Edital 01 Pibic 2026-2027", "funded_slots": funded,
            "opportunity_percentile": percentile, "already_applied": applied,
            "skills": [{"label": s, "generic": False} for s in skills],
        }],
        "rationale": {
            "opportunity_percentile": percentile, "funded_slots": funded,
            "already_applied": applied,
            "matched_skills": list(skills), "top_evidence": [],
            "reading": {"facets": {"overall": percentile, "domain": percentile},
                        "channels": {"neural": percentile}},
        },
        "portfolio_evidence": [],
    }


def render(percentile: float, *, pulsar=None, **kw) -> str:
    _, body, _ = render_message(read_template("default_subject.j2"),
                                read_template("default_body.j2"),
                                recipient(percentile, **kw), SIGNATURE, PROFILE,
                                extra={"pulsar": pulsar} if pulsar else None)
    return body


def test_a_strong_fit_may_state_the_alignment():
    body = render(STRONG_FIT_PERCENTILE + 5)
    assert AFFINITY in body
    assert "séries temporais" in body.lower()


def test_every_draft_discloses_that_it_was_generated():
    """The operator chose disclosure over concealment; it must not be droppable."""
    for percentile in (7.0, 98.0):
        body = render(percentile)
        assert "PULSAR" in body
        assert "redigida e enviada por um sistema" in body


def test_a_weak_fit_never_states_an_alignment():
    body = render(7.0)
    assert AFFINITY not in body, "a p7 recipient must not be told the plan matches this student"
    assert "Séries temporais" not in body


def test_the_threshold_is_the_only_thing_that_gates_the_claim():
    assert AFFINITY not in render(STRONG_FIT_PERCENTILE - 0.1)
    assert AFFINITY in render(STRONG_FIT_PERCENTILE)


def test_the_draft_acknowledges_an_application_the_professor_can_already_see():
    assert "me inscrevi" in render(90.0, applied=True)
    assert "tenho interesse" in render(90.0, applied=False)


def test_context_never_invents_a_fit_when_the_ranking_produced_none():
    ctx = build_context({"professor_name": "x", "qualifying_opportunities": [], "rationale": {}},
                        SIGNATURE, PROFILE)
    assert ctx["fit_is_strong"] is False and ctx["opportunity_percentile"] == 0.0


@pytest.mark.parametrize("percentile", [0.0, 50.0, 100.0])
def test_the_ask_is_always_present(percentile):
    assert "a vaga ainda está aberta?" in render(percentile)
    assert "ainda há vaga aberta aqui" in render(percentile, funded=0)


def test_a_funded_slot_is_reported_as_the_edital_says_it_not_as_fact():
    """The edital's funding flag is what PULSAR read, not what the professor has.

    Professors commonly allocate a bolsa before publication. A draft that treats
    the flag as ground truth asks a question the recipient has to correct.
    """
    body = render(90.0, funded=1)
    assert "porque é o que consta no edital" in body
    assert "já esteja combinada com um aluno" in body
    assert "o sistema não tem como enxergar isso" in body
    # And the recipient is given a cost-free way to say no.
    assert "não insisto" in body


def test_the_declared_qualifications_never_name_the_restricted_counterpart():
    """The NEES data-infrastructure work is disclosable; its counterpart is not.

    This is a real confidentiality constraint on the operator, and the text is
    sent verbatim to SIGAA and paraphrased into outreach, so it is pinned here
    rather than left to whoever next edits the profile.
    """
    from pulsar_research.config import AppConfig

    profile = AppConfig.load().load_profile()
    # Every surface that is actually sent, not only the one sent to SIGAA:
    # `about_lines` and `work_lines` go to 62 external recipients.
    surfaces = [profile.get("qualifications_text", ""),
                *profile.get("capability_lines", []),
                *profile.get("about_lines", []),
                *profile.get("work_lines", [])]
    # Naming the counterpart is not the only way to expose the project: NEES plus
    # "monitoring system" plus TabNet plus national scope reconstructs it for
    # anyone who would recognise it. The operator-facing text describes his
    # competence, not the engagement.
    for restricted in ("Ministério da Saúde", "Ministerio da Saude", "DEMAS",
                       "Grupo de Trabalho", "GT-MS", "TabNet",
                       "especificação funcional", "alcance nacional", "monitoramento"):
        for text in surfaces:
            assert restricted not in text, f"{restricted!r} must not appear in operator-facing text"
    assert "NEES" in profile["qualifications_text"], "the affiliation itself is not restricted"
    assert "DATASUS" in profile["qualifications_text"], "nor is the subject matter"


def test_the_method_panel_is_shown_to_everyone_including_a_poor_match():
    """It describes the engine, not the recipient, so nothing gates it."""
    for percentile in (7.0, 98.0):
        body = render(percentile, pulsar=PULSAR)
        assert "representação" in body and "média" in body
        assert "achar o plano pelo seu título" in body


def test_the_fit_panel_obeys_the_same_gate_as_the_prose_claim():
    """A chart asserting alignment is still an assertion of alignment."""
    assert "percentil deste plano" not in render(7.0, pulsar=PULSAR)
    assert "percentil deste plano" in render(98.0, pulsar=PULSAR)


def test_without_statistics_no_panel_is_drawn_and_nothing_breaks():
    body = render(98.0)
    assert "achar o plano pelo seu título" not in body
    assert "SOBRE MIM" in body, "the rest of the message must still render"


def test_the_artefacts_offered_are_the_ones_the_profile_declares():
    body = render(98.0)
    assert "Relatório PULSAR (PDF)" in body
    assert "manuscrito sobre leptospirose" in body
    assert "https://levimelo.github.io/mapdoc/" in body


def test_a_half_measured_corpus_refuses_to_render_rather_than_understating_itself():
    """StrictUndefined is the guard: better a failed build than "0 projetos"."""
    import jinja2

    with pytest.raises(jinja2.UndefinedError):
        render(98.0, pulsar={"n_opportunities": 187})
