from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

from ..config import AppConfig
from ..db import Database, json_text, utcnow
from .selectors import select_audience


def _read_default_template(name: str) -> str:
    p = Path(__file__).resolve().parent.parent / "templates" / name
    return p.read_text(encoding="utf-8")


def _render(subject_template: str, body_template: str, recipient: dict[str, Any], signature: str) -> tuple[str, str]:
    env = Environment(undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True)
    primary = recipient["qualifying_opportunities"][0] if recipient.get("qualifying_opportunities") else {}
    ctx = {**recipient, "primary": primary, "signature": signature}
    subject = env.from_string(subject_template).render(**ctx).strip()
    body = env.from_string(body_template).render(**ctx).strip() + "\n"
    return subject, body


def create_campaign(
    config: AppConfig,
    name: str,
    *,
    funded_only: bool = True,
    centers: list[str] | None = None,
    min_opportunity_fit: float | None = None,
    min_professor_fit: float | None = None,
    exclude_already_contacted: bool = True,
    subject_template: str | None = None,
    body_template: str | None = None,
    keywords: list[str] | None = None,
    clusters: list[int] | None = None,
    topic_ids: list[int] | None = None,
    min_topic_weight: float = 0.0,
    semantic_query: str | None = None,
    min_query_score: float | None = None,
    min_public_projects: int | None = None,
    min_publications: int | None = None,
    min_funders: int | None = None,
) -> tuple[str, int]:
    db = Database(config.paths.database); db.initialize()
    audience = select_audience(
        db, funded_only=funded_only, centers=centers,
        min_opportunity_fit=min_opportunity_fit, min_professor_fit=min_professor_fit,
        require_email=True, exclude_already_contacted=exclude_already_contacted,
        keywords=keywords, clusters=clusters, topic_ids=topic_ids, min_topic_weight=min_topic_weight,
        semantic_query=semantic_query, min_query_score=min_query_score,
        min_public_projects=min_public_projects, min_publications=min_publications, min_funders=min_funders,
    )
    campaign_id = uuid.uuid4().hex[:12]
    subject_template = subject_template or _read_default_template("default_subject.j2")
    body_template = body_template or _read_default_template("default_body.j2")
    criteria = {
        "funded_only": funded_only, "centers": centers or [],
        "min_opportunity_fit": min_opportunity_fit, "min_professor_fit": min_professor_fit,
        "exclude_already_contacted": exclude_already_contacted,
        "keywords": keywords or [], "clusters": clusters or [], "topic_ids": topic_ids or [],
        "min_topic_weight": min_topic_weight, "semantic_query": semantic_query or "", "min_query_score": min_query_score,
        "min_public_projects": min_public_projects, "min_publications": min_publications, "min_funders": min_funders,
    }
    now = utcnow(); profile = config.load_profile(); signature = profile.get("campaign_signature") or ""
    with db.connect() as con:
        con.execute("INSERT INTO campaigns VALUES (?,?,?,?,?,?,?,?)", [campaign_id, name, json_text(criteria), subject_template, body_template, "draft", now, now])
        for rec in audience:
            evidence = rec["qualifying_opportunities"]
            con.execute("INSERT INTO campaign_recipients VALUES (?,?,?,?,?,?,?,?)", [campaign_id, rec["siape"], rec["professor_name"], rec["email"], json_text(evidence), rec["selection_score"], True, now])
            subject, body = _render(subject_template, body_template, rec, signature)
            con.execute("INSERT INTO campaign_messages VALUES (?,?,?,?,?,?,?,?,?,?)", [campaign_id, rec["siape"], subject, body, False, "draft", now, "", "", ""])
    return campaign_id, len(audience)


def campaign_rows(db: Database, campaign_id: str) -> list[dict[str, Any]]:
    with db.connect(read_only=True) as con:
        rows = con.execute(
            """
            SELECT r.campaign_id,r.siape,r.professor_name,r.email,r.qualifying_evidence_json,
                   r.selection_score,r.selected,m.subject,m.body,m.is_customized,m.status,m.sent_at,m.error
            FROM campaign_recipients r
            JOIN campaign_messages m USING(campaign_id,siape)
            WHERE r.campaign_id=? ORDER BY r.selection_score DESC,r.professor_name
            """, [campaign_id]
        ).fetchdf().to_dict("records")
    for row in rows:
        try: row["qualifying_evidence"] = json.loads(row.pop("qualifying_evidence_json") or "[]")
        except Exception: row["qualifying_evidence"] = []
    return rows


def update_message(db: Database, campaign_id: str, siape: str, *, subject: str, body: str, selected: bool | None = None) -> None:
    with db.connect() as con:
        con.execute("UPDATE campaign_messages SET subject=?,body=?,is_customized=TRUE,rendered_at=? WHERE campaign_id=? AND siape=?", [subject, body, utcnow(), campaign_id, siape])
        if selected is not None:
            con.execute("UPDATE campaign_recipients SET selected=? WHERE campaign_id=? AND siape=?", [selected, campaign_id, siape])
        con.execute("UPDATE campaigns SET updated_at=? WHERE campaign_id=?", [utcnow(), campaign_id])


def set_recipient_selected(db: Database, campaign_id: str, siape: str, selected: bool) -> None:
    with db.connect() as con:
        con.execute("UPDATE campaign_recipients SET selected=? WHERE campaign_id=? AND siape=?", [selected, campaign_id, siape])


def export_campaign(db: Database, campaign_id: str, path: Path) -> Path:
    rows = campaign_rows(db, campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        fields = ["siape","professor_name","email","selection_score","selected","subject","body","status","qualifying_evidence"]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({**{k: row.get(k, "") for k in fields}, "qualifying_evidence": json.dumps(row.get("qualifying_evidence", []), ensure_ascii=False)})
    return path
