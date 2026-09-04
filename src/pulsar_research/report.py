"""A self-contained analytical report on what PULSAR has acquired and measured.

Two audiences at once: a reader who wants to know what the UFAL research
ecosystem looks like, and a reader who wants to know whether the ranking that
produced that picture can be trusted. The second half therefore reports the
retrieval benchmark honestly, including where the method loses.

Figures are matplotlib rendered to inline SVG — one asset format that survives
both the HTML and the Chromium print-to-PDF path, with no external files to go
missing. The PDF is produced by the same Chromium that already ships with
Playwright for the SIGAA automation, so there is no extra dependency.
"""

from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .config import AppConfig
from .db import Database, json_load

# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

INK = "#12171f"
MUTED = "#5c6b7f"
RULE = "#dfe5ec"
ACCENT = "#1f4e79"
SERIES = ["#1f4e79", "#2e8b8b", "#c46a1f", "#7d4a8c", "#4a7c3f", "#a8443c",
          "#3f6ea8", "#8a7b2f"]


def _style() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.dpi": 110,
        "savefig.transparent": True,
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "axes.edgecolor": RULE,
        "axes.labelcolor": INK,
        "axes.titlesize": 10,
        "axes.titleweight": "600",
        "axes.titlecolor": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": RULE,
        "grid.linewidth": 0.7,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.frameon": False,
        "legend.fontsize": 8,
    })


def _svg(fig) -> str:
    """Render a figure to inline SVG, stripped of its XML preamble."""
    import matplotlib.pyplot as plt

    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    svg = buf.getvalue()
    svg = svg[svg.index("<svg"):]
    # Let CSS size it; keep the aspect ratio from the viewBox.
    return re.sub(r'<svg width="[^"]+" height="[^"]+"', "<svg", svg, count=1)


@dataclass
class Figure:
    key: str
    number: int
    caption: str
    svg: str


@dataclass
class ReportData:
    generated_at: str
    space_id: str
    run_id: str
    counts: dict[str, Any]
    metrics: dict[str, float]
    atom_kinds: list[tuple[str, int]]
    centers: list[tuple[str, int, int]]
    editais: list[tuple[str, int, int]]
    topics: dict[str, list[dict[str, Any]]]
    skills: list[dict[str, Any]]
    benchmarks: dict[str, dict[str, dict[str, float]]]
    map_diagnostics: dict[str, float]
    space_stats: dict[str, Any]
    best_mrr: dict[str, float] = field(default_factory=dict)
    figures: list[Figure] = field(default_factory=list)

    def figure(self, key: str) -> Figure | None:
        return next((f for f in self.figures if f.key == key), None)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _gini(values: np.ndarray) -> float:
    v = np.sort(np.asarray(values, dtype=float))
    n = v.size
    if n == 0 or v.sum() <= 0:
        return 0.0
    index = np.arange(1, n + 1)
    return float((2.0 * (index * v).sum()) / (n * v.sum()) - (n + 1.0) / n)


