"""The footer summary states measurements to strangers, so it is pinned.

The body no longer carries a chart; what is left is a three-line footer rendered
twice — as a styled card in the HTML alternative and as plain lines in the text
one. Both come from `card_lines`/`build_context`, and the risk that matters is
the two drifting apart or the card outliving the data behind it.
"""
from __future__ import annotations

import re

from pulsar_research.outreach.panels import benchmark_summary, card_lines, decimal

ROWS = [
    {"benchmark": "title_to_body", "channel": c, "metric": "mrr", "value": v}
    for c, v in (("lexical_word", 0.93), ("latent", 0.80), ("neural", 0.76), ("fused", 0.86))
] + [
    {"benchmark": "cross_project_area", "channel": c, "metric": "mrr", "value": v}
    for c, v in (("lexical_word", 0.67), ("latent", 0.70), ("neural", 0.65), ("fused", 0.71))
] + [  # a non-MRR metric must never reach a quoted average
    {"benchmark": "title_to_body", "channel": "fused", "metric": "cases", "value": 187.0}
]
STATS = {"n_opportunities": 187, "n_projects": 89, "n_professors": 65,
         "n_atoms": 15019, "n_pages": 1932, "benchmark": benchmark_summary(ROWS)}
READING = {"facets": {"domain": 93.0, "methods": 98.0, "skills": 62.0}}


def test_only_mrr_reaches_the_average():
    summary = STATS["benchmark"]
    assert summary["n_tasks"] == 2
    assert 0.7 < summary["fused_mrr"] < 0.9, "187 cases must not be averaged in as a score"


def test_the_footer_states_the_corpus_and_the_evaluation():
    lines = card_lines({"stats": STATS, "reading": {}, "percentile": 0.0})
    joined = "\n".join(lines)
    assert "15.019" in joined and "187 planos" in joined
    assert "2 tarefas de recuperação" in joined
    assert "relatório em anexo" in joined, "the detail must be pointed at, not reproduced"


def test_the_footer_reports_a_measured_fit_only_when_one_was_passed():
    without = card_lines({"stats": STATS, "reading": {}, "percentile": 0.0})
    assert not any("este plano" in line for line in without)
    with_fit = "\n".join(card_lines({"stats": STATS, "reading": READING, "percentile": 98.0}))
    assert "este plano, percentil 98:" in with_fit
    # Drawn, not just stated: a bar is the whole reason the footer is worth having.
    assert "█" in with_fit
    # The block-drawing set, spelled out: a literal ▏-▉ inside a character class
    # is a range over code points, not the set of partials.
    glyphs = "█▏▎▍▌▋▊▉·"
    for label, value in (("tema", "p93"), ("métodos", "p98"), ("competências", "p62")):
        assert re.search(rf"{label}\s+[{glyphs}]+\s+{value}", with_fit), f"{label} bar missing"


def test_no_statistics_means_no_footer_rather_than_an_empty_box():
    assert card_lines(None) == []
    assert card_lines({"stats": {}}) == []


def test_the_decimal_mark_is_the_one_the_reader_uses():
    assert decimal(0.8434) == "0,84"
