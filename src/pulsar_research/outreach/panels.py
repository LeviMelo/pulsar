"""Monospaced measurement panels for outreach bodies.

An email cannot carry a matplotlib figure without an image the recipient's
client may refuse to load, so the charts here are drawn in text. They render
inside ``<pre>`` in the HTML alternative and as an indented block in the
plaintext one, which is why every line is indented: that indentation is the
signal :func:`..render.text_to_html` uses to decide what must stay monospaced.

Two rules govern what may be drawn:

*   **Never a bar chart of incommensurable quantities.** Counting work plans and
    counting archived pages on the same axis is a chart that lies. Where the
    quantities do not share a scale the panel prints a table instead.
*   **Never a bar chart of a near-tie.** The six representations sit within
    0.09 MRR of one another; drawn as bars they would look identical while
    implying a precision 187 cases do not support. Their *rank per task* carries
    the real finding — fusion rarely wins outright, but it is the only
    representation without a bad case — so the panel ranks them and the caption
    is generated from those ranks rather than asserted.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

# Full block plus the eighth-width partials, so a bar has sub-character
# resolution and two nearby values stay visually distinct.
_PARTIALS = "▏▎▍▌▋▊▉"
_TRACK = "·"
INDENT = "   "

# The seven self-supervised retrieval tasks. Each label is a short description a
# reader can check against intuition — "hide the title and see if it comes back"
# — rather than the benchmark's internal key.
TASK_LABELS: dict[str, str] = {
    "title_to_body": "achar o plano pelo seu título",
    "masked_title": "idem, com o título escondido",
    "objectives_to_methodology": "objetivos → metodologia",
    "sibling_plan": "plano irmão do mesmo projeto",
    "sibling_deduplicated": "idem, sem texto em comum",
    "cross_project_area": "mesma área, outro projeto",
    "professor_holdout": "achar o docente pela obra dele",
}
TASK_ORDER = list(TASK_LABELS)

# The table is transposed — representations down the side, tasks across the top —
# for one reason: it lets each representation carry a name a non-specialist can
# read, and it puts each representation's seven results on one line, where the
# pattern (one row with no bad number) is visible without being explained.
CHANNEL_LABELS: dict[str, str] = {
    "lexical_word": "palavras (TF-IDF)",
    "lexical_char": "trechos de palavra",
    "bm25": "BM25F",
    "bm25f": "BM25F",
    "latent": "sentido, treinado no edital",
    "neural": "neural, rodando local",
    "fused": "combinação (palavras+sentido+neural)",
}
CHANNEL_ORDER = ["lexical_word", "lexical_char", "bm25", "bm25f", "latent", "neural", "fused"]

FACET_LABELS: dict[str, str] = {
    "overall": "no conjunto",
    "domain": "tema do plano",
    "methods": "métodos empregados",
    "skills": "competências exigidas",
}


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


def _decimal(value: float, places: int = 2) -> str:
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
    """Reduce raw ``semantic_benchmarks`` MRR rows to what a panel needs.

    Everything a caption might claim is derived here, so a rebuilt semantic
    space cannot leave a stale superlative inside an email already sent.
    """
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
    worst = {c: max(ranks[t].get(c, len(channels)) for t in tasks) for c in channels}
    fused_ranks = [ranks[t]["fused"] for t in tasks if "fused" in ranks[t]]
    return {
        "channels": channels,
        "tasks": tasks,
        "mrr": by_task,
        "ranks": ranks,
        "means": means,
        "worst_ranks": worst,
        "n_tasks": len(tasks),
        "n_channels": len(channels),
        "fused_wins": sum(1 for r in fused_ranks if r == 1),
        "fused_worst": max(fused_ranks) if fused_ranks else 0,
    }


def method_panel(pulsar: Mapping[str, Any] | None) -> str:
    """Every representation's rank on every task, one representation per row.

    Returns "" when the statistics are absent, so a template can guard on it.
    """
    summary = (pulsar or {}).get("benchmark") or {}
    channels, tasks = summary.get("channels") or [], summary.get("tasks") or []
    if not channels or not tasks:
        return ""

    label_width = max(len(CHANNEL_LABELS.get(c, c)) for c in channels) + 1
    numbers = [str(i) for i in range(1, len(tasks) + 1)]

    def row(label: str, cells: Sequence[str], tail: str) -> str:
        return (f"{INDENT}{label.ljust(label_width)}"
                + "".join(cell.rjust(3) for cell in cells)
                + tail.rjust(7))

    rule = INDENT + "─" * (label_width + 3 * len(tasks) + 7)
    lines = [row("representação", numbers, "média"), rule]
    for channel in channels:
        ranks = [summary["ranks"][t] for t in tasks]
        lines.append(row(
            CHANNEL_LABELS.get(channel, channel),
            [f"{r[channel]}º" if channel in r else "—" for r in ranks],
            # Two decimals, not three: the means span 0.09 across six
            # representations, and a third digit would imply a resolution the
            # sample cannot support.
            _decimal(summary["means"].get(channel, 0.0)),
        ))
    lines.append(rule)
    lines.append(INDENT)
    # The column key, two tasks per line, so the table needs no prior reading.
    pairs = [f"{i} {TASK_LABELS.get(t, t)}" for i, t in enumerate(tasks, 1)]
    for start in range(0, len(pairs), 2):
        lines.append(INDENT + "  ·  ".join(pairs[start:start + 2]))
    return "\n".join(lines)


def method_caption(pulsar: Mapping[str, Any] | None) -> str:
    """One sentence about the table, derived from the table.

    The tempting claim — that the combination is the best representation — is
    not true on this corpus and is never made. What is true is that it is the
    only one without a bad case, and the sentence is emitted only while the
    numbers still say so.
    """
    summary = (pulsar or {}).get("benchmark") or {}
    if not summary:
        return ""
    worst = summary["fused_worst"]
    others = {c: r for c, r in (summary.get("worst_ranks") or {}).items() if c != "fused"}
    sentence = ("Nenhuma delas vence sempre, e a combinação quase nunca é a melhor "
                f"em uma prova isolada. A questão é a última linha: ela nunca cai "
                f"abaixo da {worst}ª posição")
    if others and all(rank > worst for rank in others.values()):
        sentence += (", enquanto cada representação sozinha desaba para as últimas "
                     "colocações em alguma prova. É por isso que o ranqueamento usa "
                     "a combinação: não porque tenha a melhor média, mas porque não "
                     "tem um caso ruim.")
    else:
        sentence += "."
    return sentence


def fit_panel(reading: Mapping[str, Any] | None, *, total: int = 0) -> str:
    """Where one work plan sits, per facet and per representation.

    Percentiles, unlike the MRR values above, genuinely span their range, so
    here bars are the honest display.
    """
    facets = (reading or {}).get("facets") or {}
    channels = (reading or {}).get("channels") or {}
    shown = [f for f in FACET_LABELS if f in facets]
    if not shown:
        return ""

    label_width = max(len(FACET_LABELS[f]) for f in shown) + 2
    scope = f" entre os {total} planos avaliados" if total else ""
    lines = [f"{INDENT}percentil deste plano{scope}", INDENT]
    for facet in shown:
        percentile = float(facets[facet])
        lines.append(f"{INDENT}{FACET_LABELS[facet].ljust(label_width)}"
                     f"{bar(percentile, 100.0, 22)} p{percentile:.0f}")
    agreement = [(CHANNEL_LABELS.get(c, c), channels[c])
                 for c in ("lexical_word", "latent", "neural") if c in channels]
    if agreement:
        # One per line rather than joined: the representation names are the same
        # ones the table above uses, and joined they run past any sane wrap.
        width = max(len(name) for name, _ in agreement) + 2
        lines.append(INDENT)
        lines.append(f"{INDENT}o mesmo plano, medido por cada representação sozinha")
        lines.extend(f"{INDENT}{name.ljust(width)}p{value:.0f}" for name, value in agreement)
    return "\n".join(lines)