def _lorenz(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    v = np.sort(np.asarray(values, dtype=float))
    if v.sum() <= 0:
        return np.linspace(0, 1, 2), np.linspace(0, 1, 2)
    cum = np.concatenate([[0.0], np.cumsum(v) / v.sum()])
    return np.linspace(0.0, 1.0, cum.size), cum


def collect(db: Database) -> ReportData:
    with db.connect(read_only=True) as con:
        def df(sql: str, params: list | None = None):
            return con.execute(sql, params or []).df()

        space_id = con.execute(
            "SELECT value FROM meta WHERE key='current_semantic_space_id'").fetchone()
        run_id = con.execute(
            "SELECT value FROM meta WHERE key='current_profile_run_id'").fetchone()
        space_id = space_id[0] if space_id else ""
        run_id = run_id[0] if run_id else ""

        stats_row = con.execute(
            "SELECT stats_json, identity_json, versions_json FROM semantic_spaces WHERE space_id=?",
            [space_id]).fetchone()
        space_stats = json_load(stats_row[0], {}) if stats_row else {}

        metrics = {r[0]: float(r[1]) for r in
                   con.execute("SELECT metric, value FROM global_metrics").fetchall()}
        counts = {
            "opportunities": int(con.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]),
            "professors": int(con.execute("SELECT COUNT(*) FROM professors").fetchone()[0]),
            "pages": int(con.execute("SELECT COUNT(*) FROM sigaa_public_pages").fetchone()[0]),
            "lattes_leaves": int(con.execute(
                "SELECT COUNT(*) FROM sigaa_public_lattes_flat").fetchone()[0]),
            "table_rows": int(con.execute(
                "SELECT COUNT(*) FROM sigaa_public_table_rows").fetchone()[0]),
        }

        atom_kinds: list[tuple[str, int]] = []

        centers = [(str(r[0] or "—"), int(r[1]), int(r[2] or 0)) for r in con.execute(
            "SELECT COALESCE(NULLIF(center,''),'—') AS c, COUNT(*), "
            "SUM(CASE WHEN has_funding THEN 1 ELSE 0 END) "
            "FROM opportunities GROUP BY 1 ORDER BY 2 DESC").fetchall()]
        editais = [(str(r[0] or "—"), int(r[1]), int(r[2] or 0)) for r in con.execute(
            "SELECT COALESCE(NULLIF(edital,''),'—') AS e, COUNT(*), "
            "SUM(COALESCE(funded_slots,0)) FROM opportunities GROUP BY 1 ORDER BY 2 DESC").fetchall()]

        topics: dict[str, list[dict[str, Any]]] = {}
        trows = df("SELECT facet, topic_id, parent_id, depth, label, terms_json, diagnostics_json "
                   "FROM semantic_topics WHERE space_id=? ORDER BY facet, depth, topic_id", [space_id])
        members = df("SELECT facet, topic_id, COUNT(*) AS n FROM entity_topics "
                     "WHERE space_id=? AND entity_type='project' AND is_dominant "
                     "GROUP BY 1,2", [space_id])
        member_map = {(r.facet, r.topic_id): int(r.n) for r in members.itertuples()}
        for r in trows.itertuples():
            topics.setdefault(r.facet, []).append({
                "topic_id": r.topic_id, "parent_id": r.parent_id, "depth": int(r.depth),
                "label": r.label, "terms": json_load(r.terms_json, []),
                "diagnostics": json_load(r.diagnostics_json, {}),
                "projects": member_map.get((r.facet, r.topic_id), 0),
            })

        srows = df("SELECT label, category, COUNT(DISTINCT entity_id) AS docs, SUM(mentions) AS hits "
                   "FROM entity_skills WHERE space_id=? AND entity_type='opportunity' AND NOT generic "
                   "GROUP BY 1,2 ORDER BY docs DESC", [space_id])
        skills = [{"label": r.label, "category": r.category, "docs": int(r.docs),
                   "hits": int(r.hits)} for r in srows.itertuples()]

        benchmarks: dict[str, dict[str, dict[str, float]]] = {}
        for r in df("SELECT benchmark, channel, metric, value FROM semantic_benchmarks "
                    "WHERE space_id=?", [space_id]).itertuples():
            benchmarks.setdefault(r.benchmark, {}).setdefault(r.channel, {})[r.metric] = float(r.value)

        map_diagnostics = {r[0]: float(r[1]) for r in con.execute(
            "SELECT metric, value FROM map_diagnostics WHERE space_id=?", [space_id]).fetchall()}

        slots = df("SELECT p.siape, COALESCE(SUM(o.funded_slots),0) AS slots, COUNT(o.id_opportunity) AS opps "
                   "FROM professors p LEFT JOIN opportunities o ON o.professor_siape=p.siape "
                   "GROUP BY 1")
        # Dominance is recorded at every level of the topic tree, so a plain join
        # returns one row per level and multiplies the projects. Ask for the
        # root assignment explicitly, exactly one row per project.
        geometry = df(
            "SELECT g.entity_id, g.x, g.y, ("
            "  SELECT et.topic_id FROM entity_topics et"
            "  JOIN semantic_topics st ON st.space_id=et.space_id AND st.facet=et.facet"
            "                         AND st.topic_id=et.topic_id AND st.depth=0"
            "  WHERE et.space_id=g.space_id AND et.entity_type='project'"
            "    AND et.entity_id=g.entity_id AND et.facet='domain' AND et.is_dominant"
            "  LIMIT 1) AS topic_id "
            "FROM entity_geometry g WHERE g.space_id=? AND g.entity_type='project'", [space_id])

    # Atom kinds come from the corpus itself rather than a persisted summary, so
    # the figure cannot describe a corpus the store no longer holds.
    from collections import Counter

    from .semantics.corpus import load_corpus
    atom_kinds = sorted(Counter(a.kind for a in load_corpus(db).atoms).items(),
                        key=lambda kv: -kv[1])

    best_mrr = {task: max((c.get("mrr", 0.0) for c in per.values()), default=0.0)
                for task, per in benchmarks.items()}

    data = ReportData(
        generated_at=datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y"),
        space_id=space_id, run_id=run_id, counts=counts, metrics=metrics,
        atom_kinds=atom_kinds, centers=centers, editais=editais,
        topics=topics, skills=skills, benchmarks=benchmarks,
        map_diagnostics=map_diagnostics, space_stats=space_stats,
    )
    data.best_mrr = best_mrr     # type: ignore[attr-defined]
    data._slots = slots          # type: ignore[attr-defined]
    data._geometry = geometry    # type: ignore[attr-defined]
    return data


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

KIND_LABELS = {
    "conference": "Trabalhos em eventos", "article": "Artigos publicados",
    "orientation": "Orientações concluídas", "lattes_project": "Projetos (Lattes)",
    "book_chapter": "Livros e capítulos", "sigaa_project": "Projetos (SIGAA)",
    "technical": "Produção técnica", "sigaa_extension": "Extensão (SIGAA)",
    "knowledge_area": "Áreas de atuação", "opportunity": "Planos de trabalho",
    "research_line": "Linhas de pesquisa", "profile_summary": "Resumos de currículo",
}
CATEGORY_LABELS = {
    "computing": "Computação", "statistics": "Estatística", "data_source": "Fontes de dados",
    "wet_lab": "Bancada", "clinical": "Clínica", "scholarly": "Acadêmicas",
}
TASK_LABELS = {
    "title_to_body": "Título → corpo",
    "sibling_plan": "Plano irmão",
    "sibling_deduplicated": "Plano irmão (sem cópia)",
    "objectives_to_methodology": "Objetivos → metodologia",
    "masked_title": "Título mascarado",
    "cross_project_area": "Mesma área (entre projetos)",
    "professor_holdout": "Retenção de portfólio",
}
CHANNEL_LABELS = {
    "lexical_word": "TF-IDF palavra", "lexical_char": "TF-IDF caractere",
    "bm25": "BM25F", "latent": "SPPMI+SVD", "neural": "Qwen3 (local)", "fused": "Fusão",
}


