"""Scientometrics and portfolio metrics derived from the corpus.

These are counting facts, not semantic judgements: how many funded slots a
professor controls, how concentrated opportunity supply is, who collaborates with
whom. They are computed from the atom corpus rather than by regex over scraped
page text, so they agree with what the semantic engine actually indexed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import numpy as np

from ..db import Database, utcnow
from ..semantics.corpus import SemanticCorpus
from ..semantics.normalize import normalize_person_name


def gini(values: Iterable[float]) -> float:
    """0 = every professor holds an equal share; 1 = one professor holds all."""
    x = np.asarray(list(values), dtype=float)
    if x.size == 0 or np.allclose(x.sum(), 0):
        return 0.0
    x = np.sort(np.clip(x, 0, None))
    n = x.size
    idx = np.arange(1, n + 1)
    return float(np.sum((2 * idx - n - 1) * x) / (n * x.sum()))


def hhi(values: Iterable[float]) -> float:
    """Herfindahl-Hirschman index of concentration (1/n = perfectly even)."""
    x = np.asarray(list(values), dtype=float)
    total = x.sum()
    if total <= 0:
        return 0.0
    return float(np.sum((x / total) ** 2))


def top_share(values: Iterable[float], k: int = 10) -> float:
    x = np.sort(np.asarray(list(values), dtype=float))[::-1]
    total = x.sum()
    return float(x[:k].sum() / total) if total > 0 else 0.0


def collaboration_edges(db: Database) -> list[tuple[str, str, str, int]]:
    """Lattes project team members, resolved to SIAPEs where the name is known."""
    with db.connect(read_only=True) as con:
        has_lattes = bool(con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='sigaa_public_lattes_flat'"
        ).fetchone()[0])
        if not has_lattes:
            return []
        known: dict[str, str] = {}
        for siape, name in con.execute("SELECT siape,canonical_name FROM professors").fetchall():
            if name:
                known[normalize_person_name(name)] = str(siape)
        for alias, siape in con.execute(
            "SELECT normalized_alias,siape FROM professor_aliases WHERE COALESCE(siape,'')<>''"
        ).fetchall():
            if alias:
                known.setdefault(str(alias), str(siape))
        rows = con.execute(
            """
            SELECT siape, value_text, COUNT(*) AS n
            FROM sigaa_public_lattes_flat
            WHERE value_type='string'
              AND LOWER(path) LIKE '%equipedoprojeto%'
              AND LOWER(path) LIKE '%nomecompleto%'
              AND COALESCE(value_text,'')<>''
            GROUP BY siape, value_text
            """
        ).fetchall()
        own = {str(s): normalize_person_name(n or "") for s, n in
               con.execute("SELECT siape,canonical_name FROM professors").fetchall()}
    edges: list[tuple[str, str, str, int]] = []
    for siape, name, count in rows:
        norm = normalize_person_name(name)
        if not norm or norm == own.get(str(siape)):
            continue
        edges.append((str(siape), known.get(norm, ""), str(name), int(count)))
    return edges


def calculate_metrics(db: Database, corpus: SemanticCorpus) -> dict[str, Any]:
    """Recompute professor-level and corpus-level metrics."""
    now = utcnow()
    year = datetime.now(timezone.utc).year
    atoms = corpus.atoms_by_siape()
    funders = _funder_counts(db)
    edges = collaboration_edges(db)

    with db.connect() as con:
        con.execute("DELETE FROM professor_metrics")
        con.execute("DELETE FROM collaboration_edges")
        con.execute("DELETE FROM global_metrics")

        collaborators: Counter[str] = Counter()
        for source, target, name, weight in edges:
            collaborators[source] += 1
            con.execute("INSERT INTO collaboration_edges VALUES (?,?,?,?,?)",
                        [source, target, name, weight, now])

        opp_by_prof: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in corpus.opportunities:
            siape = str(row.get("professor_siape") or "")
            if siape:
                opp_by_prof[siape].append(row)

        for professor in corpus.professors:
            siape = str(professor["siape"])
            opps = opp_by_prof.get(siape, [])
            portfolio = atoms.get(siape, [])
            kinds = Counter(a.kind for a in portfolio)
            years = [a.year for a in portfolio if a.year]
            areas = {a for atom in portfolio for a in atom.areas if a}
            areas |= {str(r.get("area") or "") for r in opps if r.get("area")}
            con.execute(
                "INSERT OR REPLACE INTO professor_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    siape,
                    len(opps),
                    sum(1 for r in opps if r.get("has_funding")),
                    int(sum(int(r.get("funded_slots") or 0) for r in opps)),
                    sum(1 for a in portfolio if a.kind == "sigaa_project" and (a.year or 0) >= year - 1),
                    kinds.get("sigaa_project", 0),
                    kinds.get("lattes_project", 0),
                    kinds.get("article", 0) + kinds.get("book_chapter", 0) + kinds.get("conference", 0),
                    kinds.get("orientation", 0),
                    int(funders.get(siape, 0)),
                    int(collaborators.get(siape, 0)),
                    len({a for a in areas if a}),
                    len(portfolio),
                    max(years) if years else None,
                    now,
                ],
            )

        counts = [len(v) for v in opp_by_prof.values()]
        funded = [sum(int(r.get("funded_slots") or 0) for r in v) for v in opp_by_prof.values()]
        total = len(corpus.opportunities)
        funded_plans = sum(1 for r in corpus.opportunities if r.get("has_funding"))
        slots = int(sum(int(r.get("funded_slots") or 0) for r in corpus.opportunities))
        metrics = {
            "opportunities_total": float(total),
            "projects_total": float(len(corpus.projects)),
            "professors_total": float(len(corpus.professors)),
            "atoms_total": float(len(corpus.atoms)),
            "funded_opportunities": float(funded_plans),
            "funded_slots": float(slots),
            "funded_opportunity_fraction": float(funded_plans / total) if total else 0.0,
            "professors_with_funded_opportunity": float(sum(1 for v in funded if v > 0)),
            "gini_opportunities_by_professor": gini(counts),
            "hhi_opportunities_by_professor": hhi(counts),
            "gini_funded_slots_by_professor": gini(funded),
            "hhi_funded_slots_by_professor": hhi(funded),
            "top10_share_funded_slots": top_share(funded, 10),
        }
        for key, value in metrics.items():
            con.execute("INSERT INTO global_metrics VALUES (?,?,?,?)", [key, float(value), "{}", now])
    return metrics


def _funder_counts(db: Database) -> dict[str, int]:
    """Distinct funding institutions per professor, in one pass."""
    with db.connect(read_only=True) as con:
        has = bool(con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='sigaa_public_lattes_flat'"
        ).fetchone()[0])
        if not has:
            return {}
        rows = con.execute(
            """SELECT siape, COUNT(DISTINCT value_text) FROM sigaa_public_lattes_flat
               WHERE value_type='string'
                 AND LOWER(path) LIKE '%financiadoresdoprojeto%'
                 AND LOWER(path) LIKE '%nomeinstituicao%'
                 AND COALESCE(value_text,'')<>''
               GROUP BY siape""",
        ).fetchall()
    return {str(r[0]): int(r[1]) for r in rows}
