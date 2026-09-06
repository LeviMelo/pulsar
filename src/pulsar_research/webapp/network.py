"""Professor graphs, built three ways from data the store already holds.

A ranked list answers "who fits", which is the easy half of planning a campaign.
The harder question is structural: is this supervisor isolated or does she sit in
a cluster three of whose members have already been written to; does the field I
keep matching contain one group or two that never co-author; if this thread goes
quiet, who is adjacent to it. None of that is visible in a table.

Three edge semantics, because they disagree in useful ways:

``collaboration``  Lattes co-participation between two indexed UFAL supervisors.
                   Sparse, factual, and the only one that reflects people who
                   have actually worked together.
``skills``         Jaccard overlap of the distinctive techniques each portfolio
                   uses. Two people who never met can be methodological
                   neighbours, which is exactly who a methods-led applicant
                   should be looking for.
``topics``         Cosine similarity over topic-share vectors — subject
                   adjacency rather than shared technique.

The similarity graphs are pruned, not thresholded alone: a plain cutoff either
leaves a hairball or strands half the corpus, so each node keeps its strongest
few edges and the union is returned. That guarantees every node has a
neighbourhood to explore while keeping the drawing readable.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable

from ..db import Database

MODES = ("collaboration", "skills", "topics")

#: Similarity below this is noise: two portfolios sharing one common technique.
MIN_SIMILARITY = 0.18
#: Each node keeps at most this many of its strongest similarity edges. The
#: union of those neighbourhoods is the drawn graph.
TOP_EDGES_PER_NODE = 6


def _pairs(vectors: dict[str, dict[str, float]], similarity,
           labels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Every above-threshold pair, then pruned to each node's strongest few.

    Each surviving edge carries the terms it was computed from. A graph that
    draws a line between two people and cannot say why is asking to be trusted
    on the strength of a picture, which is the one thing the rest of this tool
    refuses to do with a ranking.
    """
    keys = list(vectors)
    scored: list[tuple[float, str, str]] = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            value = similarity(vectors[a], vectors[b])
            if value >= MIN_SIMILARITY:
                scored.append((value, a, b))

    kept: set[tuple[str, str]] = set()
    by_node: dict[str, list[tuple[float, str, str]]] = defaultdict(list)
    for edge in scored:
        by_node[edge[1]].append(edge)
        by_node[edge[2]].append(edge)
    for node, edges in by_node.items():
        edges.sort(key=lambda e: -e[0])
        for value, a, b in edges[:TOP_EDGES_PER_NODE]:
            kept.add((a, b))

    edges = []
    for value, a, b in scored:
        if (a, b) not in kept:
            continue
        shared = sorted(set(vectors[a]) & set(vectors[b]),
                        key=lambda k: -min(vectors[a][k], vectors[b][k]))
        edges.append({
            "source": a, "target": b, "weight": round(value, 4),
            "why": [labels.get(k, k) for k in shared[:4]] if labels else [],
            "shared": len(shared),
        })
    return edges


def _jaccard(a: dict[str, float], b: dict[str, float]) -> float:
    left, right = set(a), set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    shared = set(a) & set(b)
    if not shared:
        return 0.0
    dot = sum(a[k] * b[k] for k in shared)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def _collaboration_edges(db: Database, known: set[str]) -> list[dict[str, Any]]:
    """Only edges whose *both* endpoints are indexed supervisors.

    The store holds 4,545 collaboration rows, but most name an external
    co-author with no SIAPE. Those belong to an ego view of one professor, not
    to a graph of this faculty, and including them would bury the 220 internal
    edges under thousands of degree-one leaves.
    """
    frame = db.query_df(
        "SELECT source_siape, target_siape, SUM(weight) AS weight "
        "FROM collaboration_edges WHERE COALESCE(target_siape,'')<>'' "
        "GROUP BY source_siape, target_siape")
    seen: dict[tuple[str, str], float] = {}
    for row in frame.itertuples():
        a, b = str(row.source_siape), str(row.target_siape)
        if a == b or a not in known or b not in known:
            continue
        key = (a, b) if a < b else (b, a)
        seen[key] = seen.get(key, 0.0) + float(row.weight or 1)
    return [{"source": a, "target": b, "weight": weight,
             "why": [], "shared": int(weight)}
            for (a, b), weight in seen.items()]


