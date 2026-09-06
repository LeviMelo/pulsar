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

from pulsar_research.apps.outreach.panels import benchmark_summary
from pulsar_research.apps.outreach.render import (MAX_CONTRIBUTIONS, STRONG_FIT_PERCENTILE,
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
         "short": "leptospirose no Brasil", "blurb": "uma série temporal por região de saúde",
         "tags": ["time_series", "sinan"]},
        {"key": "glp1", "label": "GLP-1 no SNGPC", "recent": True,
         "short": "GLP-1 no SNGPC", "blurb": "farmacoepidemiologia nacional",
         "tags": ["datasus"]},
        {"key": "pulsar", "label": "Relatório técnico do PULSAR", "recent": True,
         "short": "o relatório do PULSAR", "blurb": "corpus e avaliação".replace("ç", "ç"),
         "tags": ["machine_learning"]},
    ],
    "contacts": [{"address": "levi.amorim@famed.ufal.br", "purpose": "iniciação científica"},
                 {"address": "levi.amorim@nees.ufal.br", "purpose": "outros assuntos"}],
    "sending_note": "Esta mensagem sai do meu endereço pessoal.",
    "expertise_note": "Trabalho há dois anos com ciência de dados, epidemiologia e computação científica.",
    "offer_prompt": "Se for útil ao projeto, eu poderia contribuir mais diretamente nestas frentes:",
    "closing_line": "Se a vaga ainda estiver aberta, fico à disposição para conversar "
                    "sobre o plano quando lhe for conveniente.",
    "links_intro": "Fora dos anexos, o que eu construo em software está público:",
    "works_tail": "Desenvolvi também o CMapDoc ({cmapdoc}), e o código está no meu GitHub ({github}).",
    "links": [{"key": "cmapdoc", "url": "https://levimelo.github.io/mapdoc/"},
              {"key": "github", "url": "https://github.com/LeviMelo"}],
    "campaign_signature": "Levi de Melo Amorim",
}
PULSAR = {"n_opportunities": 187, "n_projects": 89, "n_professors": 65,
          "n_atoms": 15019, "n_pages": 1932, "benchmark": benchmark_summary([
              {"benchmark": b, "channel": c, "metric": "mrr", "value": v}
              for b in ("title_to_body", "cross_project_area")
              for c, v in (("lexical_word", 0.9), ("latent", 0.7), ("fused", 0.8))])}
SIGNATURE = "Levi de Melo Amorim"
# The gated claim. It used to be a position ("O seu ficou em 4º") drawn on a
# scale: telling a professor his rank among his colleagues is a strange thing to
# do, so what survives is the fact that the match was strong, without a number.
FIT_CLAIM = "uma das correspondências mais fortes"


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


def test_a_strong_fit_may_say_the_match_was_strong_but_never_where_it_ranked():
    text = body(98.0)
    assert FIT_CLAIM in text
    # A position, a percentile and a drawn scale all told the recipient his place
    # in a ranking of his colleagues. None of them belongs in this message.
    for telemetry in ("O seu ficou em", "percentil", "MRR", "1º ├", "█"):
        assert telemetry not in text


def test_a_weak_fit_never_states_a_ranking():
    assert FIT_CLAIM not in body(7.0)


def test_a_weak_fit_may_still_name_what_the_plan_itself_asks_for():
    """Otherwise a low-ranked draft is a form letter, which is worse than silent."""
    text = body(7.0)
    assert "O seu plano pede time series" in text
    assert "montar a análise de tendência temporal" in text


def test_the_threshold_is_the_only_thing_that_gates_the_ranking():
    assert FIT_CLAIM not in body(STRONG_FIT_PERCENTILE - 0.1)
    assert FIT_CLAIM in body(STRONG_FIT_PERCENTILE)


def test_every_draft_says_who_is_writing_on_the_first_screen():
    first = body(50.0).split("\n\n")[1]
    assert "5º período de Medicina na FAMED/UFAL" in first
    assert "Escrevo sobre o plano" in first
    assert "registrei interesse no SIGAA" in first


