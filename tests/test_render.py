"""A draft must never claim more affinity than the ranking found, and must not
read as a form letter with a name substituted in.

The operator applied across an edital, most of it outside his field. An email
asserting shared research interest to a recipient at the 7th percentile is a
claim two colleagues can disprove by comparing inboxes, so the ranking assertion
is gated on the measured fit. What is *not* gated is naming a technique the
recipient's own plan asks for: that is a fact about their plan, and it is the
only thing that makes a low-ranked draft worth reading.
"""
from __future__ import annotations

import pytest

from pulsar_research.outreach.panels import benchmark_summary
from pulsar_research.outreach.render import (MAX_CONTRIBUTIONS, STRONG_FIT_PERCENTILE,
                                             build_context, read_template, render_message,
                                             select_annexes, select_contributions)

PROFILE = {
    "identity_line": "estudante do 5º período de Medicina na FAMED/UFAL",
    "contribution_texts": {
        "temporal": "montar a análise de tendência temporal",
        "sus": "automatizar a extração das bases do SUS",
        "meta": "conduzir a metanálise",
        "database": "construir e curar a base de dados",
        "stats": "conduzir a análise estatística",
    },
    "contribution_by_skill": {
        "time_series": "temporal",
        "datasus": "sus", "sinan": "sus",       # one key, deliberately shared
        "meta_analysis": "meta",
        "biostatistics": "stats",
    },
    "contribution_default": ["database", "stats"],
    "annexes": [
        {"key": "lepto", "label": "Leptospirose 2007–2025", "recent": False,
         "tags": ["time_series", "sinan"]},
        {"key": "glp1", "label": "GLP-1 no SNGPC", "recent": True, "tags": ["datasus"]},
        {"key": "pulsar", "label": "Relatório técnico do PULSAR", "recent": True,
         "tags": ["machine_learning"]},
    ],
    "contacts": [{"address": "levi.amorim@famed.ufal.br", "purpose": "iniciação científica"},
                 {"address": "levi.amorim@nees.ufal.br", "purpose": "outros assuntos"}],
    "sending_note": "Esta mensagem sai do meu endereço pessoal.",
    "expertise_note": "São dois anos trabalhando com ciência de dados, epidemiologia e computação científica, e é aí que eu rendo mais: em qualquer frente que se resolva no computador.",
    "links": [{"label": "CMapDoc", "url": "https://levimelo.github.io/mapdoc/"}],
    "campaign_signature": "Levi de Melo Amorim",
}
PULSAR = {"n_opportunities": 187, "n_projects": 89, "n_professors": 65,
          "n_atoms": 15019, "n_pages": 1932, "benchmark": benchmark_summary([
              {"benchmark": b, "channel": c, "metric": "mrr", "value": v}
              for b in ("title_to_body", "cross_project_area")
              for c, v in (("lexical_word", 0.9), ("latent", 0.7), ("fused", 0.8))])}
SIGNATURE = "Levi de Melo Amorim"
# The gated claim: only a strong fit may tell a recipient where they ranked.
RANK_CLAIM = "O seu ficou em"


def recipient(percentile: float, *, skills=("time_series",), applied=True, funded=1):
    return {
        "siape": "1", "professor_name": "maria das gracas taveira",
        "email": "maria@example.invalid",
        "qualifying_opportunities": [{
            "id_opportunity": "9", "project_title": "Projeto X", "plan_title": "Plano Y",
            "edital": "Edital 01 Pibic 2026-2027", "funded_slots": funded,
            "opportunity_percentile": percentile, "already_applied": applied,
            "skills": [{"skill_id": s, "label": s.replace("_", " "), "generic": False}
                       for s in skills],
        }],
        "rationale": {
            "opportunity_percentile": percentile, "funded_slots": funded,
            "already_applied": applied,
            "matched_skills": [s.replace("_", " ") for s in skills], "top_evidence": [],
            "reading": {"facets": {"domain": percentile, "methods": percentile}},
        },
        "portfolio_evidence": [],
    }


