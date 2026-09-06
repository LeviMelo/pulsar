"""The benchmark reduction, which is the last thing this module still does.

The panels it used to draw — the ranked scale, the per-facet bars, the footer
card — were removed from the outreach message: a cold email is not the place to
tell a professor his position in a ranking of his colleagues. What is pinned
here is the arithmetic, because the report still quotes it and a non-MRR metric
sneaking into an averaged score would misstate a measurement to a reader.
"""
from __future__ import annotations

from pulsar_research.outreach.panels import benchmark_summary, decimal

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

def test_only_mrr_reaches_the_average():
    summary = STATS["benchmark"]
    assert summary["n_tasks"] == 2
    assert 0.7 < summary["fused_mrr"] < 0.9, "187 cases must not be averaged in as a score"

def test_the_decimal_mark_is_the_one_the_reader_uses():
    assert decimal(0.8434) == "0,84"