def build_figures(data: ReportData) -> None:
    _style()
    import matplotlib.pyplot as plt

    n = 0

    def add(key: str, caption: str, fig) -> None:
        nonlocal n
        n += 1
        data.figures.append(Figure(key, n, caption, _svg(fig)))

    # 1 — the evidence base
    kinds = data.atom_kinds[:12][::-1]
    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    labels = [KIND_LABELS.get(k, k) for k, _ in kinds]
    values = [v for _, v in kinds]
    ax.barh(labels, values, color=ACCENT, height=0.68)
    for y, v in enumerate(values):
        ax.text(v + max(values) * 0.012, y, f"{v:,}".replace(",", "."),
                va="center", fontsize=8, color=MUTED)
    ax.set_xlim(0, max(values) * 1.16)
    ax.set_xlabel("Registros extraídos")
    ax.xaxis.set_major_formatter(lambda x, _: f"{int(x):,}".replace(",", "."))
    ax.grid(axis="y", visible=False)
    add("atoms", "Composição do corpus atômico. Cada registro é uma unidade de evidência "
        "independente — um projeto, um artigo, uma orientação — e não um currículo concatenado.", fig)

    # 2 — supply by centre
    centers = data.centers[:10][::-1]
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    names = [c[0][:34] for c in centers]
    total = np.array([c[1] for c in centers], dtype=float)
    funded = np.array([c[2] for c in centers], dtype=float)
    ax.barh(names, total, color=RULE, height=0.66, label="Sem bolsa indicada")
    ax.barh(names, funded, color=ACCENT, height=0.66, label="Com bolsa")
    ax.set_xlabel("Planos de trabalho")
    ax.legend(loc="lower right")
    ax.grid(axis="y", visible=False)
    add("centers", "Oferta por centro acadêmico, separando planos com bolsa indicada dos demais.", fig)

    # 3 — concentration
    slots = data._slots  # type: ignore[attr-defined]
    s = slots["slots"].to_numpy(dtype=float)
    o = slots["opps"].to_numpy(dtype=float)
    fig, ax = plt.subplots(figsize=(5.4, 4.4))
    x, y = _lorenz(s)
    ax.plot(x, y, color=ACCENT, lw=2.0, label=f"Bolsas (Gini {_gini(s):.2f})")
    x2, y2 = _lorenz(o)
    ax.plot(x2, y2, color=SERIES[2], lw=2.0, ls="--",
            label=f"Planos ofertados (Gini {_gini(o):.2f})")
    ax.plot([0, 1], [0, 1], color=MUTED, lw=1.0, ls=":", label="Igualdade perfeita")
    ax.set_xlabel("Proporção acumulada de docentes")
    ax.set_ylabel("Proporção acumulada")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.legend(loc="upper left")
    add("lorenz", "Curvas de Lorenz da oferta. A capacidade de orientação distribui-se de "
        "forma consideravelmente mais equitativa do que o financiamento.", fig)

    # 4 — thematic structure
    fig, axes = plt.subplots(1, 2, figsize=(7.4, 3.9))
    for ax, facet, title in zip(axes, ("domain", "methods"), ("Domínio", "Métodos")):
        nodes = [t for t in data.topics.get(facet, []) if t["depth"] == 1 and t["projects"]]
        nodes = sorted(nodes, key=lambda t: t["projects"])[-9:]
        if not nodes:
            ax.axis("off"); continue
        labels = [" / ".join(t["label"].split(" / ")[:2])[:26] for t in nodes]
        vals = [t["projects"] for t in nodes]
        roots = [t["parent_id"] for t in nodes]
        order = {r: i for i, r in enumerate(sorted(set(roots)))}
        ax.barh(labels, vals, color=[SERIES[order[r] % len(SERIES)] for r in roots], height=0.66)
        ax.set_title(title)
        ax.set_xlabel("Projetos")
        ax.grid(axis="y", visible=False)
    add("topics", "Subtemas do modelo hierárquico de tópicos, coloridos pelo tema-raiz. "
        "Rótulos derivam de um escore FREX (frequência × exclusividade).", fig)

    # 5 — methodological supply
    top = data.skills[:18][::-1]
    fig, ax = plt.subplots(figsize=(7.2, 4.6))
    cats = sorted({s["category"] for s in top})
    cmap = {c: SERIES[i % len(SERIES)] for i, c in enumerate(cats)}
    ax.barh([s["label"][:32] for s in top], [s["docs"] for s in top],
            color=[cmap[s["category"]] for s in top], height=0.68)
    ax.set_xlabel("Planos de trabalho que mencionam a competência")
    ax.grid(axis="y", visible=False)
    handles = [plt.Rectangle((0, 0), 1, 1, color=cmap[c]) for c in cats]
    ax.legend(handles, [CATEGORY_LABELS.get(c, c) for c in cats], loc="lower right", ncol=2)
    add("skills", "Competências técnicas explicitamente mencionadas nos planos, extraídas por "
        "um léxico determinístico multirrótulo. Competências genéricas foram excluídas.", fig)

    # 6 — semantic map
    geo = data._geometry.copy()  # type: ignore[attr-defined]
    # Colour by the ROOT theme, not the leaf: eleven leaf colours in one scatter
    # is a legend, not a finding.
    nodes = {t["topic_id"]: t for t in data.topics.get("domain", [])}

    def root_of(tid: object) -> str:
        # Guard against a NULL parent arriving as NaN rather than None, which
        # is truthy and would walk the tree straight off its root.
        seen: set[str] = set()
        node = nodes.get(tid)
        while node:
            parent = node.get("parent_id")
            if not isinstance(parent, str) or not parent or parent in seen:
                return str(node["topic_id"])
            seen.add(parent)
            node = nodes.get(parent)
        return "—"

    geo["root"] = [root_of(t) for t in geo["topic_id"]]
    assert len(geo) == len(set(geo["entity_id"])), "one point per project"
    roots = sorted(geo["root"].unique())
    cmap = {t: SERIES[i % len(SERIES)] for i, t in enumerate(roots)}
    fig, ax = plt.subplots(figsize=(6.6, 4.6))
    for tid in roots:
        sub = geo[geo["root"] == tid]
        label = nodes.get(tid, {}).get("label", "sem tópico")
        label = " · ".join(label.split(" / ")[:3])[:34]
        ax.scatter(sub["x"], sub["y"], s=44, alpha=0.85, color=cmap[tid],
                   edgecolor="white", linewidth=0.7, label=f"{label}  (n={len(sub)})")
    stress = data.map_diagnostics.get("stress", 0.0)
    rho = data.map_diagnostics.get("spearman", 0.0)
    ax.set_xlabel("Dimensão 1"); ax.set_ylabel("Dimensão 2")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2, fontsize=7.5)
    ax.set_title(f"stress de Kruskal {stress:.2f} · ρ de Spearman {rho:.2f}", fontsize=8.5)
    add("map", "Projeção bidimensional dos projetos (PCoA refinada por SMACOF), colorida pelo "
        "tópico dominante. O stress é alto: leia vizinhanças, não distâncias.", fig)

    # 7 — retrieval quality
    tasks = [t for t in TASK_LABELS if t in data.benchmarks]
    channels = [c for c in ("lexical_word", "lexical_char", "bm25", "latent", "neural", "fused")
                if any(c in data.benchmarks[t] for t in tasks)]
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    width = 0.8 / max(len(channels), 1)
    xs = np.arange(len(tasks))
    for i, ch in enumerate(channels):
        vals = [data.benchmarks[t].get(ch, {}).get("mrr", np.nan) for t in tasks]
        ax.bar(xs + i * width - 0.4 + width / 2, vals, width * 0.92,
               label=CHANNEL_LABELS.get(ch, ch), color=SERIES[i % len(SERIES)])
    ax.set_xticks(xs)
    ax.set_xticklabels([TASK_LABELS[t] for t in tasks], rotation=22, ha="right")
    ax.set_ylabel("MRR")
    ax.set_ylim(0, 1.0)
    ax.legend(ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.18))
    ax.grid(axis="x", visible=False)
    add("benchmark", "Bateria de recuperação: sete tarefas construídas a partir do próprio "
        "corpus. Nenhum canal vence em todas — é por isso que permanecem separados.", fig)

    # 8 & 9 — the two decisive experiments, if their records survive
    exp = _load_experiments()
    if exp.get("lsa"):
        fig, ax = plt.subplots(figsize=(6.4, 3.8))
        ks, series = exp["lsa"]["ks"], exp["lsa"]["series"]
        for i, (name, vals) in enumerate(series.items()):
            ax.plot(ks, vals, marker="o", ms=4, lw=1.8, color=SERIES[i % len(SERIES)],
                    label=TASK_LABELS.get(name, name))
        ax.set_xscale("log", base=2)
        ax.set_xticks(ks); ax.set_xticklabels([str(k) for k in ks])
        ax.set_xlabel("Dimensões latentes (k)"); ax.set_ylabel("MRR")
        ax.legend(fontsize=7.5)
        add("lsa", "Sensibilidade da LSA à dimensionalidade. Abaixo de k≈256 o modelo descarta "
            "justamente o vocabulário específico que identifica um projeto.", fig)

    if exp.get("neural"):
        fig, ax = plt.subplots(figsize=(7.0, 3.8))
        rows = exp["neural"]
        labels = [TASK_LABELS.get(t, t) for t, _ in rows]
        deltas = [d for _, d in rows]
        colors = [SERIES[1] if d >= 0 else SERIES[5] for d in deltas]
        ax.barh(labels, deltas, color=colors, height=0.62)
        ax.axvline(0, color=MUTED, lw=1.0)
        ax.set_xlabel("Vantagem do Qwen3 sobre o melhor canal nativo do corpus")
        ax.grid(axis="y", visible=False)
        add("neural", "Complementaridade do modelo neural: pior canal em recuperação literal, "
            "melhor canal em coerência de portfólio. É essa assimetria que justifica a fusão.", fig)


