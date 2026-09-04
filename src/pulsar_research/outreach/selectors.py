"""Audience selection: structured filters + research intelligence + retrieval.

An audience is never "everyone with a funded slot". Every recipient carries the
exact records that qualified them — which opportunity, which project, which
portfolio evidence, which channel scored it — so a campaign can always answer
*why this person* months later.

Semantic filtering always scores against the **full** corpus and filters
afterwards. Scoring only the survivors of a structured filter would let a
candidate's score change because some unrelated candidate was excluded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import pandas as pd

from ..db import Database, json_load
from ..semantics.provenance import current_run_id


@dataclass(slots=True)
class AudienceQuery:
    """Everything that defines an audience. Persisted verbatim with the campaign."""

    funded_only: bool = True
    centers: list[str] = field(default_factory=list)
    departments: list[str] = field(default_factory=list)
    require_email: bool = True
    exclude_already_contacted: bool = True

    # Research-intelligence filters
    min_current_percentile: float | None = None
    min_trajectory_percentile: float | None = None
    min_opportunity_percentile: float | None = None
    rank_channel: str = "fused"
    rank_facet: str = "overall"

    # Structure filters
    topic_ids: list[str] = field(default_factory=list)
    topic_facet: str = "domain"
    min_topic_weight: float = 0.15
    skill_ids: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    # Portfolio thresholds
    min_public_projects: int | None = None
    min_publications: int | None = None
    min_funders: int | None = None

    limit: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AudienceQuery":
        return cls(**{k: v for k, v in (payload or {}).items() if k in cls.__slots__})


def _opportunity_scores(db: Database, run_id: str, facet: str, channel: str) -> dict[str, float]:
    rows = db.query_df(
        "SELECT entity_id, percentile FROM entity_scores "
        "WHERE run_id=? AND entity_type='opportunity' AND facet=? AND channel=?",
        [run_id, facet, channel],
    )
    return {str(r.entity_id): float(r.percentile) for r in rows.itertuples()}


def _opportunity_readings(db: Database, run_id: str) -> dict[str, dict[str, dict[str, float]]]:
    """Every facet and every channel percentile, per opportunity.

    One query for the whole corpus: 187 opportunities times a handful of
    facet/channel pairs is a few thousand rows. Fetched in full because an
    outreach draft shows *which* facet drove a match, and a per-recipient query
    inside the campaign write transaction would deadlock on DuckDB's
    process-level write lock.
    """
    rows = db.query_df(
        "SELECT entity_id, facet, channel, percentile FROM entity_scores "
        "WHERE run_id=? AND entity_type='opportunity'",
        [run_id],
    )
    out: dict[str, dict[str, dict[str, float]]] = {}
    for r in rows.itertuples():
        entry = out.setdefault(str(r.entity_id), {"facets": {}, "channels": {}})
        percentile = float(r.percentile)
        # `facets` reads the fused channel across facets: what matched.
        # `channels` reads the domain facet across channels: whether the
        # independent representations agree that it matched.
        if r.channel == "fused":
            entry["facets"][str(r.facet)] = percentile
        if r.facet == "domain":
            entry["channels"][str(r.channel)] = percentile
    return out


def _professor_scores(db: Database, run_id: str, facet: str, channel: str) -> dict[str, float]:
    rows = db.query_df(
        "SELECT entity_id, percentile FROM entity_scores "
        "WHERE run_id=? AND entity_type='professor' AND facet=? AND channel=?",
        [run_id, facet, channel],
    )
    return {str(r.entity_id): float(r.percentile) for r in rows.itertuples()}


def select_audience(db: Database, query: AudienceQuery, *, run_id: str | None = None) -> list[dict[str, Any]]:
    """Build the recipient list, each with its qualifying evidence and rationale."""
    run_id = run_id or current_run_id(db)
    clauses = ["COALESCE(o.professor_siape,'')<>''"]
    params: list[Any] = []
    if query.funded_only:
        clauses.append("o.has_funding=TRUE")
    if query.centers:
        clauses.append(f"o.center IN ({','.join('?' for _ in query.centers)})")
        params.extend(query.centers)
    if query.departments:
        clauses.append(f"p.department IN ({','.join('?' for _ in query.departments)})")
        params.extend(query.departments)
    if query.require_email:
        clauses.append("COALESCE(p.email,'')<>''")
    if query.min_public_projects is not None:
        clauses.append("COALESCE(pm.public_project_count,0)>=?")
        params.append(int(query.min_public_projects))
    if query.min_publications is not None:
        clauses.append("COALESCE(pm.publication_count,0)>=?")
        params.append(int(query.min_publications))
    if query.min_funders is not None:
        clauses.append("COALESCE(pm.funding_agency_count,0)>=?")
        params.append(int(query.min_funders))
    if query.exclude_already_contacted:
        clauses.append("NOT EXISTS (SELECT 1 FROM campaign_messages cm "
                       "WHERE cm.siape=o.professor_siape AND cm.status='sent')")
    for keyword in query.keywords:
        clauses.append(
            "LOWER(COALESCE(o.project_title,'')||' '||COALESCE(o.plan_title,'')||' '||"
            "COALESCE(o.area,'')||' '||COALESCE(o.methodology,'')||' '||"
            "COALESCE(o.objectives,'')) LIKE ?"
        )
        params.append(f"%{keyword.lower()}%")
    if query.topic_ids:
        clauses.append(
            "EXISTS (SELECT 1 FROM entity_topics et WHERE et.entity_type='opportunity' "
            f"AND et.entity_id=o.id_opportunity AND et.facet=? AND et.topic_id IN "
            f"({','.join('?' for _ in query.topic_ids)}) AND et.weight>=?)"
        )
        params.append(query.topic_facet)
        params.extend(query.topic_ids)
        params.append(float(query.min_topic_weight))
    if query.skill_ids:
        clauses.append(
            "EXISTS (SELECT 1 FROM entity_skills es WHERE es.entity_type='opportunity' "
            f"AND es.entity_id=o.id_opportunity AND es.skill_id IN "
            f"({','.join('?' for _ in query.skill_ids)}))"
        )
        params.extend(query.skill_ids)

    sql = f"""
    SELECT o.professor_siape, p.canonical_name, p.email, p.department, p.center, p.profile_summary,
           o.id_opportunity, o.project_code, o.project_title, o.plan_title, o.funded_slots,
           o.vacancies_text, o.edital, o.quota, o.area, o.large_area, o.objectives, o.methodology,
           COALESCE(pm.publication_count,0) AS publications,
           COALESCE(pm.public_project_count,0) AS public_projects,
           COALESCE(pm.funding_agency_count,0) AS funders,
           (a.id_opportunity IS NOT NULL) AS already_applied
    FROM opportunities o
    LEFT JOIN applications a ON a.id_opportunity=o.id_opportunity
    JOIN professors p ON p.siape=o.professor_siape
    LEFT JOIN professor_metrics pm ON pm.siape=o.professor_siape
    WHERE {' AND '.join(clauses)}
    ORDER BY p.canonical_name, o.id_opportunity
    """
    rows = db.query_df(sql, params).to_dict("records")

    opp_pct = _opportunity_scores(db, run_id, query.rank_facet, query.rank_channel) if run_id else {}
    current_pct = _professor_scores(db, run_id, "current", query.rank_channel) if run_id else {}
    trajectory_pct = _professor_scores(db, run_id, "trajectory", query.rank_channel) if run_id else {}
    readings = _opportunity_readings(db, run_id) if run_id else {}
    evidence_rows = _evidence_by_professor(db, run_id) if run_id else {}
    skills_by_opp = _skills_by_entity(db, "opportunity")

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        siape = str(row["professor_siape"])
        oid = str(row["id_opportunity"])
        opportunity_percentile = opp_pct.get(oid, 0.0)
        if query.min_opportunity_percentile is not None and opportunity_percentile < query.min_opportunity_percentile:
            continue
        recipient = grouped.setdefault(siape, {
            "siape": siape,
            "professor_name": row.get("canonical_name") or "",
            "email": row.get("email") or "",
            "department": row.get("department") or "",
            "center": row.get("center") or "",
            "profile_summary": row.get("profile_summary") or "",
            "current_percentile": current_pct.get(siape, 0.0),
            "trajectory_percentile": trajectory_pct.get(siape, 0.0),
            "publications": int(row.get("publications") or 0),
            "public_projects": int(row.get("public_projects") or 0),
            "funders": int(row.get("funders") or 0),
            "qualifying_opportunities": [],
            "portfolio_evidence": evidence_rows.get(siape, []),
        })
        recipient["qualifying_opportunities"].append({
            "id_opportunity": oid,
            "project_code": row.get("project_code") or "",
            "project_title": row.get("project_title") or "",
            "plan_title": row.get("plan_title") or "",
            "funded_slots": int(row.get("funded_slots") or 0),
            "vacancies_text": row.get("vacancies_text") or "",
            "edital": row.get("edital") or "",
            "quota": row.get("quota") or "",
            "area": row.get("area") or row.get("large_area") or "",
            "objectives": row.get("objectives") or "",
            "methodology": row.get("methodology") or "",
            "opportunity_percentile": opportunity_percentile,
            "already_applied": bool(row.get("already_applied")),
            "skills": skills_by_opp.get(oid, []),
            "reading": readings.get(oid, {}),
        })

    out: list[dict[str, Any]] = []
    for recipient in grouped.values():
        if query.min_current_percentile is not None and recipient["current_percentile"] < query.min_current_percentile:
            continue
        if query.min_trajectory_percentile is not None and recipient["trajectory_percentile"] < query.min_trajectory_percentile:
            continue
        recipient["qualifying_opportunities"].sort(key=lambda o: -o["opportunity_percentile"])
        best = recipient["qualifying_opportunities"][0]["opportunity_percentile"] if recipient["qualifying_opportunities"] else 0.0
        # The selection score is a *rank* blend, stated as such. It orders the
        # audience; it is never presented as a probability of interest.
        recipient["selection_score"] = round(
            0.5 * best + 0.3 * recipient["current_percentile"] + 0.2 * recipient["trajectory_percentile"], 3
        )
        recipient["rationale"] = _rationale(recipient)
        out.append(recipient)
    out.sort(key=lambda r: (-r["selection_score"], r["professor_name"]))
    return out[: query.limit] if query.limit else out


def _rationale(recipient: dict[str, Any]) -> dict[str, Any]:
    """A short, checkable statement of why this professor is in the audience."""
    best = recipient["qualifying_opportunities"][0] if recipient["qualifying_opportunities"] else {}
    return {
        "primary_opportunity": best.get("id_opportunity", ""),
        "primary_title": best.get("plan_title") or best.get("project_title") or "",
        "funded_slots": best.get("funded_slots", 0),
        "already_applied": bool(best.get("already_applied")),
        "opportunity_percentile": round(best.get("opportunity_percentile", 0.0), 1),
        # The per-facet and per-channel reading of the plan actually written
        # about, so the draft can show what matched rather than only how much.
        "reading": best.get("reading", {}),
        "current_percentile": round(recipient["current_percentile"], 1),
        "trajectory_percentile": round(recipient["trajectory_percentile"], 1),
        "matched_skills": [s["label"] for s in best.get("skills", []) if not s.get("generic")][:8],
        "top_evidence": [
            {"kind": e["kind"], "label": e["label"], "year": e["year"]}
            for e in recipient["portfolio_evidence"][:3]
        ],
    }


def _opt_int(value: Any) -> int | None:
    """DuckDB nullable integers arrive as pandas NA, which raises on truth-testing."""
    return None if value is None or pd.isna(value) else int(value)


def _evidence_by_professor(db: Database, run_id: str) -> dict[str, list[dict[str, Any]]]:
    rows = db.query_df(
        "SELECT siape, scope, evidence_rank, kind, label, year, score, weight, payload_json "
        "FROM professor_evidence WHERE run_id=? ORDER BY siape, scope, evidence_rank",
        [run_id],
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows.itertuples():
        out.setdefault(str(r.siape), []).append({
            "scope": r.scope, "rank": int(r.evidence_rank), "kind": r.kind, "label": r.label,
            "year": _opt_int(r.year),
            "score": float(r.score), "weight": float(r.weight),
            "payload": json_load(r.payload_json, {}),
        })
    for siape, items in out.items():
        items.sort(key=lambda e: (e["scope"] != "current", e["rank"]))
    return out


def _skills_by_entity(db: Database, entity_type: str) -> dict[str, list[dict[str, Any]]]:
    if not db.table_exists("entity_skills"):
        return {}
    rows = db.query_df(
        "SELECT entity_id, skill_id, label, category, generic, mentions FROM entity_skills "
        "WHERE entity_type=? ORDER BY entity_id, generic, mentions DESC",
        [entity_type],
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows.itertuples():
        out.setdefault(str(r.entity_id), []).append({
            "skill_id": r.skill_id, "label": r.label, "category": r.category,
            "generic": bool(r.generic), "mentions": int(r.mentions),
        })
    return out
