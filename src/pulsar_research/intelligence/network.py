"""Structural measures over the entity graph.

Every screen in PULSAR until now ranked people by *fit* — how close their work
is to a described interest. Fit is an attribute question, and attribute
questions are the ones a table can already answer. The questions a table cannot
answer are structural, and they are usually the ones that decide what to do:

* Who sits between two groups that otherwise do not talk? Not the most central
  person, and often not a highly ranked one.
* Which clusters does this faculty actually decompose into, as opposed to the
  units it is administratively divided into?
* Whose collaboration reaches outside the institution, and whose does not?

None of these are computable from a row. They are computable from the edge
store, and because that store is typed, each of them can be asked of *one*
relation at a time — co-authorship says something different from shared
technique, and averaging them would answer neither question.

Everything here is deterministic. A centrality that changes between two runs on
unchanged data is not a measurement, and an operator who sees a rank move has to
be able to conclude that the data moved.
"""

from __future__ import annotations

import heapq
from collections import deque
from typing import Any, Iterable, Mapping, Sequence

Adjacency = Mapping[str, Mapping[str, float]]


def degree(adjacency: Adjacency) -> dict[str, float]:
    """How many distinct things this entity is joined to."""
    return {node: float(len(neighbours)) for node, neighbours in adjacency.items()}


def weighted_degree(adjacency: Adjacency) -> dict[str, float]:
    """Total tie strength. Ten papers with one person is not ten collaborators."""
    return {node: float(sum(neighbours.values())) for node, neighbours in adjacency.items()}


# ---------------------------------------------------------------------------
# Betweenness
# ---------------------------------------------------------------------------


def betweenness(adjacency: Adjacency, *, pivots: int = 256) -> dict[str, float]:
    """Brandes' betweenness, over a deterministic sample of source vertices.

    Exact betweenness is O(VE) — on a co-authorship graph of a few thousand
    people that is minutes, and this runs inside a build that already takes
    long enough. Sampling sources is the standard approximation (Brandes &
    Pich): the estimate is unbiased and the ranking is stable well before the
    values converge, which is what this is used for.

    The pivots are the highest-degree vertices with ties broken by id, not a
    random draw, so two runs on the same graph give the same numbers. On a graph
    smaller than the pivot budget this is exact.
    """
    nodes = sorted(adjacency)
    if len(nodes) < 3:
        return {node: 0.0 for node in nodes}

    sources = nodes
    scale = 1.0
    if len(nodes) > pivots:
        ranked = sorted(nodes, key=lambda n: (-len(adjacency[n]), n))
        sources = sorted(ranked[:pivots])
        scale = len(nodes) / float(len(sources))

    score = {node: 0.0 for node in nodes}
    for start in sources:
        stack: list[str] = []
        predecessors: dict[str, list[str]] = {node: [] for node in nodes}
        paths = {node: 0.0 for node in nodes}
        paths[start] = 1.0
        distance = {node: -1 for node in nodes}
        distance[start] = 0
        queue = deque([start])
        while queue:
            node = queue.popleft()
            stack.append(node)
            for neighbour in sorted(adjacency[node]):
                if distance[neighbour] < 0:
                    distance[neighbour] = distance[node] + 1
                    queue.append(neighbour)
                if distance[neighbour] == distance[node] + 1:
                    paths[neighbour] += paths[node]
                    predecessors[neighbour].append(node)
        delta = {node: 0.0 for node in nodes}
        while stack:
            node = stack.pop()
            for predecessor in predecessors[node]:
                if paths[node]:
                    delta[predecessor] += (paths[predecessor] / paths[node]) * (1.0 + delta[node])
            if node != start:
                score[node] += delta[node]

    # Undirected, so every pair is counted from both ends; then normalized by
    # the number of pairs a vertex could possibly sit between, which makes the
    # value a share in [0, 1] and comparable between graphs of different sizes.
    # Unnormalized Brandes counts run to seven figures on a graph this size and
    # read as noise.
    total = len(nodes)
    pairs = (total - 1) * (total - 2) / 2.0
    factor = (scale / 2.0) / pairs if pairs > 0 else 0.0
    return {node: value * factor for node, value in score.items()}


# ---------------------------------------------------------------------------
# Communities
# ---------------------------------------------------------------------------


def communities(adjacency: Adjacency, *, rounds: int = 40) -> dict[str, str]:
    """Weighted label propagation.

    Chosen over modularity maximisation because it makes no claim to have found
    an optimum: it reports which vertices keep agreeing with their neighbours,
    which is all the interpretation the drawing can support anyway. Modularity
    optima on a graph this sparse are unstable across resolutions, and quoting
    one as *the* community structure would be a stronger claim than the data
    licenses.

    Determinism comes from processing vertices in a fixed order and breaking
    label ties by the smallest label rather than at random.
    """
    labels = {node: node for node in sorted(adjacency)}
    order = sorted(adjacency, key=lambda n: (-len(adjacency[n]), n))
    for _ in range(rounds):
        changed = False
        for node in order:
            neighbours = adjacency[node]
            if not neighbours:
                continue
            weights: dict[str, float] = {}
            for neighbour, weight in neighbours.items():
                label = labels.get(neighbour)
                if label is not None:
                    weights[label] = weights.get(label, 0.0) + float(weight)
            if not weights:
                continue
            best = min(sorted(weights), key=lambda label: (-weights[label], label))
            if best != labels[node]:
                labels[node] = best
                changed = True
        if not changed:
            break

    # Rename to compact, stable ids ordered by size, so "community 0" is the
    # largest one on every run rather than whichever vertex happened to win.
    sizes: dict[str, int] = {}
    for label in labels.values():
        sizes[label] = sizes.get(label, 0) + 1
    ranked = sorted(sizes, key=lambda label: (-sizes[label], label))
    renamed = {label: f"c{index}" for index, label in enumerate(ranked)}
    return {node: renamed[label] for node, label in labels.items()}