def _external_degree(db: Database) -> dict[str, int]:
    """How many co-authors each professor has outside the indexed faculty."""
    frame = db.query_df(
        "SELECT source_siape, COUNT(DISTINCT collaborator_name) AS n "
        "FROM collaboration_edges WHERE COALESCE(target_siape,'')='' GROUP BY source_siape")
    return {str(r.source_siape): int(r.n) for r in frame.itertuples()}


def build(db: Database, mode: str, *, display_name=None) -> dict[str, Any]:
    """Nodes and edges for one graph, plus what the legend needs to explain it."""
    mode = mode if mode in MODES else "collaboration"
    from ..dashboard.queries import Repository
    from ..outreach import outcomes as oc

    repo = Repository(db)
    professors = repo.professors()
    if not len(professors):
        return {"mode": mode, "nodes": [], "edges": [], "note": "No professors indexed."}

    known = {str(s) for s in professors["siape"]}
    contacted: dict[str, dict[str, Any]] = {}
    recorded = oc.outcomes(db)
    sent = db.query_df(
        "SELECT r.siape, r.campaign_id, m.status FROM campaign_recipients r "
        "JOIN campaign_messages m ON m.campaign_id=r.campaign_id AND m.siape=r.siape")
    for row in sent.itertuples() if len(sent) else ():
        siape = str(row.siape)
        state = recorded.get((str(row.campaign_id), siape), {}).get("state")
        if str(row.status) == "sent":
            contacted[siape] = {"campaign_id": str(row.campaign_id),
                                "state": state or oc.DEFAULT_STATE}

    external = _external_degree(db)
    # A couple of words of subject matter per node. The graph can encode
    # position, size and colour but never *what the person works on*, and that
    # is exactly what decides whether a neighbour is worth opening next.
    keywords: dict[str, list[str]] = defaultdict(list)
    key_rows = db.query_df(
        "SELECT entity_id, label FROM entity_skills "
        "WHERE space_id=? AND entity_type='professor' AND NOT generic "
        "ORDER BY entity_id, mentions DESC", [repo.space_id])
    for row in key_rows.itertuples() if len(key_rows) else ():
        bucket = keywords[str(row.entity_id)]
        if len(bucket) < 3:
            bucket.append(str(row.label))

    nodes = []
    for row in professors.itertuples():
        siape = str(row.siape)
        thread = contacted.get(siape)
        nodes.append({
            "id": siape,
            "name": (display_name(siape, row.canonical_name) if display_name
                     else str(row.canonical_name)),
            "center": _text(getattr(row, "center", "")),
            "department": " ".join(_text(getattr(row, "department", "")).split()),
            "cluster": _int(getattr(row, "cluster_id", None)),
            "x": _float(getattr(row, "x", None)),
            "y": _float(getattr(row, "y", None)),
            "funded_slots": _int(getattr(row, "funded_slots", 0)) or 0,
            "opportunities": _int(getattr(row, "opportunities", 0)) or 0,
            "publications": _int(getattr(row, "publications", 0)) or 0,
            "orientations": _int(getattr(row, "orientations", 0)) or 0,
            "current_projects": _int(getattr(row, "current_projects", 0)) or 0,
            "latest_year": _int(getattr(row, "latest_evidence_year", None)),
            "external_collaborators": external.get(siape, 0),
            "keywords": keywords.get(siape, []),
            "current_pct": _float(getattr(row, "current_fused_pct", None)),
            "trajectory_pct": _float(getattr(row, "trajectory_fused_pct", None)),
            "methods_pct": _float(getattr(row, "methods_fused_pct", None)),
            "skills_pct": _float(getattr(row, "skills_fused_pct", None)),
            "contacted": bool(thread),
            "state": (thread or {}).get("state"),
            "campaign_id": (thread or {}).get("campaign_id"),
        })

    if mode == "collaboration":
        edges = _collaboration_edges(db, known)
        because = lambda e: f"{e['shared']} shared record{'' if e['shared'] == 1 else 's'}"
        note = ("Lattes co-participation between two indexed supervisors. Sparse and factual: "
                "an edge means these two have actually appeared on the same project or paper. "
                "Co-authors outside this faculty are counted on the node, not drawn.")
    elif mode == "skills":
        skills = db.query_df(
            "SELECT entity_id, skill_id, label FROM entity_skills "
            "WHERE space_id=? AND entity_type='professor' AND NOT generic", [repo.space_id])
        vectors: dict[str, dict[str, float]] = defaultdict(dict)
        names: dict[str, str] = {}
        for row in skills.itertuples() if len(skills) else ():
            vectors[str(row.entity_id)][str(row.skill_id)] = 1.0
            names[str(row.skill_id)] = str(row.label)
        edges = _pairs(dict(vectors), _jaccard, names)
        because = lambda e: " · ".join(e["why"]) or "shared techniques"
        note = ("Jaccard overlap of the distinctive techniques in each portfolio, with generic "
                "scholarly competencies excluded. Two supervisors who have never met can be "
                "close here — which is the point: this is the methodological neighbourhood, "
                "not the social one.")
    else:
        # `entity_topics` holds no professor rows: topics are fitted over plan and
        # project documents, not over people. A supervisor's subject vector is the
        # sum of the topic shares of the plans they are offering, which is also
        # why someone with no open plan has no position in this graph at all.
        topics = db.query_df(
            "SELECT o.professor_siape AS siape, et.topic_id, SUM(et.weight) AS weight "
            "FROM entity_topics et "
            "JOIN opportunities o ON o.id_opportunity = et.entity_id "
            "WHERE et.space_id=? AND et.entity_type='opportunity' AND et.facet='domain' "
            "  AND COALESCE(o.professor_siape,'')<>'' "
            "GROUP BY o.professor_siape, et.topic_id", [repo.space_id])
        vectors = defaultdict(dict)
        for row in topics.itertuples() if len(topics) else ():
            vectors[str(row.siape)][str(row.topic_id)] = float(row.weight or 0)
        named = db.query_df(
            "SELECT topic_id, label FROM semantic_topics WHERE space_id=? AND facet='domain'",
            [repo.space_id])
        names = {str(r.topic_id): str(r.label) for r in named.itertuples()} if len(named) else {}
        edges = _pairs(dict(vectors), _cosine, names)
        because = lambda e: " · ".join(e["why"]) or "shared subjects"
        note = ("Cosine similarity over topic-share vectors: subject adjacency rather than "
                "shared technique. Professor topic shares are derived from their work plans, "
                "so a supervisor with no open plan has no position here.")

    for edge in edges:
        edge["because"] = because(edge)

    degree: dict[str, int] = defaultdict(int)
    for edge in edges:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1
    for node in nodes:
        node["degree"] = degree.get(node["id"], 0)

    return {
        "mode": mode,
        "modes": [{"key": m, "label": _LABELS[m]} for m in MODES],
        "nodes": nodes,
        "edges": edges,
        "note": note,
        "isolated": sum(1 for n in nodes if not n["degree"]),
    }


_LABELS = {
    "collaboration": "Co-authorship",
    "skills": "Shared techniques",
    "topics": "Shared subjects",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _int(value: Any) -> int | None:
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    except (TypeError, ValueError):
        return None


def neighbourhood(edges: Iterable[dict[str, Any]], root: str, hops: int = 1) -> set[str]:
    """Node ids within `hops` of `root`, for focus mode on the client."""
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        adjacency[edge["source"]].add(edge["target"])
        adjacency[edge["target"]].add(edge["source"])
    seen = {root}
    frontier = {root}
    for _ in range(max(0, hops)):
        frontier = {n for node in frontier for n in adjacency[node]} - seen
        seen |= frontier
    return seen