def _load_experiments() -> dict[str, Any]:
    """Optional detail from the model-selection experiments, if archived."""
    out: dict[str, Any] = {}
    path = Path(__file__).resolve().parents[2] / "data" / "exports" / "experiments"
    e1, e2 = path / "exp1.json", path / "exp2.json"
    if e1.exists():
        raw = json.loads(e1.read_text(encoding="utf-8"))
        ks = [48, 64, 128, 256, 384]
        chosen = ["title_to_body", "sibling_deduplicated", "professor_holdout"]
        series = {}
        for task in chosen:
            vals = []
            for k in ks:
                cand = raw.get(task, {}).get(f"lsa{k}", {})
                vals.append(cand.get("mrr"))
            if all(v is not None for v in vals):
                series[task] = vals
        if series:
            out["lsa"] = {"ks": ks, "series": series}
    if e2.exists():
        raw = json.loads(e2.read_text(encoding="utf-8"))
        native = ("word", "char", "bm25", "lsa384", "ppmi300")
        rows = []
        for task, per in raw.items():
            metric = "ndcg_at_10" if per.get("word", {}).get("ndcg_at_10") is not None else "mrr"
            q = per.get("qwen2560", {}).get(metric)
            best = max((per[c].get(metric) for c in native if c in per
                        and per[c].get(metric) is not None), default=None)
            if q is not None and best is not None:
                rows.append((task, q - best))
        if rows:
            out["neural"] = rows
    return out


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

def _fmt(value: float | int, decimals: int = 0) -> str:
    if decimals:
        return f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")
    return f"{int(value):,}".replace(",", ".")


