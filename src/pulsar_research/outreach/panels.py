"""The compact measurement summary that rides in the footer of an outreach mail.

An earlier version drew the whole retrieval battery into the message body as a
monospaced table. It was accurate and it was wrong for the medium: four
paragraphs of method stood between a professor reading on deadline day and the
question being asked, and the monospaced blocks made the message awkward to
quote or copy. The battery now lives in the attached report, where a reader who
wants it will find it, and only a three-line summary survives here.

`benchmark_summary` is still computed from the store rather than written down,
so a rebuilt semantic space cannot leave a stale number inside an email.
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

FACET_LABELS: dict[str, str] = {
    "domain": "tema",
    "methods": "métodos",
    "skills": "competências",
}


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


def card_lines(card: Mapping[str, Any] | None) -> list[str]:
    """The footer, as plain lines.

    The same values the HTML card renders, so the two alternatives of the
    message cannot drift apart: one function, two presentations.
    """
    stats = (card or {}).get("stats") or {}
    if not stats:
        return []
    lines = ["PULSAR — sistema de prospecção de oportunidades de pesquisa, "
             "desenvolvido por mim"]
    lines.append(
        f"{stats.get('n_opportunities', 0):,}".replace(",", ".") + " planos · "
        + f"{stats.get('n_projects', 0):,}".replace(",", ".") + " projetos · "
        + f"{stats.get('n_professors', 0):,}".replace(",", ".") + " docentes · "
        + f"{stats.get('n_atoms', 0):,}".replace(",", ".") + " registros do SIGAA e do Lattes"
    )
    benchmark = stats.get("benchmark") or {}
    if benchmark:
        lines.append(
            f"ranqueamento avaliado em {benchmark['n_tasks']} tarefas de recuperação "
            f"(MRR {decimal(benchmark['fused_mrr'])}); método e resultados no relatório em anexo"
        )
    facets = ((card or {}).get("reading") or {}).get("facets") or {}
    shown = [f"{FACET_LABELS[f]} p{float(facets[f]):.0f}" for f in FACET_LABELS if f in facets]
    if shown and card.get("percentile"):
        lines.append(f"este plano: percentil {float(card['percentile']):.0f} · " + " · ".join(shown))
    return lines
