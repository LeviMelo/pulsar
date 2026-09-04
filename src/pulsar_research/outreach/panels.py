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


# Full block plus the eighth-width partials, so a bar has sub-character
# resolution and two nearby values stay visually distinct.
_PARTIALS = "▏▎▍▌▋▊▉"
_TRACK = "·"
INDENT = "   "


def bar(value: float, maximum: float, width: int) -> str:
    """A horizontal bar of `width` cells, with an explicit track behind it.

    The track matters: without it a reader cannot tell a short bar from a
    truncated axis.
    """
    if maximum <= 0 or width <= 0:
        return _TRACK * max(width, 0)
    cells = max(0.0, min(1.0, value / maximum)) * width
    full = int(cells)
    out = "█" * min(full, width)
    remainder = cells - full
    if full < width and remainder >= 1 / 16:
        out += _PARTIALS[min(len(_PARTIALS) - 1, int(remainder * 8))]
    return out + _TRACK * (width - len(out))


def rank_scale(rank: int, total: int, width: int = 38) -> str:
    """One line placing a plan on the whole ranked list.

    "5º de 187" is already concrete; seeing the marker sit hard against the left
    end is what makes it land. Deliberately one line — the previous version of
    this message carried a nine-row table and buried the question being asked.
    """
    if rank <= 0 or total <= 1:
        return ""
    position = min(width - 1, max(0, round((rank - 1) / (total - 1) * (width - 1))))
    return f"{INDENT}1º ├{'─' * position}●{'─' * (width - 1 - position)}┤ {total}º"


def fit_bars(reading: Mapping[str, Any] | None, width: int = 16) -> list[tuple[str, str, str]]:
    """`(label, bar, value)` per facet, for the footer in either alternative.

    Returned as parts rather than a formatted string so the HTML card and the
    plaintext footer lay them out for their own medium while sharing the values.
    """
    facets = (reading or {}).get("facets") or {}
    return [(FACET_LABELS[key], bar(float(facets[key]), 100.0, width), f"p{float(facets[key]):.0f}")
            for key in FACET_LABELS if key in facets]


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
    bars = fit_bars((card or {}).get("reading")) if (card or {}).get("percentile") else []
    if bars:
        lines.append("")
        lines.append(f"este plano, percentil {float(card['percentile']):.0f}:")
        pad = max(len(label) for label, _, _ in bars) + 1
        lines.extend(f"  {label.ljust(pad)}{drawn}  {value}" for label, drawn, value in bars)
    return lines