def render(percentile: float, *, pulsar=PULSAR, **kw):
    return render_message(read_template("default_subject.j2"),
                          read_template("default_body.j2"),
                          recipient(percentile, **kw), SIGNATURE, PROFILE,
                          extra={"pulsar": pulsar} if pulsar else None)


def body(percentile: float, **kw) -> str:
    return render(percentile, **kw)[1]


def test_a_strong_fit_may_state_where_the_plan_ranked():
    text = body(98.0)
    assert RANK_CLAIM in text and "5º" in text, "percentile 98 of 187 is roughly 5th"


def test_a_weak_fit_never_states_a_ranking():
    assert RANK_CLAIM not in body(7.0)


def test_a_weak_fit_may_still_name_what_the_plan_itself_asks_for():
    """Otherwise a low-ranked draft is a form letter, which is worse than silent."""
    text = body(7.0)
    assert "O seu plano pede time series" in text
    assert "montar a análise de tendência temporal" in text


def test_the_threshold_is_the_only_thing_that_gates_the_ranking():
    assert RANK_CLAIM not in body(STRONG_FIT_PERCENTILE - 0.1)
    assert RANK_CLAIM in body(STRONG_FIT_PERCENTILE)


def test_every_draft_says_who_is_writing_on_the_first_screen():
    first = body(50.0).split("\n\n")[1]
    assert "5º período de Medicina na FAMED/UFAL" in first
    assert "Registrei interesse no plano" in first


def test_every_draft_separates_registration_from_indication():
    """The whole campaign rests on this distinction; it must never be dropped."""
    for percentile in (7.0, 98.0):
        text = body(percentile)
        # The registration is owned, not disclaimed: telling a professor it is
        # "no commitment" is both discourteous and, by now, untrue.
        assert "chegou antes de mim" in text
        assert "a indicação é única" in text
        assert "vale com bolsa ou sem ela" in text, "the offer is not conditioned on funding"
        assert "não é indicação nem compromisso" not in text,             "disclaiming a registration the professor can see is discourteous and untrue"


def test_the_scholarship_question_is_asked_plainly_in_every_draft():
    for percentile in (0.0, 50.0, 100.0):
        text = body(percentile)
        assert "A bolsa que consta no edital ainda está disponível?" in text
        assert "Como só posso confirmar um vínculo" in text


def test_the_offer_is_built_from_the_plan_not_from_a_fixed_list():
    single = select_contributions({"skills": [{"skill_id": "meta_analysis", "generic": False}]},
                                  PROFILE)
    assert single[0] == "conduzir a metanálise"
    # Several SUS skills mean one sentence about SUS extraction, not several.
    deduped = select_contributions(
        {"skills": [{"skill_id": s, "generic": False} for s in ("datasus", "sinan")]}, PROFILE)
    assert deduped.count("automatizar a extração das bases do SUS") == 1


def test_a_plan_matching_nothing_still_gets_a_usable_offer():
    assert select_contributions({"skills": []}, PROFILE) ==         [PROFILE["contribution_texts"][k] for k in PROFILE["contribution_default"]]


def test_a_default_never_repeats_work_a_matched_skill_already_named():
    """Deduplication is by key: two keys can name the same work in other words."""
    chosen = select_contributions(
        {"skills": [{"skill_id": "biostatistics", "generic": False}]}, PROFILE)
    assert chosen.count("conduzir a análise estatística") == 1
    assert len(chosen) == len(set(chosen))


BROAD_METHODS = {"biostatistics", "regression", "epi_design", "data_management"}


def test_no_annex_is_tagged_with_a_method_all_of_them_share():
    """What stops a spurious "closest match" is curation, not a threshold.

    Every one of these documents uses biostatistics and epidemiological design.
    Tagging them so matched a plant-physiology plan against the leptospirosis
    paper because both mention statistics.
    """
    from pulsar_research.config import AppConfig

    for annex in AppConfig.load().load_profile()["annexes"]:
        shared = BROAD_METHODS & set(annex["tags"])
        assert not shared, f"{annex['key']} is tagged with the non-distinctive {shared}"


def test_the_offer_never_becomes_a_catalogue():
    many = [{"skill_id": s, "generic": False}
            for s in ("time_series", "datasus", "meta_analysis", "sinan")]
    assert len(select_contributions({"skills": many}, PROFILE)) == MAX_CONTRIBUTIONS




