"""Reading and writing the entity graph.

Everything here goes through `graph_entities` and `graph_edges`, and nothing
here knows what a professor is. That is the point: a screen that wants "who is
two hops from this person, by any relation" should not have to be rewritten when
the platform learns about a new kind of thing.

Symmetric relations are stored once and expanded on read. Storing them twice is
the standard way a degree count silently doubles, and it makes "how many edges
does this graph have" a question with two defensible answers.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from ..db import Database, json_text, utcnow
from ..semantics.normalize import normalize_person_name
from .model import Edge, Entity, Kind, Relation, SYMMETRIC


def edge_id(source_id: str, target_id: str, relation: Relation) -> str:
    """Stable id, so re-projecting the same fact twice is idempotent."""
    payload = f"{source_id}|{target_id}|{relation.value}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def write_graph(
    db: Database,
    entities: Iterable[Entity],
    edges: Iterable[Edge],
    *,
    validate: bool = True,
) -> dict[str, Any]:
    """Replace the graph wholesale.

    A projection is not an accumulation: an opportunity that closed, a
    co-author whose name was corrected upstream, a department that was renamed
    must all *disappear*, and an incremental upsert cannot express that. The
    graph is cheap to rebuild — it is one pass over facts already in the store —
    so the honest operation is a replacement.
    """
    entities = list(entities)
    edges = list(edges)
    kinds = {e.entity_id: e.kind for e in entities}

    if validate:
        known = set(kinds)
        for edge in edges:
            missing = {edge.source_id, edge.target_id} - known
            if missing:
                raise ValueError(
                    f"{edge.relation.value} edge references unknown entities: {sorted(missing)}")
            edge.validate(kinds)

    # Two facts can imply the same relation — a co-authorship seen from both
    # ends, a person leading a project through two atoms — so weights are
    # summed rather than last-write-wins.
    merged: dict[str, Edge] = {}
    for edge in edges:
        key = edge_id(edge.source_id, edge.target_id, edge.relation)
        existing = merged.get(key)
        if existing is None:
            merged[key] = edge
        else:
            existing.weight += edge.weight
            if edge.year and (existing.year is None or edge.year > existing.year):
                existing.year = edge.year

    now = utcnow()
    entity_rows = [
        [e.entity_id, e.kind.value, e.key, e.name,
         normalize_person_name(e.name) if e.kind is Kind.PERSON else (e.name or "").lower(),
         json_text(e.payload), json_text(list(e.sources)), now]
        for e in entities]
    edge_rows = [
        [key, e.source_id, e.target_id, e.relation.value, float(e.weight),
         e.directed, e.year, e.source, json_text(e.evidence), now]
        for key, e in merged.items()]

    with db.connect() as con:
        con.execute("DELETE FROM graph_entities")
        con.execute("DELETE FROM graph_edges")
        # An empty projection is a legitimate result — a store with nothing
        # acquired yet, or a graph deliberately emptied — and DuckDB rejects
        # `executemany` with no parameter sets, so it must not be reached.
        if entity_rows:
            con.executemany("INSERT INTO graph_entities VALUES (?,?,?,?,?,?,?,?)", entity_rows)
        if edge_rows:
            con.executemany("INSERT INTO graph_edges VALUES (?,?,?,?,?,?,?,?,?,?)", edge_rows)
        con.execute("CHECKPOINT")

    return {
        "entities": len(entities),
        "edges": len(merged),
        "by_kind": _tally(e.kind.value for e in entities),
        "by_relation": _tally(e.relation.value for e in merged.values()),
    }


def record_build(db: Database, fingerprint: str, stats: Mapping[str, Any]) -> str:
    """Journal what this projection saw, so staleness is answerable later."""
    build_id = hashlib.sha1(
        f"{fingerprint}|{json.dumps(stats, sort_keys=True, default=str)}".encode("utf-8")
    ).hexdigest()[:16]
    with db.connect() as con:
        con.execute("DELETE FROM graph_builds")
        con.execute("INSERT INTO graph_builds VALUES (?,?,?,?)",
                    [build_id, fingerprint, json_text(dict(stats)), utcnow()])
    return build_id


def write_metrics(db: Database, rows: Iterable[tuple[str, str, float, Mapping[str, Any] | None]]) -> int:
    """Replace the structural measures. Tall, so a new measure is a new row."""
    now = utcnow()
    payload = [[entity_id, metric, float(value), json_text(dict(extra or {})), now]
               for entity_id, metric, value, extra in rows]
    with db.connect() as con:
        con.execute("DELETE FROM entity_metrics")
        if payload:
            con.executemany("INSERT INTO entity_metrics VALUES (?,?,?,?,?)", payload)
    return len(payload)


def _tally(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def summary(db: Database) -> dict[str, Any]:
    """Size and composition, for `pulsar graph status` and the console."""
    if not db.table_exists("graph_entities"):
        return {"entities": 0, "edges": 0, "by_kind": {}, "by_relation": {}}
    kinds = db.query_df("SELECT kind, COUNT(*) n FROM graph_entities GROUP BY 1 ORDER BY 2 DESC")
    rels = db.query_df(
        "SELECT relation, COUNT(*) n, SUM(weight) w FROM graph_edges GROUP BY 1 ORDER BY 2 DESC")
    build = db.query_df("SELECT * FROM graph_builds LIMIT 1")
    return {
        "entities": int(kinds["n"].sum()) if len(kinds) else 0,
        "edges": int(rels["n"].sum()) if len(rels) else 0,
        "by_kind": {r.kind: int(r.n) for r in kinds.itertuples()},
        "by_relation": {r.relation: int(r.n) for r in rels.itertuples()},
        "built_at": str(build.iloc[0]["created_at"]) if len(build) else None,
        "corpus_fingerprint": str(build.iloc[0]["corpus_fingerprint"]) if len(build) else None,
    }


def entity(db: Database, entity_id: str) -> dict[str, Any] | None:
    rows = db.query_df("SELECT * FROM graph_entities WHERE entity_id=?", [entity_id])
    if not len(rows):
        return None
    row = rows.iloc[0].to_dict()
    row["payload"] = json.loads(row.pop("payload_json") or "{}")
    row["sources"] = json.loads(row.pop("sources_json") or "[]")
    return row


def neighbours(
    db: Database,
    entity_id: str,
    *,
    relations: Sequence[Relation] | None = None,
    kinds: Sequence[Kind] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """One hop out, in either direction, with the relation that got you there.

    A symmetric edge is stored once, so both directions are unioned here rather
    than at write time — the read is where "in either direction" is what the
    caller actually meant.
    """
    where = ["(e.source_id = ? OR e.target_id = ?)"]
    params: list[Any] = [entity_id, entity_id]
    if relations:
        where.append("e.relation IN (" + ",".join("?" * len(relations)) + ")")
        params.extend(r.value for r in relations)
    if kinds:
        where.append("n.kind IN (" + ",".join("?" * len(kinds)) + ")")
        params.extend(k.value for k in kinds)
    params.append(int(limit))

    # The pivot id is bound three times rather than interpolated: entity ids are
    # built from scraped text, and a name carrying an apostrophe would otherwise
    # end the string literal.
    sql = f"""
        SELECT n.entity_id, n.kind, n.name, e.relation, e.weight, e.year, e.source,
               CASE WHEN e.source_id = ? THEN 'out' ELSE 'in' END AS direction
        FROM graph_edges e
        JOIN graph_entities n
          ON n.entity_id = CASE WHEN e.source_id = ? THEN e.target_id ELSE e.source_id END
        WHERE {' AND '.join(where)} AND n.entity_id <> ?
        ORDER BY e.weight DESC
        LIMIT ?
    """
    return db.query_df(sql, [entity_id, entity_id, *params[:-1], entity_id, params[-1]]).to_dict("records")


def adjacency(
    db: Database,
    *,
    relations: Sequence[Relation] | None = None,
    kinds: Sequence[Kind] | None = None,
) -> dict[str, dict[str, float]]:
    """The whole graph as a weighted adjacency map, undirected.

    Structural measures — centrality, communities, bridges — are questions about
    reachability, and reachability does not care which way an authorship arrow
    points. Callers that need direction read `graph_edges` themselves.
    """
    where: list[str] = []
    params: list[Any] = []
    if relations:
        where.append("e.relation IN (" + ",".join("?" * len(relations)) + ")")
        params.extend(r.value for r in relations)
    join = ""
    if kinds:
        placeholders = ",".join("?" * len(kinds))
        join = (" JOIN graph_entities a ON a.entity_id = e.source_id"
                " JOIN graph_entities b ON b.entity_id = e.target_id")
        where.append(f"a.kind IN ({placeholders}) AND b.kind IN ({placeholders})")
        params.extend([k.value for k in kinds] * 2)
    sql = ("SELECT e.source_id, e.target_id, e.weight FROM graph_edges e" + join
           + (" WHERE " + " AND ".join(where) if where else ""))

    out: dict[str, dict[str, float]] = {}
    for row in db.query_df(sql, params).itertuples():
        out.setdefault(row.source_id, {})[row.target_id] = float(row.weight)
        out.setdefault(row.target_id, {})[row.source_id] = float(row.weight)
    return out


def metrics_for(db: Database, entity_id: str) -> dict[str, float]:
    rows = db.query_df("SELECT metric, value FROM entity_metrics WHERE entity_id=?", [entity_id])
    return {r.metric: float(r.value) for r in rows.itertuples()}


def top_by_metric(db: Database, metric: str, *, kind: Kind | None = None,
                  limit: int = 20, min_degree: int = 0) -> list[dict[str, Any]]:
    """Rank entities by one measure, with degree shown beside it.

    Degree is not decoration. Every normalized share — bridging, external reach
    — is trivially extreme on a vertex with two or three ties: a co-author who
    appears once, on one paper, with someone outside their cluster, scores a
    perfect bridging of 1.0 and means nothing by it. `min_degree` is how a
    caller says which vertices are worth ranking at all, and printing the degree
    is how a reader can tell whether the answer was worth having.
    """
    sql = ("SELECT m.entity_id, n.kind, n.name, m.value, "
           "       CAST(COALESCE(d.value, 0) AS INTEGER) AS degree "
           "FROM entity_metrics m "
           "JOIN graph_entities n ON n.entity_id = m.entity_id "
           "LEFT JOIN entity_metrics d ON d.entity_id = m.entity_id AND d.metric = 'degree' "
           "WHERE m.metric = ?")
    params: list[Any] = [metric]
    if kind:
        sql += " AND n.kind = ?"
        params.append(kind.value)
    if min_degree > 0:
        sql += " AND COALESCE(d.value, 0) >= ?"
        params.append(float(min_degree))
    sql += " ORDER BY m.value DESC, n.entity_id LIMIT ?"
    params.append(int(limit))
    return db.query_df(sql, params).to_dict("records")
