"""The text-drawn panels state measurements to 62 strangers, so they are pinned.

Three things can go wrong with a chart in an email and all three are checked
here: it can claim more than the numbers support, it can be reflowed into
nonsense by the recipient's client, and it can be too wide for the window it
lands in.
"""
from __future__ import annotations

import pytest

from pulsar_research.outreach.panels import (INDENT, bar, benchmark_summary, fit_panel,
                                             method_caption, method_panel)
from pulsar_research.outreach.render import text_to_html

# A miniature battery with the shape of the real one: fusion wins rarely, but is
# the only representation that is never worse than second.
ROWS = [
    {"benchmark": "title_to_body", "channel": c, "metric": "mrr", "value": v}
    for c, v in (("lexical_word", 0.93), ("latent", 0.80), ("neural", 0.76), ("fused", 0.86))
] + [
    {"benchmark": "cross_project_area", "channel": c, "metric": "mrr", "value": v}
    for c, v in (("lexical_word", 0.67), ("latent", 0.70), ("neural", 0.65), ("fused", 0.71))
] + [
    {"benchmark": "professor_holdout", "channel": c, "metric": "mrr", "value": v}
    for c, v in (("lexical_word", 0.70), ("latent", 0.75), ("neural", 0.80), ("fused", 0.80))
] + [  # a non-MRR metric must not reach the table
    {"benchmark": "title_to_body", "channel": "fused", "metric": "cases", "value": 187.0}
]
PULSAR = {"n_opportunities": 187, "benchmark": benchmark_summary(ROWS)}
READING = {"facets": {"overall": 98.0, "domain": 93.0, "methods": 98.0, "skills": 62.0},
           "channels": {"lexical_word": 80.0, "latent": 87.0, "neural": 98.0}}


def test_only_mrr_reaches_the_table():
    summary = PULSAR["benchmark"]
    assert summary["n_tasks"] == 3
    assert 187.0 not in summary["mrr"]["title_to_body"].values()


def test_ties_share_the_better_rank():
    """neural and fused both score 0.80 on the holdout; neither may be '2º'."""
    ranks = PULSAR["benchmark"]["ranks"]["professor_holdout"]
    assert ranks["neural"] == ranks["fused"] == 1


def test_the_caption_never_claims_a_superiority_the_numbers_deny():
    """Fusion wins 1 of 3 tasks here. The sentence must say so, not invert it."""
    caption = method_caption(PULSAR)
    assert "quase nunca é a melhor" in caption
    assert "nunca cai abaixo da 2ª posição" in caption
    # It is the only representation without a bad case, so it earns the clause.
    assert "não tem um caso ruim" in caption


def test_the_caption_drops_the_claim_when_a_rival_is_equally_robust():
    rows = [{"benchmark": b, "channel": c, "metric": "mrr", "value": v}
            for b in ("title_to_body", "cross_project_area")
            for c, v in (("lexical_word", 0.9), ("fused", 0.9))]
    caption = method_caption({"benchmark": benchmark_summary(rows)})
    assert caption and "não tem um caso ruim" not in caption


def test_absent_statistics_produce_no_panel_rather_than_a_broken_one():
    assert method_panel(None) == "" and method_panel({}) == ""
    assert method_caption(None) == "" and method_caption({"benchmark": {}}) == ""
    assert fit_panel(None) == "" and fit_panel({"facets": {}}) == ""


@pytest.mark.parametrize("panel", [method_panel(PULSAR), fit_panel(READING, total=187)])
def test_every_panel_line_is_indented_and_fits_the_window(panel):
    """Indentation is what keeps a panel monospaced; width is what keeps it legible."""
    assert panel
    for line in panel.split("\n"):
        assert line.startswith(INDENT), f"unindented panel line would reflow: {line!r}"
        assert len(line) <= 72, f"line of {len(line)} chars will wrap: {line!r}"


def test_a_panel_survives_the_html_conversion_as_one_preformatted_block():
    body = ("Prosa antes.\n\n" + fit_panel(READING, total=187) + "\n\nProsa depois.")
    html = text_to_html(body)
    assert html.count("<pre") == 1, "the blank line inside a panel must not split it"
    assert "white-space:pre" in html
    assert html.count("<p>") == 2
    # The bars must arrive as drawn, not escaped away or collapsed.
    assert "█" in html and "p98" in html


def test_prose_is_never_turned_into_a_chart_box():
    assert "<pre" not in text_to_html("Uma linha.\n\nOutra linha.")


def test_a_bar_is_drawn_against_a_visible_track():
    assert len(bar(50.0, 100.0, 20)) == 20
    assert bar(0.0, 100.0, 20) == "·" * 20
    assert bar(100.0, 100.0, 20) == "█" * 20
    assert bar(200.0, 100.0, 20) == "█" * 20, "an out-of-range value must not overflow"
    # Sub-character resolution: two nearby values must not draw identically.
    assert bar(93.0, 100.0, 22) != bar(98.0, 100.0, 22)


def test_the_fit_panel_reports_every_facet_it_was_given():
    panel = fit_panel(READING, total=187)
    assert "p98" in panel and "p93" in panel and "p62" in panel
    assert "187 planos avaliados" in panel
    assert "por cada representação sozinha" in panel