def test_every_draft_separates_registration_from_indication():
    """The whole campaign rests on this distinction; it must never be dropped."""
    for percentile in (7.0, 98.0):
        text = body(percentile)
        # The registration is owned, not disclaimed: telling a professor it is
        # "no commitment" is both discourteous and, by now, untrue.
        # Stated as an operational fact about the SIGAA records, not as a story
        # about the automation arriving before its author.
        assert "não devem ser lidos como compromissos simultâneos" in text
        assert "autorização para uma indicação imediata" in text
        assert "Estou resolvendo caso a caso" in text
        for anthropomorphic in ("chegou antes de mim", "não é indicação nem compromisso"):
            assert anthropomorphic not in text


def test_the_scholarship_question_is_asked_inside_the_paragraph_that_motivates_it():
    """The question and its reason are one paragraph, not two.

    Standing alone it read as a demand arriving from nowhere. What licenses it
    is the confirmed PIBITI: a second link is only worth making if it is funded,
    and that premise has to be in the same breath as the question.
    """
    for percentile in (0.0, 50.0, 100.0):
        text = body(percentile)
        paragraph = next(p for p in text.split(2 * chr(10))
                         if "compromissos simultâneos" in p)
        assert "outra oportunidade de PIBITI encaminhada" in paragraph
        assert "priorizando as vagas com bolsa" in paragraph
        assert "status real" in paragraph
        # Not procurement: "só faria a troca por uma vaga com bolsa" read as
        # switching suppliers for a better price.
        assert "faria a troca" not in text


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


def test_the_html_alternative_is_nothing_but_plain_prose():
    """The body pasted into a reply must not arrive as a stack of styled boxes."""
    markup = render(98.0)[2]
    assert markup.count("<table") == 0, "the measurement card is gone"
    assert markup.count("<pre") == 0, "and so is every drawn panel"
    assert markup.count("<p>") >= 6, "the message itself is plain paragraphs"
    assert "background:#f4f6f8" not in markup, "no page chrome around the message"


def test_neither_alternative_carries_the_measurement_footer():
    """Corpus counts and a retrieval MRR under a cold email are telemetry.

    They also implied the sender was scoring professors against one another.
    The evaluation is in the attached report, where a reader who wants it looks.
    """
    _, text, markup = render(98.0)
    for telemetry in ("15.019", "MRR", "percentil", "registros do SIGAA e do Lattes"):
        assert telemetry not in text and telemetry not in markup


def test_the_built_software_is_named_in_the_body_not_buried_in_the_footer():
    """The manuscripts show the analysis; these show he ships the software.

    They lived in the footer card once, as two link chips under a measurement
    box, which is where a reader stops reading. Both alternatives must carry
    them in the message itself.
    """
    for percentile in (7.0, 98.0):
        _, text, markup = render(percentile)
        for url in ("https://levimelo.github.io/mapdoc/", "https://github.com/LeviMelo"):
            assert url in text and url in markup
        head, _, footer = text.partition("—" * 30)
        assert "CMapDoc" in head, "the app must be named before the footer rule"
        assert "github.com" not in footer, "no second copy under the card"


def test_the_message_does_not_depend_on_the_corpus_statistics():
    """It no longer quotes a single number from them, so it must render without."""
    text = body(98.0, pulsar=None)
    assert "Escrevo sobre o plano" in text
    assert "priorizando as vagas com bolsa" in text


def test_context_never_invents_a_fit_when_the_ranking_produced_none():
    ctx = build_context({"professor_name": "x", "qualifying_opportunities": [], "rationale": {}},
                        SIGNATURE, PROFILE)
    assert ctx["fit_is_strong"] is False and ctx["opportunity_percentile"] == 0.0
    assert FIT_CLAIM not in ctx["saudacao"]


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
    assert "Prezada professora Maria," in text
    assert "Prof(a)" not in text, "a filled-in placeholder is not a salutation"
    assert "Plano Y" in text


def test_every_draft_says_where_the_work_is_best_spent():
    """A medical student writing to a lab is otherwise read as asking for bench time."""
    for percentile in (7.0, 98.0):
        text = body(percentile)
        assert "dois anos com ciência de dados" in text


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
