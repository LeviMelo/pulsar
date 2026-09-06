"""Reducing the stored retrieval benchmarks to the few numbers a report quotes.

This module used to draw panels for the outreach message: a ranked scale, a
per-facet bar chart, a footer card of corpus counts and MRR. All of it is gone.
Telling a professor where his own plan sat in a ranking of his colleagues' plans
is a strange thing to do in a cold email, and the counts under it read as a
product demo rather than as a student writing to a potential supervisor. The
evaluation belongs in the attached report, and that is where it now lives.

What survives is `benchmark_summary`, computed from the store rather than
written down, so a rebuilt semantic space cannot leave a stale number behind.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

# The seven self-supervised retrieval tasks, in the order a reader should meet
# them: literal recall first, generalisation last.
TASK_ORDER = [
    "title_to_body", "masked_title", "objectives_to_methodology",
    "sibling_plan", "sibling_deduplicated", "cross_project_area",
    "professor_holdout",
]
CHANNEL_ORDER = ["lexical_word", "lexical_char", "bm25", "bm25f", "latent", "neural", "fused"]


def decimal(value: float, places: int = 2) -> str:
    """0.843 -> '0,84'. The email is in Portuguese; so is its decimal mark."""
    return f"{value:.{places}f}".replace(".", ",")


def _ranks(values: Mapping[str, float]) -> dict[str, int]:
    """Competition ranking (1 is best), ties sharing the better rank."""
    ordered = sorted(values.items(), key=lambda kv: -kv[1])
    out: dict[str, int] = {}
    for index, (key, value) in enumerate(ordered):
        prior = next((k for k, v in ordered[:index] if v == value), None)
        out[key] = out[prior] if prior is not None else index + 1
    return out


def benchmark_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Reduce raw ``semantic_benchmarks`` MRR rows to what the footer needs."""
    by_task: dict[str, dict[str, float]] = {}
    for row in rows:
        if row.get("metric") != "mrr":
            continue
        by_task.setdefault(str(row["benchmark"]), {})[str(row["channel"])] = float(row["value"])
    if not by_task:
        return {}

    channels = [c for c in CHANNEL_ORDER if any(c in v for v in by_task.values())]
    tasks = [t for t in TASK_ORDER if t in by_task] + \
            [t for t in by_task if t not in TASK_ORDER]
    ranks = {task: _ranks({c: v for c, v in by_task[task].items() if c in channels})
             for task in tasks}
    means = {c: sum(by_task[t][c] for t in tasks if c in by_task[t]) /
                max(1, sum(1 for t in tasks if c in by_task[t]))
             for c in channels}
    fused_ranks = [ranks[t]["fused"] for t in tasks if "fused" in ranks[t]]
    return {
        "channels": channels,
        "tasks": tasks,
        "means": means,
        "n_tasks": len(tasks),
        "fused_mrr": means.get("fused", 0.0),
        "fused_worst": max(fused_ranks) if fused_ranks else 0,
    }
