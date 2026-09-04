from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..db import Database
from ..analysis.text import quick_query_scores


def select_audience(
    db: Database,
    *,
    funded_only: bool = True,
    centers: list[str] | None = None,
    min_opportunity_fit: float | None = None,
    min_professor_fit: float | None = None,
    require_email: bool = True,
    exclude_already_contacted: bool = False,
    keywords: list[str] | None = None,
    clusters: list[int] | None = None,
    topic_ids: list[int] | None = None,
    min_topic_weight: float = 0.0,
    semantic_query: str | None = None,
    min_query_score: float | None = None,
    min_public_projects: int | None = None,
    min_publications: int | None = None,
    min_funders: int | None = None,
) -> list[dict[str, Any]]:
    centers = [c for c in (centers or []) if c]
    keywords = [k.strip() for k in (keywords or []) if k.strip()]
    clusters = list(clusters or [])
    topic_ids = list(topic_ids or [])
    clauses = ["COALESCE(o.professor_siape,'')<>''"]
    params: list[Any] = []
    if funded_only:
        clauses.append("o.has_funding=TRUE")
    if centers:
        placeholders = ",".join("?" for _ in centers)
        clauses.append(f"o.center IN ({placeholders})")
        params.extend(centers)
    if require_email:
        clauses.append("COALESCE(p.email,'')<>''")
    if min_opportunity_fit is not None:
        clauses.append("COALESCE(os.combined_score,0)>=?")
        params.append(float(min_opportunity_fit))
    if min_professor_fit is not None:
        clauses.append("COALESCE(ps.combined_score,0)>=?")
        params.append(float(min_professor_fit))
    if min_public_projects is not None:
        clauses.append("COALESCE(pm.public_project_count,0)>=?")
        params.append(int(min_public_projects))
    if min_publications is not None:
        clauses.append("COALESCE(pm.publication_count,0)>=?")
        params.append(int(min_publications))
    if min_funders is not None:
        clauses.append("COALESCE(pm.funding_agency_count,0)>=?")
        params.append(int(min_funders))
    if exclude_already_contacted:
        clauses.append("NOT EXISTS (SELECT 1 FROM campaign_messages cm WHERE cm.siape=o.professor_siape AND cm.status='sent')")
    if keywords:
        for keyword in keywords:
            clauses.append("LOWER(COALESCE(o.project_title,'') || ' ' || COALESCE(o.plan_title,'') || ' ' || COALESCE(o.area,'') || ' ' || COALESCE(o.methodology,'') || ' ' || COALESCE(o.objectives,'')) LIKE ?")
            params.append('%' + keyword.lower() + '%')
    if clusters:
        placeholders = ",".join("?" for _ in clusters)
        clauses.append(f"os.cluster_id IN ({placeholders})")
        params.extend(clusters)
    if topic_ids:
        placeholders = ",".join("?" for _ in topic_ids)
        clauses.append(f"EXISTS (SELECT 1 FROM entity_topics et WHERE et.entity_type='opportunity' AND et.entity_id=o.id_opportunity AND et.topic_id IN ({placeholders}) AND et.weight>=?)")
        params.extend(topic_ids); params.append(float(min_topic_weight))

    sql = f"""
    SELECT
      o.professor_siape, p.canonical_name, p.email, p.department, p.center,
      p.profile_summary,
      o.id_opportunity, o.project_code, o.project_title, o.plan_title,
      o.funded_slots, o.vacancies_text, o.edital, o.quota, o.area, o.large_area,
      o.methodology, o.objectives,
      COALESCE(os.combined_score,0) opportunity_fit,
      COALESCE(ps.combined_score,0) professor_fit
    FROM opportunities o
    JOIN professors p ON p.siape=o.professor_siape
    LEFT JOIN analysis_scores os ON os.entity_type='opportunity' AND os.entity_id=o.id_opportunity
    LEFT JOIN analysis_scores ps ON ps.entity_type='professor' AND ps.entity_id=o.professor_siape
    LEFT JOIN professor_metrics pm ON pm.siape=o.professor_siape
    WHERE {' AND '.join(clauses)}
    ORDER BY p.canonical_name, opportunity_fit DESC, o.id_opportunity
    """
    with db.connect(read_only=True) as con:
        rows = con.execute(sql, params).fetchdf().to_dict("records")

    if semantic_query and rows:
        query_docs = [" ".join([str(r.get("project_title") or ""), str(r.get("plan_title") or ""), str(r.get("area") or ""), str(r.get("methodology") or ""), str(r.get("objectives") or "")]) for r in rows]
        qscores = quick_query_scores(query_docs, semantic_query)
        enriched = []
        for r, score in zip(rows, qscores):
            r["campaign_query_score"] = float(score)
            if min_query_score is None or float(score) >= float(min_query_score):
                enriched.append(r)
        rows = enriched
    else:
        for r in rows:
            r["campaign_query_score"] = 0.0

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        siape = str(row["professor_siape"])
        recipient = grouped.setdefault(siape, {
            "siape": siape,
            "professor_name": row.get("canonical_name") or "",
            "email": row.get("email") or "",
            "department": row.get("department") or "",
            "center": row.get("center") or "",
            "profile_summary": row.get("profile_summary") or "",
            "professor_fit": float(row.get("professor_fit") or 0),
            "qualifying_opportunities": [],
        })
        recipient["qualifying_opportunities"].append({
            "id_opportunity": str(row.get("id_opportunity") or ""),
            "project_code": row.get("project_code") or "",
            "project_title": row.get("project_title") or "",
            "plan_title": row.get("plan_title") or "",
            "funded_slots": int(row.get("funded_slots") or 0),
            "vacancies_text": row.get("vacancies_text") or "",
            "edital": row.get("edital") or "",
            "quota": row.get("quota") or "",
            "area": row.get("area") or row.get("large_area") or "",
            "methodology": row.get("methodology") or "",
            "objectives": row.get("objectives") or "",
            "opportunity_fit": float(row.get("opportunity_fit") or 0),
            "campaign_query_score": float(row.get("campaign_query_score") or 0),
        })
    out = list(grouped.values())
    for rec in out:
        query_scores = [o.get("campaign_query_score", 0.0) for o in rec["qualifying_opportunities"]]
        rec["selection_score"] = max(query_scores) if semantic_query else max(
            [rec["professor_fit"]] + [o["opportunity_fit"] for o in rec["qualifying_opportunities"]]
        )
    out.sort(key=lambda x: x["selection_score"], reverse=True)
    return out
