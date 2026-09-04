"""A draft must never claim more affinity than the ranking actually found.

The operator applied to 187 opportunities across 65 professors, most of them
outside his field. An email asserting shared research interest to a recipient at
the 7th percentile is a claim two colleagues can disprove by comparing inboxes,
so the assertion is gated on the measured fit rather than written unconditionally
into the template.
"""
from __future__ import annotations

import pytest

from pulsar_research.outreach.render import (STRONG_FIT_PERCENTILE, build_context,
                                             read_template, render_message)

PROFILE = {
    "capability_lines": ["Python e R para análise", "bases públicas do SUS"],
    "campaign_signature": "Levi de Melo Amorim",
}
SIGNATURE = "Levi de Melo Amorim"
AFFINITY = "dialoga de perto"


def recipient(percentile: float, *, skills=("Séries temporais",), applied=True):
    return {
        "siape": "1", "professor_name": "maria das gracas taveira",
        "email": "maria@example.invalid",
        "qualifying_opportunities": [{
            "id_opportunity": "9", "project_title": "Projeto X", "plan_title": "Plano Y",
            "edital": "Edital 01 Pibic 2026-2027", "funded_slots": 1,
            "opportunity_percentile": percentile, "already_applied": applied,
            "skills": [{"label": s, "generic": False} for s in skills],
        }],
        "rationale": {
            "opportunity_percentile": percentile, "funded_slots": 1,
            "already_applied": applied,
            "matched_skills": list(skills), "top_evidence": [],
        },
        "portfolio_evidence": [],
    }


def render(percentile: float, **kw) -> str:
    _, body, _ = render_message(read_template("default_subject.j2"),
                                read_template("default_body.j2"),
                                recipient(percentile, **kw), SIGNATURE, PROFILE)
    return body


def test_a_strong_fit_may_state_the_alignment():
    body = render(STRONG_FIT_PERCENTILE + 5)
    assert AFFINITY in body
    assert "séries temporais" in body.lower()


def test_a_weak_fit_never_states_an_alignment():
    body = render(7.0)
    assert AFFINITY not in body, "a p7 recipient must not be told the plan matches this student"
    assert "Séries temporais" not in body


def test_the_threshold_is_the_only_thing_that_gates_the_claim():
    assert AFFINITY not in render(STRONG_FIT_PERCENTILE - 0.1)
    assert AFFINITY in render(STRONG_FIT_PERCENTILE)


def test_capabilities_are_always_presented_and_stay_on_their_own_lines():
    for percentile in (7.0, 98.0):
        body = render(percentile)
        for line in PROFILE["capability_lines"]:
            assert f"- {line}\n" in body, "each capability needs its own bullet, not a run-on"


def test_the_draft_acknowledges_an_application_the_professor_can_already_see():
    assert "me inscrevi" in render(90.0, applied=True)
    assert "tenho interesse" in render(90.0, applied=False)


def test_context_never_invents_a_fit_when_the_ranking_produced_none():
    ctx = build_context({"professor_name": "x", "qualifying_opportunities": [], "rationale": {}},
                        SIGNATURE, PROFILE)
    assert ctx["fit_is_strong"] is False and ctx["opportunity_percentile"] == 0.0


@pytest.mark.parametrize("percentile", [0.0, 50.0, 100.0])
def test_the_ask_is_always_present(percentile):
    assert "ainda está disponível" in render(percentile)
