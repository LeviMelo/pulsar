"""The PULSAR semantic regression battery.

The engine is developed as a measurable information-retrieval system, not by
eyeballing topic labels. Every candidate representation runs the same
deterministic, self-supervised tasks built from PULSAR's own corpus, and a more
complicated method only survives if it earns its complexity.

The tasks are chosen so that *different tasks disagree*. Literal recall
(`title_to_body`) and thematic affinity (`sibling_masked`) are different
retrieval problems, and a model that wins one while losing the other is telling
us something real rather than being "worse".
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import numpy as np
from scipy import sparse

from .corpus import SemanticCorpus, opportunity_views
from .normalize import PORTUGUESE_STOP_WORDS, clean_text, fold, join_unique, scrub_citations
from .representations import cosine
from .retrieval import BM25FIndex

# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


class RepresentationCandidate:
    """Adapts any Representation to the benchmark's score-matrix interface."""

    def __init__(self, representation, name: str | None = None):
        self.representation = representation
        self.name = name or representation.name
        self.has_geometry = True

    def score_matrix(self, queries: Sequence[str], documents: Sequence[str]) -> np.ndarray:
        # A fusion can score without materializing its concatenation; taking that
        # route keeps a sparse lexical view from being densified corpus-wide.
        similarity = getattr(self.representation, "similarity", None)
        if callable(similarity):
            return similarity(list(queries), list(documents))
        q = self.representation.encode(list(queries))
        d = self.representation.encode(list(documents))
        return cosine(q, d)

    def doc_vectors(self, documents: Sequence[str]):
        return self.representation.encode(list(documents))


class BM25Candidate:
    """BM25 as a pure retrieval operator: relevance without a geometry."""

    name = "bm25"
    has_geometry = False

    def __init__(self, name: str = "bm25", k1: float = 1.5, b: float = 0.75):
        self.name = name
        self.k1 = k1
        self.b = b

    def score_matrix(self, queries: Sequence[str], documents: Sequence[str]) -> np.ndarray:
        index = BM25FIndex({"text": 1.0}, k1=self.k1, b=self.b).fit(
            [{"text": d} for d in documents]
        )
        return np.vstack([index.score(q) for q in queries])

    def doc_vectors(self, documents: Sequence[str]):
        return None


# ---------------------------------------------------------------------------
# Ranking metrics
# ---------------------------------------------------------------------------


def _ranks_of(scores: np.ndarray, relevant: Sequence[int], *, exclude: Sequence[int] = ()) -> list[int]:
    """1-based ranks of relevant documents after removing excluded positions."""
    masked = np.array(scores, dtype=float)
    if len(exclude):
        masked[list(exclude)] = -np.inf
    order = np.argsort(-masked, kind="mergesort")
    position = np.empty(len(masked), dtype=int)
    position[order] = np.arange(1, len(masked) + 1)
    return [int(position[i]) for i in relevant]


def _ndcg(scores: np.ndarray, relevant: set[int], *, exclude: Sequence[int] = (), k: int = 10) -> float:
    masked = np.array(scores, dtype=float)
    if len(exclude):
        masked[list(exclude)] = -np.inf
    order = np.argsort(-masked, kind="mergesort")[:k]
    dcg = sum(1.0 / math.log2(rank + 1) for rank, idx in enumerate(order, start=1) if int(idx) in relevant)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
    return float(dcg / ideal) if ideal > 0 else 0.0


def _aggregate(ranks: Sequence[int], ndcgs: Sequence[float] | None = None) -> dict[str, float]:
    arr = np.asarray(ranks, dtype=float)
    if len(arr) == 0:
        return {"mrr": 0.0, "recall_at_1": 0.0, "recall_at_5": 0.0, "recall_at_10": 0.0, "cases": 0.0}
    out = {
        "mrr": float(np.mean(1.0 / arr)),
        "recall_at_1": float(np.mean(arr <= 1)),
        "recall_at_5": float(np.mean(arr <= 5)),
        "recall_at_10": float(np.mean(arr <= 10)),
        "cases": float(len(arr)),
    }
    if ndcgs:
        out["ndcg_at_10"] = float(np.mean(ndcgs))
    return out