def test_the_message_stays_short_enough_to_read_on_a_deadline_day():
    text = body(98.0)
    assert len(text) < 4400, f"a cold email of {len(text)} chars will not be read"


def test_the_html_alternative_is_plain_prose_plus_exactly_one_card():
    """The body pasted into a reply must not arrive as a stack of styled boxes."""
    markup = render(98.0)[2]
    assert markup.count("<table") == 1, "the footer card is the only table"
    assert markup.count("<pre") <= 1, "at most the one-line rank scale is preformatted"
    assert markup.count("<p>") >= 6, "the message itself is plain paragraphs"
    assert "background:#f4f6f8" not in markup, "no page chrome around the message"


def test_the_footer_says_the_same_thing_in_both_alternatives():
    _, text, markup = render(98.0)
    for token in ("15.019", "187", "PULSAR"):
        assert token in text and token in markup
    assert "https://levimelo.github.io/mapdoc/" in text
    assert 'href="https://levimelo.github.io/mapdoc/"' in markup


def test_without_statistics_the_footer_is_omitted_and_the_message_still_stands():
    text = body(98.0, pulsar=None)
    assert "sistema de prospecção" not in text
    assert "Registrei interesse no plano" in text
    assert "A bolsa que consta no edital ainda está disponível?" in text


def test_context_never_invents_a_fit_when_the_ranking_produced_none():
    ctx = build_context({"professor_name": "x", "qualifying_opportunities": [], "rationale": {}},
                        SIGNATURE, PROFILE)
    assert ctx["fit_is_strong"] is False and ctx["opportunity_percentile"] == 0.0
    assert ctx["card"]["percentile"] == 0.0


def test_the_declared_qualifications_never_name_the_restricted_counterpart():
    """The NEES data-infrastructure work is disclosable; its counterpart is not.

    These emails will be read far more attentively than the SIGAA field, so
    every outward surface is checked, not only the one sent to SIGAA.
    """
    from pulsar_research.config import AppConfig

    profile = AppConfig.load().load_profile()
    surfaces = [profile.get("qualifications_text", ""), profile.get("identity_line", ""),
                *profile.get("capability_lines", []), *profile.get("about_lines", []),
                *(w.get("text", "") for w in profile.get("work_lines", [])),
                *(profile.get("contribution_by_skill") or {}).values()]
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


@pytest.mark.parametrize("percentile", [0.0, 69.9, 70.0, 100.0])
def test_no_draft_ever_loses_the_recipient_or_the_plan(percentile):
    text = body(percentile)
    assert "Prezado(a) Prof(a). Maria das Gracas Taveira," in text
    assert "Plano Y" in text


def test_every_draft_says_where_the_work_is_best_spent():
    """A medical student writing to a lab is otherwise read as asking for bench time."""
    for percentile in (7.0, 98.0):
        text = body(percentile)
        assert "dois anos trabalhando com ciência de dados" in text
        assert "se resolva no computador" in text


# Phrasings that kept reappearing while this template was being written: the
# letter narrating itself, or explaining its own construction, instead of just
# saying the thing. Each one was written, read back as synthetic, and removed.
SELF_NARRATION = [
    "Esta mensagem é a continuação",
    "Sobre a mensagem em si",
    "A pergunta prática",
    "Uma pergunta objetiva",
    "Pergunto porque",
    "Uma ressalva",
    "De forma mais ampla",
    "Vale dizer",
    "é o que estou corrigindo agora",
    "O interesse eu assumo",
    "caso queira ver como eu trabalho",
]


@pytest.mark.parametrize("percentile", [7.0, 98.0])
def test_the_letter_never_narrates_itself(percentile):
    """The prose must read as written to the professor, not about the writing.

    Explaining why a question is being asked, announcing what the message is, or
    reassuring the reader about the writer's intentions all read as machine
    output even when every individual sentence is fine.
    """
    text = body(percentile)
    for tic in SELF_NARRATION:
        assert tic not in text, f"{tic!r} is the letter talking about itself"
