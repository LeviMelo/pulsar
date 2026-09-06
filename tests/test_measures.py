"""Structural measures, checked against graphs whose answers are known by hand.

Centrality is the easiest thing in the project to get subtly wrong and the
hardest to notice: any implementation returns plausible-looking floats, and a
ranking that is quietly off by a factor of two still ranks. So each measure is
run over a graph small enough that the right answer can be argued in a comment,
plus one property that matters more than any single value — that two runs on
unchanged data agree. A centrality that moves on its own is not a measurement,
and an operator who sees a rank change has to be able to conclude the data
changed.
"""
from __future__ import annotations

from pulsar_research.intelligence import network as net


def undirected(*pairs, weight: float = 1.0) -> dict[str, dict[str, float]]:
    graph: dict[str, dict[str, float]] = {}
    for a, b in pairs:
        graph.setdefault(a, {})[b] = weight
        graph.setdefault(b, {})[a] = weight
    return graph


#: Two triangles joined by a single vertex. `c` is the only way across, so it is
#: the whole answer to every "who sits between" question this graph can pose.
BOWTIE = undirected(("a", "b"), ("a", "c"), ("b", "c"),
                    ("c", "d"), ("c", "e"), ("d", "e"))

#: A star: `hub` touches everything, the leaves touch only the hub.
STAR = undirected(("hub", "l1"), ("hub", "l2"), ("hub", "l3"), ("hub", "l4"))


# ---------------------------------------------------------------------------
# Degree
# ---------------------------------------------------------------------------


def test_degree_counts_partners_and_weighted_degree_counts_strength():
    """Ten papers with one person is one collaborator, not ten."""
    graph = {"a": {"b": 10.0}, "b": {"a": 10.0, "c": 1.0}, "c": {"b": 1.0}}
    assert net.degree(graph) == {"a": 1.0, "b": 2.0, "c": 1.0}
    assert net.weighted_degree(graph) == {"a": 10.0, "b": 11.0, "c": 1.0}


# ---------------------------------------------------------------------------
# Betweenness
# ---------------------------------------------------------------------------


def test_the_cut_vertex_carries_every_path_across():
    """In the bow-tie, only `c` lies between the two triangles."""
    scores = net.betweenness(BOWTIE)
    assert scores["c"] == max(scores.values())
    # `a`, `b`, `d`, `e` each sit inside a triangle where every pair is adjacent,
    # so no shortest path passes through any of them.
    assert all(scores[node] == 0.0 for node in ("a", "b", "d", "e"))


def test_betweenness_is_a_share_not_a_raw_count():
    """The hub of a star lies between every pair of leaves — all of them."""
    assert net.betweenness(STAR)["hub"] == 1.0
    assert all(0.0 <= v <= 1.0 for v in net.betweenness(BOWTIE).values())


def test_a_graph_too_small_to_have_a_middle_scores_zero():
    assert net.betweenness({"a": {"b": 1.0}, "b": {"a": 1.0}}) == {"a": 0.0, "b": 0.0}


def test_sampling_does_not_make_the_answer_move():
    """Below the pivot budget the estimate is exact; above it, still deterministic."""
    exact = net.betweenness(BOWTIE, pivots=256)
    assert net.betweenness(BOWTIE, pivots=256) == exact
    sampled = net.betweenness(BOWTIE, pivots=2)
    assert sampled == net.betweenness(BOWTIE, pivots=2)
    assert max(sampled, key=sampled.get) == "c"


# ---------------------------------------------------------------------------
# Communities and bridging
# ---------------------------------------------------------------------------


def test_two_cliques_joined_by_one_thin_edge_are_two_communities():
    graph = undirected(("a", "b"), ("a", "c"), ("b", "c"),
                       ("x", "y"), ("x", "z"), ("y", "z"))
    graph["c"]["x"] = 0.1
    graph["x"]["c"] = 0.1
    labels = net.communities(graph)
    assert labels["a"] == labels["b"] == labels["c"]
    assert labels["x"] == labels["y"] == labels["z"]
    assert labels["a"] != labels["x"]


def test_community_ids_are_ordered_by_size_so_c0_means_the_same_thing_twice():
    graph = undirected(("a", "b"), ("a", "c"), ("b", "c"), ("x", "y"))
    labels = net.communities(graph)
    assert labels["a"] == "c0"
    assert net.communities(graph) == labels


def test_bridging_is_the_share_of_strength_that_leaves_the_cluster():
    labels = {"a": "c0", "b": "c0", "x": "c1"}
    graph = {"a": {"b": 3.0, "x": 1.0}, "b": {"a": 3.0}, "x": {"a": 1.0}}
    scores = net.bridging(graph, labels)
    assert scores["a"] == 0.25          # one of four units of strength leaves
    assert scores["b"] == 0.0           # entirely embedded, structurally redundant
    assert scores["x"] == 1.0           # everything it has is a tie outward


def test_external_reach_separates_central_here_from_central_elsewhere():
    """The distinction the faculty-only graph cannot make."""
    graph = {"inside": {"other": 2.0, "stranger": 6.0},
             "other": {"inside": 2.0},
             "stranger": {"inside": 6.0}}
    scores = net.reach(graph, inside=["inside", "other"])
    assert scores["inside"] == 0.75
    assert scores["other"] == 0.0


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_the_path_prefers_a_strong_chain_over_a_short_one():
    """A heavier tie is a shorter step: an introduction that would actually happen."""
    graph = undirected(("a", "weak"), ("weak", "d"))
    graph["a"]["s1"] = 10.0
    graph.setdefault("s1", {})["a"] = 10.0
    graph["s1"]["s2"] = 10.0
    graph.setdefault("s2", {})["s1"] = 10.0
    graph["s2"]["d"] = 10.0
    graph["d"]["s2"] = 10.0
    assert net.shortest_path(graph, "a", "d") == ["a", "s1", "s2", "d"]


def test_no_path_between_components_is_an_empty_list_not_an_exception():
    graph = undirected(("a", "b"), ("x", "y"))
    assert net.shortest_path(graph, "a", "y") == []
    assert net.shortest_path(graph, "a", "nobody") == []
    assert net.shortest_path(graph, "a", "a") == ["a"]


def test_components_are_reported_largest_first():
    graph = undirected(("a", "b"), ("b", "c"), ("x", "y"))
    assert [sorted(group) for group in net.components(graph)] == [["a", "b", "c"], ["x", "y"]]


# ---------------------------------------------------------------------------
# The bundle
# ---------------------------------------------------------------------------


def test_the_written_bundle_is_reproducible_row_for_row():
    """The property the whole module rests on."""
    first, stats = net.structural_metrics(BOWTIE, inside=["a", "b", "c"])
    second, stats2 = net.structural_metrics(BOWTIE, inside=["a", "b", "c"])
    assert first == second
    assert stats == stats2
    assert stats == {"nodes": 5, "edges": 6, "communities": stats["communities"],
                     "components": 1, "largest_component": 5}


def test_every_declared_measure_reaches_the_rows():
    rows, _ = net.structural_metrics(BOWTIE, inside=["a", "b", "c"])
    metrics = {metric for _, metric, _, _ in rows}
    assert metrics == {"degree", "weighted_degree", "betweenness", "bridging",
                       "external_reach", "community"}


def test_an_empty_graph_produces_nothing_rather_than_failing():
    rows, stats = net.structural_metrics({})
    assert rows == []
    assert stats["nodes"] == 0