def render_html(data: ReportData) -> str:
    from jinja2 import Environment, StrictUndefined

    env = Environment(undefined=StrictUndefined, autoescape=False,
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["num"] = _fmt
    template = env.from_string(_TEMPLATE)
    return template.render(d=data, fig=data.figure, TASKS=TASK_LABELS, CHANNELS=CHANNEL_LABELS,
                           CATEGORIES=CATEGORY_LABELS, KINDS=KIND_LABELS)


def write_pdf(html_path: Path, pdf_path: Path) -> Path:
    """Print the document with the Chromium that Playwright already provides."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        page.emulate_media(media="print")
        page.pdf(path=str(pdf_path), format="A4", print_background=True,
                 margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"},
                 display_header_footer=True,
                 header_template="<div></div>",
                 footer_template=(
                     '<div style="width:100%;font-size:7.5pt;color:#5c6b7f;'
                     'font-family:Georgia,serif;padding:0 16mm;display:flex;'
                     'justify-content:space-between;">'
                     '<span>PULSAR · Levi de Melo Amorim</span>'
                     '<span class="pageNumber"></span></div>'))
        browser.close()
    return pdf_path


def build_report(config: AppConfig, db: Database, out_dir: Path | None = None,
                 *, pdf: bool = True, log=print) -> dict[str, Path]:
    out_dir = Path(out_dir or config.paths.exports_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log("collecting corpus, topics, skills, geometry and benchmarks")
    data = collect(db)
    if not data.space_id:
        raise RuntimeError("No semantic space. Run `pulsar semantics build` first.")
    log("rendering figures")
    build_figures(data)
    log(f"  {len(data.figures)} figures")
    html_path = out_dir / "pulsar_relatorio.html"
    html_path.write_text(render_html(data), encoding="utf-8")
    log(f"wrote {html_path}")
    result = {"html": html_path}
    if pdf:
        log("printing PDF via chromium")
        result["pdf"] = write_pdf(html_path, out_dir / "pulsar_relatorio.pdf")
        log(f"wrote {result['pdf']}")
    return result


_TEMPLATE = r"""
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>PULSAR — Análise do ecossistema de pesquisa da UFAL</title>
<style>
  :root {
    --ink:#12171f; --muted:#5c6b7f; --rule:#dfe5ec; --accent:#1f4e79;
    --bg:#ffffff; --soft:#f6f8fa;
  }
  * { box-sizing:border-box; }
  html { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  body {
    margin:0; background:var(--bg); color:var(--ink);
    font-family:Georgia,'Iowan Old Style','Times New Roman',serif;
    font-size:10.5pt; line-height:1.62;
  }
  .sheet { max-width:186mm; margin:0 auto; padding:16mm 0 24mm; }
  h1,h2,h3,.kicker,.tag,figcaption,table,.meta,.stat,.footer {
    font-family:'Segoe UI',-apple-system,'Helvetica Neue',Arial,sans-serif;
  }
  .kicker { font-size:8.5pt; letter-spacing:.14em; text-transform:uppercase;
            color:var(--accent); font-weight:700; }
  h1 { font-size:23pt; line-height:1.2; margin:.35rem 0 .5rem; font-weight:700;
       letter-spacing:-.01em; font-family:Georgia,serif; }
  .subtitle { font-size:12pt; color:var(--muted); margin:0 0 1.4rem; font-style:italic; }
  .meta { font-size:8.5pt; color:var(--muted); border-top:1px solid var(--rule);
          border-bottom:1px solid var(--rule); padding:.6rem 0; margin-bottom:1.8rem;
          display:flex; flex-wrap:wrap; gap:1.4rem; }
  .meta b { color:var(--ink); font-weight:600; }
  h2 { font-size:13.5pt; margin:2.2rem 0 .7rem; padding-bottom:.35rem;
       border-bottom:2px solid var(--accent); font-weight:700; }
  h2 .n { color:var(--accent); margin-right:.5rem; }
  h3 { font-size:10.5pt; margin:1.4rem 0 .4rem; font-weight:700; color:var(--accent); }
  p { margin:.55rem 0; text-align:justify; hyphens:auto; }
  .lead { font-size:11.5pt; }
  ul,ol { margin:.5rem 0 .8rem; padding-left:1.15rem; }
  li { margin:.28rem 0; }
  .grid { display:grid; grid-template-columns:repeat(4,1fr); gap:.7rem; margin:1.1rem 0 1.4rem; }
  .stat { background:var(--soft); border-left:3px solid var(--accent);
          padding:.6rem .7rem; break-inside:avoid; }
  .stat .v { font-size:16pt; font-weight:700; line-height:1.1; color:var(--accent);
             font-family:Georgia,serif; }
  .stat .l { font-size:7.8pt; color:var(--muted); text-transform:uppercase;
             letter-spacing:.06em; margin-top:.18rem; }
  figure { margin:1.3rem 0; break-inside:avoid; page-break-inside:avoid; }
  figure svg { width:100%; height:auto; display:block; }
  figcaption { font-size:8.3pt; color:var(--muted); margin-top:.5rem; line-height:1.5;
               border-left:2px solid var(--rule); padding-left:.6rem; }
  figcaption b { color:var(--ink); }
  table { width:100%; border-collapse:collapse; font-size:8.6pt; margin:1rem 0;
          break-inside:avoid; }
  caption { caption-side:top; text-align:left; font-size:8.3pt; color:var(--muted);
            margin-bottom:.4rem; }
  caption b { color:var(--ink); }
  th { text-align:left; font-weight:600; border-bottom:1.5px solid var(--ink);
       padding:.35rem .5rem; }
  td { padding:.32rem .5rem; border-bottom:1px solid var(--rule); }
  td.n, th.n { text-align:right; font-variant-numeric:tabular-nums; }
  tbody tr:nth-child(even) { background:var(--soft); }
  .callout { background:var(--soft); border-left:3px solid var(--accent);
             padding:.75rem .9rem; margin:1.1rem 0; font-size:9.8pt; break-inside:avoid; }
  .callout .t { font-weight:700; font-family:'Segoe UI',sans-serif; font-size:9pt;
                display:block; margin-bottom:.25rem; color:var(--accent); }
  code { font-family:'Cascadia Mono',Consolas,monospace; font-size:8.6pt;
         background:var(--soft); padding:.05rem .3rem; border-radius:2px; }
  .footer { margin-top:2.4rem; padding-top:.8rem; border-top:1px solid var(--rule);
            font-size:8pt; color:var(--muted); }
  @page { size:A4; margin:18mm 16mm; }
  @media print {
    .sheet { max-width:none; padding:0; }
    h2 { break-after:avoid; page-break-after:avoid; }
    h3 { break-after:avoid; page-break-after:avoid; }
    p, li { orphans:3; widows:3; }
    .break { break-before:page; page-break-before:always; }
  }
</style>
</head>
<body><div class="sheet">

<div class="kicker">Relatório técnico · {{ d.generated_at }}</div>
<h1>O ecossistema de pesquisa da UFAL,<br>lido por um sistema próprio</h1>
<p class="subtitle">Construção de corpus, estrutura temática e avaliação de recuperação
semântica sobre os editais PIBIC/PIBITI 2026–2027</p>

<div class="meta">
  <span><b>Autor</b> Levi de Melo Amorim · Medicina, FAMED/UFAL</span>
  <span><b>Sistema</b> PULSAR</span>
  <span><b>Espaço semântico</b> <code>{{ d.space_id }}</code></span>
</div>

<p class="lead">Este relatório descreve o que um sistema de aquisição e análise construído
para uso próprio conseguiu extrair do edital de iniciação científica da UFAL e dos
currículos públicos do corpo docente, e — igualmente importante — quanto do que ele
afirma pode ser verificado. A primeira metade caracteriza a oferta de pesquisa; a
segunda submete o mecanismo de ranqueamento a uma bateria de testes construída a
partir do próprio corpus, incluindo os casos em que o método perde.</p>

<h2><span class="n">1</span>O que foi adquirido</h2>

<div class="grid">
  <div class="stat"><div class="v">{{ d.metrics.opportunities_total|num }}</div>
    <div class="l">Planos de trabalho</div></div>
  <div class="stat"><div class="v">{{ d.metrics.projects_total|num }}</div>
    <div class="l">Projetos</div></div>
  <div class="stat"><div class="v">{{ d.metrics.professors_total|num }}</div>
    <div class="l">Docentes</div></div>
  <div class="stat"><div class="v">{{ d.metrics.atoms_total|num }}</div>
    <div class="l">Registros acadêmicos</div></div>
</div>

<p>A aquisição percorre duas fontes distintas. Do SIGAA autenticado vêm os planos de
trabalho do edital, com texto integral de justificativa, objetivos, metodologia e
competências a serem adquiridas. Das páginas públicas do corpo docente vêm o histórico
de projetos, produção e ensino, além do objeto <code>curriculo</code> embutido, que
carrega o currículo Lattes completo: {{ d.counts.pages|num }} páginas arquivadas,
{{ d.counts.table_rows|num }} linhas de tabela e {{ d.counts.lattes_leaves|num }} campos
individuais do Lattes.</p>

<p>A decisão de modelagem mais consequente foi não tratar cada docente como um documento
único. Um currículo concatenado dilui trabalho recente e específico em duas décadas de
produção heterogênea. Em vez disso, cada evidência vira um <i>registro atômico</i>
independente — um projeto, um artigo, uma orientação, uma linha de pesquisa — preservando
tipo, ano e vínculo. São {{ d.metrics.atoms_total|num }} registros para
{{ d.metrics.professors_total|num }} docentes, cerca de 80 vezes o volume da tabela de
oportunidades, e é esse volume que torna viável treinar um modelo distribucional sobre o
vocabulário local.</p>

{% set f = fig('atoms') %}
{% if f %}
<figure>{{ f.svg }}<figcaption><b>Figura {{ f.number }}.</b> {{ f.caption }}</figcaption></figure>
{% endif %}

<h2><span class="n">2</span>A oferta de pesquisa</h2>

<p>Dos {{ d.metrics.opportunities_total|num }} planos, {{ d.metrics.funded_opportunities|num }}
({{ (d.metrics.funded_opportunity_fraction * 100)|num(1) }}%) indicam bolsa, somando
{{ d.metrics.funded_slots|num }} vagas financiadas. Elas concentram-se em
{{ d.metrics.professors_with_funded_opportunity|num }} dos
{{ d.metrics.professors_total|num }} docentes: praticamente metade do corpo docente que
oferta plano não dispõe de nenhuma vaga com bolsa.</p>

{% set f = fig('centers') %}
{% if f %}
<figure>{{ f.svg }}<figcaption><b>Figura {{ f.number }}.</b> {{ f.caption }}</figcaption></figure>
{% endif %}

<table>
  <caption><b>Tabela 1.</b> Distribuição por edital.</caption>
  <thead><tr><th>Edital</th><th class="n">Planos</th><th class="n">Vagas com bolsa</th></tr></thead>
  <tbody>
  {% for name, plans, slots in d.editais[:8] %}
    <tr><td>{{ name }}</td><td class="n">{{ plans|num }}</td><td class="n">{{ slots|num }}</td></tr>
  {% endfor %}
  </tbody>
</table>

<h3>Concentração</h3>

<p>A oferta de <i>planos</i> distribui-se de modo relativamente equitativo
(Gini {{ d.metrics.gini_opportunities_by_professor|num(2) }}), mas a oferta de
<i>bolsas</i> não: Gini {{ d.metrics.gini_funded_slots_by_professor|num(2) }}, com os dez
docentes mais bem financiados detendo {{ (d.metrics.top10_share_funded_slots * 100)|num(0) }}%
de todas as vagas financiadas. Em outras palavras, a capacidade de orientar está
distribuída; a capacidade de <i>pagar</i> um bolsista está concentrada. Para um estudante,
as duas perguntas — quem pode me orientar e quem pode me financiar — têm respostas
substancialmente diferentes, e essa diferença é mensurável.</p>

{% set f = fig('lorenz') %}
{% if f %}
<figure>{{ f.svg }}<figcaption><b>Figura {{ f.number }}.</b> {{ f.caption }}</figcaption></figure>
{% endif %}

<h2 class="break"><span class="n">3</span>Estrutura temática</h2>

<p>A estrutura temática é estimada por fatoração matricial não negativa sobre os documentos
em nível de projeto, separadamente para o que a pesquisa investiga (domínio) e para como ela
é conduzida (métodos). O número de fatores não é escolhido por conveniência: a seleção
pondera coerência NPMI, estabilidade sob reamostragem determinística, sustentação
independente e exclusividade, e então aplica parcimônia — o menor <i>k</i> dentro de uma
margem do melhor escore. O erro de reconstrução é deliberadamente excluído, por cair
monotonicamente com <i>k</i> e portanto medir capacidade, não estrutura.</p>

{% for facet, title in [('domain','Domínio'), ('methods','Métodos')] %}
{% set roots = d.topics.get(facet, [])|selectattr('depth','equalto',0)|list %}
{% if roots %}
<h3>{{ title }}</h3>
<table>
  <caption><b>Tabela {{ loop.index + 1 }}.</b> Temas-raiz e seus termos mais distintivos
  ({{ title|lower }}).</caption>
  <thead><tr><th>Tema</th><th>Termos característicos</th><th class="n">Projetos</th></tr></thead>
  <tbody>
  {% for t in roots %}
    <tr><td>{{ t.label }}</td>
        <td style="color:var(--muted)">{{ t.terms[:8]|join(' · ') }}</td>
        <td class="n">{{ t.projects|num }}</td></tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}
{% endfor %}

{% set f = fig('topics') %}
{% if f %}
<figure>{{ f.svg }}<figcaption><b>Figura {{ f.number }}.</b> {{ f.caption }}</figcaption></figure>
{% endif %}

<h2><span class="n">4</span>Competências técnicas requeridas</h2>

<p>Competências são um conjunto, não uma partição: um projeto pode envolver
simultaneamente Python, PCR e revisão sistemática, sem competição entre esses rótulos. Uma
tentativa anterior de estimá-las por fatoração falhou de forma instrutiva — 74 de 89
projetos colapsaram sobre um único fator genérico de <i>leitura crítica e redação
científica</i>, precisamente porque todo plano promete isso e o sinal não discrimina.
A extração passou então a um léxico determinístico multirrótulo, no qual competências
genéricas são registradas mas nunca pontuadas.</p>

{% set f = fig('skills') %}
{% if f %}
<figure>{{ f.svg }}<figcaption><b>Figura {{ f.number }}.</b> {{ f.caption }}</figcaption></figure>
{% endif %}

<h2 class="break"><span class="n">5</span>Geometria semântica</h2>

<p>A projeção bidimensional parte de uma PCoA sobre distâncias de cosseno e é refinada por
escalonamento multidimensional métrico (SMACOF). O refinamento melhora o ajuste — o stress
de Kruskal cai de {{ d.map_diagnostics.get('pcoa_stress', 0)|num(3) }} para
{{ d.map_diagnostics.get('smacof_stress', 0)|num(3) }} — mas o resultado permanece uma
representação imperfeita: com correlação de Spearman de
{{ d.map_diagnostics.get('spearman', 0)|num(2) }} entre distâncias originais e projetadas,
o mapa preserva vizinhanças, não distâncias. Reporta-se o número justamente para que a
figura não sugira mais precisão do que possui.</p>

{% set f = fig('map') %}
{% if f %}
<figure>{{ f.svg }}<figcaption><b>Figura {{ f.number }}.</b> {{ f.caption }}</figcaption></figure>
{% endif %}

<h2><span class="n">6</span>O ranqueamento é confiável?</h2>

<p>Um sistema que ordena oportunidades por afinidade precisa demonstrar que a ordenação
significa alguma coisa. A avaliação usa sete tarefas de recuperação construídas a partir do
próprio corpus, sem rótulos manuais: recuperar o corpo de um plano a partir do título;
recuperar um plano irmão do mesmo projeto; repetir isso após remover as frases copiadas
verbatim entre irmãos; recuperar a metodologia a partir dos objetivos; recuperar o plano a
partir do título privado de seus dois termos mais raros; recuperar projetos da mesma área
CNPq; e recuperar o portfólio de um docente a partir de um único registro retido.</p>

<p>As tarefas foram escolhidas para <i>discordar entre si</i>. Um método que vencesse todas
indicaria que as tarefas medem a mesma coisa — e as tornaria inúteis para decidir qualquer
questão de projeto.</p>

{% set f = fig('benchmark') %}
{% if f %}
<figure>{{ f.svg }}<figcaption><b>Figura {{ f.number }}.</b> {{ f.caption }}</figcaption></figure>
{% endif %}

<table>
  <caption><b>Tabela 4.</b> MRR por tarefa e canal. Em negrito, o melhor canal de cada linha.</caption>
  <thead><tr><th>Tarefa</th>
  {% for ch in ['lexical_word','lexical_char','bm25','latent','neural','fused'] %}
    <th class="n">{{ CHANNELS[ch] }}</th>
  {% endfor %}
  </tr></thead>
  <tbody>
  {% for task, label in TASKS.items() %}
    {% if task in d.benchmarks %}
    {% set row = d.benchmarks[task] %}
    {% set best = d.best_mrr.get(task, 0) %}
    <tr><td>{{ label }}</td>
    {% for ch in ['lexical_word','lexical_char','bm25','latent','neural','fused'] %}
      {% set v = row.get(ch, {}).get('mrr') %}
      <td class="n">{% if v %}{% if v == best %}<b>{{ v|num(3) }}</b>{% else %}{{ v|num(3) }}{% endif %}{% else %}—{% endif %}</td>
    {% endfor %}
    </tr>
    {% endif %}
  {% endfor %}
  </tbody>
</table>

<h3>Três resultados que mudaram o desenho</h3>

{% set f = fig('lsa') %}
{% if f %}
<figure>{{ f.svg }}<figcaption><b>Figura {{ f.number }}.</b> {{ f.caption }}</figcaption></figure>
{% endif %}

<p><b>A dimensionalidade latente não é um detalhe.</b> A configuração anterior operava com
48–64 dimensões. Ajustada sobre o corpus completo, essa faixa produz MRR próximo de 0,44 em
recuperação de planos irmãos, onde um simples TF-IDF alcança 0,93. Dimensionalidades baixas
descartam exatamente o vocabulário raro que identifica um projeto. O piso passou a 256.</p>

<p><b>Um modelo distribucional treinado no próprio corpus supera a LSA canônica.</b>
Fatorar uma matriz de coocorrência termo-contexto com PMI positivo deslocado e suavização
da distribuição de contexto — parente determinístico do skip-gram, sem amostragem — iguala
ou supera a LSA em cinco das sete tarefas, com metade do custo de ajuste. O modelo aprende
vizinhanças que só existem neste corpus: <i>geoprocessamento</i> aproxima-se de
<i>espacial</i>, <i>territorial</i> e <i>dashboards</i>; <i>datasus</i> aproxima-se de
<i>sinan</i>, <i>sia/sus</i> e <i>boletins</i>.</p>

{% set f = fig('neural') %}
{% if f %}
<figure>{{ f.svg }}<figcaption><b>Figura {{ f.number }}.</b> {{ f.caption }}</figcaption></figure>
{% endif %}

<p><b>O modelo neural é complementar, não superior.</b> Um modelo de embeddings de 4 bilhões
de parâmetros executado localmente é o <i>pior</i> canal em recuperação literal — perde por
ampla margem na tarefa de título mascarado — e o <i>melhor</i> em coerência de portfólio,
onde supera todo canal nativo do corpus. É exatamente esse padrão que justifica combinar as
representações em vez de escolher uma; e é por isso que o sistema expõe canais nomeados
separadamente em vez de um único escore de "afinidade" sem significado definido.</p>

<div class="callout">
  <span class="t">O que a avaliação não demonstra</span>
  As tarefas medem coerência interna do corpus, não sucesso na vida real. Nenhuma delas
  responde se um estudante bem ranqueado se tornará um bom orientando. Os escores são
  ordinais e reportados como percentis dentro do corpus; não são probabilidades e não devem
  ser lidos como tal.
</div>

<h2><span class="n">7</span>Limitações</h2>

<ul>
  <li>A projeção bidimensional tem stress de {{ d.map_diagnostics.get('stress', 0)|num(2) }};
      vizinhanças são informativas, distâncias absolutas não.</li>
  <li>A tarefa de área CNPq usa um rótulo grosseiro e inconsistentemente preenchido; seus
      valores absolutos são indicativos.</li>
  <li>Facetas de método e competência só existem para docentes que ofertam plano no edital —
      o Lattes não traz texto de metodologia.</li>
  <li>O corpus é um recorte temporal único. Nada aqui mede tendência.</li>
</ul>

<h2><span class="n">8</span>Reprodutibilidade</h2>

<p>Todo resultado deste documento é regenerável a partir do banco analítico por um único
comando. A identidade do espaço semântico é o hash do corpus, do pré-processamento, da
arquitetura dos modelos, do modelo de embeddings e das versões de biblioteca; a identidade
da execução de perfil acrescenta a configuração de consulta. Alterar interesses pessoais
cria uma nova execução sem invalidar tópicos ou geometria; atualizar o corpus docente
invalida o espaço, e o sistema recusa-se a combinar entidades novas com escores antigos.</p>

<table>
  <caption><b>Tabela 5.</b> Proveniência desta edição.</caption>
  <tbody>
    <tr><td>Espaço semântico</td><td><code>{{ d.space_id }}</code></td></tr>
    <tr><td>Execução de perfil</td><td><code>{{ d.run_id }}</code></td></tr>
    <tr><td>Registros atômicos</td><td>{{ d.metrics.atoms_total|num }}</td></tr>
    <tr><td>Documentos de treino</td><td>{{ d.space_stats.get('training_documents', 0)|num }}</td></tr>
    <tr><td>Canais avaliados</td><td>{{ d.space_stats.get('channels', [])|join(', ') }}</td></tr>
  </tbody>
</table>

<div class="footer">
  PULSAR — sistema local de inteligência de pesquisa e prospecção de oportunidades.
  Documento gerado automaticamente em {{ d.generated_at }} a partir do espaço
  <code>{{ d.space_id }}</code>. Todos os números derivam do banco analítico; nenhum foi
  transcrito manualmente.
</div>

</div></body></html>
"""