def bridging(adjacency: Adjacency, labels: Mapping[str, str]) -> dict[str, float]:
    """The share of a vertex's tie strength that leaves its own community.

    A person whose every collaborator is inside their own cluster is well
    embedded and structurally redundant: anything reachable through them is
    reachable anyway. A person with half their weight outside is a door. This is
    the measure that answers "who could introduce me to a group I have no way
    into", which is the question the whole platform is for.
    """
    out: dict[str, float] = {}
    for node, neighbours in adjacency.items():
        mine = labels.get(node)
        total = sum(neighbours.values())
        if not total:
            out[node] = 0.0
            continue
        outside = sum(w for other, w in neighbours.items() if labels.get(other) != mine)
        out[node] = outside / total
    return out


def reach(adjacency: Adjacency, inside: Iterable[str]) -> dict[str, float]:
    """How much of a vertex's tie strength goes to vertices outside a given set.

    Used with the indexed faculty as the inside set, this separates a professor
    who is central *here* from one whose collaboration mostly happens elsewhere
    — a distinction the faculty-only graph cannot make and which changes what an
    approach to them can plausibly ask for.
    """
    inside_set = set(inside)
    out: dict[str, float] = {}
    for node, neighbours in adjacency.items():
        total = sum(neighbours.values())
        if not total:
            out[node] = 0.0
            continue
        out[node] = sum(w for other, w in neighbours.items() if other not in inside_set) / total
    return out


def components(adjacency: Adjacency) -> list[set[str]]:
    """Connected components, largest first. A sanity check more than a measure."""
    seen: set[str] = set()
    found: list[set[str]] = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        group = {start}
        queue = deque([start])
        seen.add(start)
        while queue:
            node = queue.popleft()
            for neighbour in adjacency[node]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    group.add(neighbour)
                    queue.append(neighbour)
        found.append(group)
    return sorted(found, key=len, reverse=True)


def shortest_path(adjacency: Adjacency, source: str, target: str,
                  *, limit: int = 6) -> list[str]:
    """The strongest short path between two entities, or an empty list.

    "How do I get to this person" is the one graph question every user of a
    professional network asks, and it is answered by a path, not by a score.
    Ties are broken toward heavier edges, so the path returned is the one most
    likely to be a real introduction rather than a technicality.
    """
    if source not in adjacency or target not in adjacency:
        return []
    if source == target:
        return [source]
    # Dijkstra on 1/weight: a heavier tie is a shorter step.
    best: dict[str, float] = {source: 0.0}
    previous: dict[str, str] = {}
    queue: list[tuple[float, int, str]] = [(0.0, 0, source)]
    while queue:
        cost, hops, node = heapq.heappop(queue)
        if node == target:
            path = [node]
            while path[-1] != source:
                path.append(previous[path[-1]])
            return list(reversed(path))
        if hops >= limit or cost > best.get(node, float("inf")):
            continue
        for neighbour, weight in sorted(adjacency[node].items()):
            step = 1.0 / max(float(weight), 1e-6)
            candidate = cost + step
            if candidate < best.get(neighbour, float("inf")):
                best[neighbour] = candidate
                previous[neighbour] = node
                heapq.heappush(queue, (candidate, hops + 1, neighbour))
    return []


# ---------------------------------------------------------------------------
# The bundle written to `entity_metrics`
# ---------------------------------------------------------------------------

#: Measured over co-authorship alone. Shared technique and shared subject are
#: derived similarities, not observed relations, and mixing an observation with
#: an inference into one centrality produces a number that means neither.
def structural_metrics(
    adjacency: Adjacency,
    *,
    inside: Sequence[str] = (),
    pivots: int = 256,
) -> tuple[list[tuple[str, str, float, dict[str, Any]]], dict[str, Any]]:
    """Every structural measure, as rows ready for `entity_metrics`."""
    if not adjacency:
        return [], {"nodes": 0, "edges": 0}

    labels = communities(adjacency)
    measures = {
        "degree": degree(adjacency),
        "weighted_degree": weighted_degree(adjacency),
        "betweenness": betweenness(adjacency, pivots=pivots),
        "bridging": bridging(adjacency, labels),
    }
    if inside:
        measures["external_reach"] = reach(adjacency, inside)

    rows: list[tuple[str, str, float, dict[str, Any]]] = []
    for metric, values in measures.items():
        for node, value in values.items():
            rows.append((node, metric, float(value), {}))
    for node, label in labels.items():
        rows.append((node, "community", float(label[1:]), {"label": label}))

    groups = components(adjacency)
    stats = {
        "nodes": len(adjacency),
        "edges": sum(len(v) for v in adjacency.values()) // 2,
        "communities": len(set(labels.values())),
        "components": len(groups),
        "largest_component": len(groups[0]) if groups else 0,
    }
    return rows, stats