# ---------------------------------------------------------------------------
# Task construction
# ---------------------------------------------------------------------------


_SENTENCE = re.compile(r"(?<=[.;:!?])\s+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text or "") if len(s.strip()) > 25]


def _suppress_shared_sentences(bodies: Sequence[str], groups: Sequence[str]) -> list[str]:
    """Delete sentences that a document copies verbatim from a sibling.

    Sibling work plans under one SIGAA project share whole paragraphs. Retrieving
    a sibling by that copied text measures string matching, not thematic
    affinity, so the honest version of the sibling task removes it first.
    """
    by_group: dict[str, list[int]] = defaultdict(list)
    for i, g in enumerate(groups):
        by_group[g].append(i)
    sent_sets = [{fold(s) for s in _sentences(b)} for b in bodies]
    out: list[str] = []
    for i, body in enumerate(bodies):
        shared: set[str] = set()
        for j in by_group.get(groups[i], []):
            if j != i:
                shared |= sent_sets[i] & sent_sets[j]
        kept = [s for s in _sentences(body) if fold(s) not in shared]
        out.append(" ".join(kept) if kept else clean_text(body)[:200])
    return out


def _mask_distinctive_terms(titles: Sequence[str], corpus: Sequence[str], *, n_mask: int = 2) -> list[str]:
    """Remove each title's rarest tokens, forcing generalization beyond one keyword.

    A model that only ever matched ``mellonella`` to the one document containing
    ``mellonella`` has memorized an identifier, not learned a topic.
    """
    df: Counter[str] = Counter()
    for doc in corpus:
        df.update(set(t for t in re.findall(r"[a-z0-9][a-z0-9-]{2,}", fold(doc))))
    out: list[str] = []
    for title in titles:
        toks = [t for t in re.findall(r"[a-z0-9][a-z0-9-]{2,}", fold(title)) if t not in PORTUGUESE_STOP_WORDS]
        if not toks:
            out.append(clean_text(title))
            continue
        rarest = sorted(set(toks), key=lambda t: (df.get(t, 0), t))[:n_mask]
        kept = [t for t in toks if t not in rarest]
        out.append(" ".join(kept) if kept else " ".join(toks))
    return out


@dataclass(slots=True)
class RetrievalTask:
    """A query set, a document set and the relevance judgements linking them."""

    name: str
    queries: list[str]
    documents: list[str]
    relevant: list[list[int]]
    exclude: list[list[int]] = field(default_factory=list)
    note: str = ""
    graded: bool = False

    def evaluate(self, candidate) -> dict[str, float]:
        if not self.queries or not self.documents:
            return _aggregate([])
        scores = candidate.score_matrix(self.queries, self.documents)
        ranks: list[int] = []
        ndcgs: list[float] = []
        for i, rel in enumerate(self.relevant):
            if not rel:
                continue
            excl = self.exclude[i] if self.exclude else []
            positions = _ranks_of(scores[i], rel, exclude=excl)
            ranks.append(min(positions))
            if self.graded:
                ndcgs.append(_ndcg(scores[i], set(rel), exclude=excl))
        return _aggregate(ranks, ndcgs if self.graded else None)


def build_retrieval_tasks(corpus: SemanticCorpus, *, min_professor_atoms: int = 6) -> list[RetrievalTask]:
    """Construct the deterministic task battery from the live corpus."""
    rows = corpus.opportunities
    tasks: list[RetrievalTask] = []
    if not rows:
        return tasks

    views = [opportunity_views(r, topic_clean=True) for r in rows]
    bodies = [
        join_unique([v["domain_intro"], v["domain_objectives"], v["method_methodology"], v["skill_skills"]])
        or "vazio"
        for v in views
    ]
    titles = [clean_text(r.get("plan_title") or r.get("project_title")) for r in rows]
    projects = [str(r.get("project_code") or "") for r in rows]

    own = [[i] for i in range(len(rows))]
    tasks.append(RetrievalTask(
        "title_to_body", titles, bodies, own,
        note="Can the engine find a work plan from its own title? Literal recall floor.",
    ))

    siblings = [
        [j for j, p in enumerate(projects) if p and p == projects[i] and j != i]
        for i in range(len(rows))
    ]
    tasks.append(RetrievalTask(
        "sibling_plan", titles, bodies, siblings, exclude=own,
        note="Find a different work plan of the same project. Thematic affinity, but inflated by copied prose.",
    ))

    clean_bodies = _suppress_shared_sentences(bodies, projects)
    tasks.append(RetrievalTask(
        "sibling_deduplicated", titles, clean_bodies, siblings, exclude=own,
        note="Sibling retrieval after verbatim shared sentences are deleted. The honest affinity test.",
    ))

    objectives = [v["domain_objectives"] for v in views]
    methodologies = [v["method_methodology"] for v in views]
    keep = [i for i in range(len(rows)) if len(objectives[i]) > 60 and len(methodologies[i]) > 60]
    if keep:
        tasks.append(RetrievalTask(
            "objectives_to_methodology",
            [objectives[i] for i in keep], [methodologies[i] for i in keep],
            [[n] for n in range(len(keep))],
            note="Match what a study wants to a description of how it will be done. Cross-view coherence.",
        ))

    masked = _mask_distinctive_terms(titles, bodies)
    tasks.append(RetrievalTask(
        "masked_title", masked, bodies, own,
        note="Title with its two rarest tokens removed. Penalizes pure identifier memorization.",
    ))

    # Cross-project same-area retrieval (graded, project level).
    pdocs = [p["document"] for p in corpus.projects]
    pareas = [fold(p.get("area") or p.get("large_area") or "") for p in corpus.projects]
    area_groups: dict[str, list[int]] = defaultdict(list)
    for i, a in enumerate(pareas):
        if a:
            area_groups[a].append(i)
    rel_area = [
        [j for j in area_groups.get(pareas[i], []) if j != i] if pareas[i] else []
        for i in range(len(pdocs))
    ]
    if any(rel_area):
        tasks.append(RetrievalTask(
            "cross_project_area", pdocs, pdocs, rel_area, exclude=[[i] for i in range(len(pdocs))],
            note="Retrieve other projects sharing the CNPq area. Weak external label, graded by NDCG@10.",
            graded=True,
        ))

    # Professor holdout: does one atom of a portfolio retrieve its siblings?
    by_prof = corpus.atoms_by_siape()
    pool: list[str] = []
    owner: list[str] = []
    for siape in sorted(by_prof):
        atoms = [a for a in by_prof[siape] if len(a.text) > 40]
        if len(atoms) < min_professor_atoms:
            continue
        for atom in atoms:
            pool.append(atom.text)
            owner.append(siape)
    if pool:
        by_owner: dict[str, list[int]] = defaultdict(list)
        for i, s in enumerate(owner):
            by_owner[s].append(i)
        # One deterministic holdout per professor keeps the task cheap and stable.
        q_idx = [sorted(v)[len(v) // 2] for v in by_owner.values()]
        tasks.append(RetrievalTask(
            "professor_holdout",
            [pool[i] for i in q_idx], pool,
            [[j for j in by_owner[owner[i]] if j != i] for i in q_idx],
            exclude=[[i] for i in q_idx],
            note="A held-out portfolio item should retrieve its owner's other work. Portfolio coherence.",
            graded=True,
        ))
    return tasks


# ---------------------------------------------------------------------------
# Geometry diagnostics
# ---------------------------------------------------------------------------


def map_fidelity(high_dim, coords: np.ndarray, *, sample: int = 400, seed: int = 0) -> dict[str, float]:
    """How much of the real geometry survives the 2-D projection?

    A pretty scatter plot implies more than it contains unless stress and rank
    correlation are reported next to it.
    """
    from scipy.stats import pearsonr, spearmanr

    # The high-dimensional metric MUST be the one the map was optimized against,
    # or this reports the distortion of a projection nobody built. An earlier
    # version used the chord distance sqrt(2-2cos) here while `build_map` used
    # 1-cos, and then overwrote build_map's honest stress with a number from a
    # different geometry.
    from .landscape import cosine_distance_matrix

    x = high_dim
    if sparse.issparse(x):
        x = np.asarray(x.todense())
    x = np.asarray(x, dtype=float)
    n = x.shape[0]
    if n < 4:
        return {"stress": 0.0, "pearson": 0.0, "spearman": 0.0, "points": float(n)}
    idx = np.arange(n)
    if n > sample:
        idx = np.sort(np.random.default_rng(seed).choice(n, size=sample, replace=False))
    cs = np.asarray(coords, dtype=float)[idx]
    hi = cosine_distance_matrix(x[idx])
    lo = np.linalg.norm(cs[:, None, :] - cs[None, :, :], axis=-1)
    iu = np.triu_indices(len(idx), k=1)
    a, b = hi[iu], lo[iu]
    scale = float(np.dot(a, b) / np.dot(b, b)) if np.dot(b, b) > 0 else 1.0
    b_scaled = b * scale
    denom = float(np.sum(a ** 2)) or 1.0
    return {
        "stress": float(math.sqrt(np.sum((a - b_scaled) ** 2) / denom)),
        "pearson": float(pearsonr(a, b)[0]) if np.std(b) > 0 else 0.0,
        "spearman": float(spearmanr(a, b)[0]) if np.std(b) > 0 else 0.0,
        "points": float(len(idx)),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BenchmarkReport:
    results: dict[str, dict[str, dict[str, float]]]  # task -> candidate -> metrics
    notes: dict[str, str]

    def to_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for task, per_candidate in self.results.items():
            for candidate, metrics in per_candidate.items():
                for metric, value in metrics.items():
                    rows.append({"benchmark": task, "channel": candidate, "metric": metric, "value": float(value)})
        return rows

    def table(self, metric: str = "mrr") -> str:
        candidates = sorted({c for per in self.results.values() for c in per})
        width = max([len(t) for t in self.results] + [22])
        head = f"{'task'.ljust(width)}  " + "  ".join(c.rjust(12) for c in candidates)
        lines = [head, "-" * len(head)]
        for task in self.results:
            cells = []
            for c in candidates:
                value = self.results[task].get(c, {}).get(metric)
                cells.append(("—" if value is None else f"{value:.4f}").rjust(12))
            lines.append(f"{task.ljust(width)}  " + "  ".join(cells))
        return "\n".join(lines)

    def best_per_task(self, metric: str = "mrr") -> dict[str, str]:
        out: dict[str, str] = {}
        for task, per in self.results.items():
            scored = [(m.get(metric, 0.0), c) for c, m in per.items() if m.get("cases", 1) > 0]
            if scored:
                out[task] = max(scored)[1]
        return out


def run_benchmarks(
    tasks: Sequence[RetrievalTask],
    candidates: Mapping[str, Any],
    *,
    progress: Callable[[str, str], None] | None = None,
) -> BenchmarkReport:
    results: dict[str, dict[str, dict[str, float]]] = {}
    for task in tasks:
        results[task.name] = {}
        for name, candidate in candidates.items():
            if progress:
                progress(task.name, name)
            results[task.name][name] = task.evaluate(candidate)
    return BenchmarkReport(results=results, notes={t.name: t.note for t in tasks})
