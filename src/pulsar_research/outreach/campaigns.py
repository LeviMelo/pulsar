"""Campaigns: frozen audiences, individual drafts, explicit sending.

A campaign is a **snapshot**. Creation stores the audience query, the exact
recipient set, the qualifying evidence, the semantic provenance that produced the
ranking, and one independently editable draft per recipient. Later data changes
can never rewrite why someone was selected or what was said to them.

Creation is not sending. Nothing in this module opens an SMTP connection.
"""

from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..db import Database, json_load, json_text, utcnow
from ..semantics.provenance import check_staleness, current_run_id, current_space_id
from .render import read_template, render_message
from .selectors import AudienceQuery, select_audience


def create_campaign(
    config: AppConfig,
    db: Database,
    name: str,
    query: AudienceQuery,
    *,
    subject_template: str | None = None,
    body_template: str | None = None,
    theme: str = "default_email.html",
    corpus_fingerprint: str | None = None,
    allow_stale: bool = False,
) -> tuple[str, int]:
    """Build an audience and persist one personalized draft per recipient."""
    if corpus_fingerprint and not allow_stale:
        state = check_staleness(db, corpus_fingerprint)
        if state.is_stale:
            raise RuntimeError(
                f"Refusing to create a campaign on stale semantics. {state.describe()}"
            )

    run_id = current_run_id(db)
    audience = select_audience(db, query, run_id=run_id)
    campaign_id = uuid.uuid4().hex[:12]
    subject_template = subject_template or read_template("default_subject.j2")
    body_template = body_template or read_template("default_body.j2")
    profile = config.load_profile()
    signature = profile.get("campaign_signature") or ""

    provenance = {
        "profile_run_id": run_id,
        "semantic_space_id": current_space_id(db),
        "corpus_fingerprint": corpus_fingerprint or "",
        "created_with": "pulsar",
    }
    now = utcnow()
    # Measured BEFORE the write connection is opened: DuckDB takes a
    # process-level write lock, so a read opened inside the transaction below
    # fails, and these numbers go into the body of an email.
    corpus_stats = _corpus_stats(db)
    with db.connect() as con:
        # One transaction: a campaign that exists with only half its recipients
        # is worse than no campaign, because the next step is sending it.
        con.execute("BEGIN TRANSACTION")
        try:
            _write_campaign(con, campaign_id, name, query, provenance, subject_template,
                            body_template, theme, audience, signature, profile, now,
                            corpus_stats=corpus_stats)
        except Exception:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")
    return campaign_id, len(audience)


def _corpus_stats(db: Database) -> dict[str, Any]:
    """Facts about the analysis, measured now and frozen into the campaign.

    The outreach discloses that it was produced by PULSAR and quotes the size of
    the corpus behind it. Those numbers are read from the live store rather than
    written into the template, so a later sync cannot turn a sentence in a sent
    email into a false claim.
    """
    def count(sql: str) -> int:
        return int(db.scalar(sql, default=0) or 0)

    stats = {
        "n_opportunities": count("SELECT COUNT(*) FROM opportunities"),
        "n_projects": count("SELECT COUNT(DISTINCT COALESCE(NULLIF(project_code,''), project_title)) "
                            "FROM opportunities"),
        "n_professors": count("SELECT COUNT(*) FROM professors"),
        "n_pages": count("SELECT COUNT(*) FROM sigaa_public_pages"),
        "n_atoms": 0,
        "channels": [],
    }
    stats["n_atoms"] = count(
        "SELECT CAST(json_extract_string(stats_json, '$.atoms') AS BIGINT) FROM semantic_spaces "
        "WHERE space_id = (SELECT value FROM meta WHERE key='current_semantic_space_id')")
    # A swallowed error here once produced "0 planos de trabalho" inside a draft.
    # An email that misstates the work behind it is worse than a failed build.
    empty = [k for k, v in stats.items() if isinstance(v, int) and v <= 0]
    if empty:
        raise RuntimeError(
            "refusing to build a campaign quoting empty corpus statistics: "
            f"{', '.join(sorted(empty))} came back as zero. Run `pulsar semantics build` first."
        )
    return {"pulsar": stats}


def _write_campaign(con, campaign_id, name, query, provenance, subject_template,
                    body_template, theme, audience, signature, profile, now,
                    corpus_stats=None) -> None:
    # Columns are named, not positional: a store migrated from v2 has the
    # widened outreach columns appended at the end, so VALUES(...) would
    # write the rationale into `selection_score`.
    con.execute(
        "INSERT INTO campaigns (campaign_id, name, audience_query_json, provenance_json, "
        "subject_template, body_template, theme, status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [campaign_id, name, json_text(query.to_dict()), json_text(provenance),
         subject_template, body_template, theme, "draft", now, now])
    for recipient in audience:
        con.execute(
            "INSERT INTO campaign_recipients (campaign_id, siape, professor_name, email, "
            "qualifying_evidence_json, rationale_json, selection_score, selected, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [campaign_id, recipient["siape"], recipient["professor_name"],
             recipient["email"], json_text(recipient["qualifying_opportunities"]),
             json_text(recipient["rationale"]), recipient["selection_score"], True, now])
        subject, body, body_html = render_message(
            subject_template, body_template, recipient, signature, profile,
            theme=theme, extra=corpus_stats,
        )
        con.execute(
            "INSERT INTO campaign_messages (campaign_id, siape, subject, body_text, body_html, "
            "is_customized, status, rendered_at, sent_at, provider_message_id, error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [campaign_id, recipient["siape"], subject, body, body_html,
             False, "draft", now, "", "", ""])


def campaign_rows(db: Database, campaign_id: str) -> list[dict[str, Any]]:
    rows = db.query_df(
        """
        SELECT r.campaign_id, r.siape, r.professor_name, r.email, r.qualifying_evidence_json,
               r.rationale_json, r.selection_score, r.selected,
               m.subject, m.body_text, m.body_html, m.is_customized, m.status, m.sent_at, m.error
        FROM campaign_recipients r
        JOIN campaign_messages m USING (campaign_id, siape)
        WHERE r.campaign_id=?
        ORDER BY r.selection_score DESC, r.professor_name
        """,
        [campaign_id],
    ).to_dict("records")
    for row in rows:
        row["qualifying_evidence"] = json_load(row.pop("qualifying_evidence_json"), [])
        row["rationale"] = json_load(row.pop("rationale_json"), {})
    return rows


def campaign_summary(db: Database, campaign_id: str) -> dict[str, Any]:
    head = db.query_df("SELECT * FROM campaigns WHERE campaign_id=?", [campaign_id])
    if not len(head):
        raise KeyError(f"Unknown campaign: {campaign_id}")
    record = head.iloc[0].to_dict()
    counts = db.query_df(
        "SELECT m.status, COUNT(*) AS n FROM campaign_messages m "
        "JOIN campaign_recipients r USING (campaign_id, siape) "
        "WHERE m.campaign_id=? GROUP BY m.status",
        [campaign_id],
    )
    selected = int(db.scalar(
        "SELECT COUNT(*) FROM campaign_recipients WHERE campaign_id=? AND selected", [campaign_id], 0))
    return {
        "campaign_id": campaign_id,
        "name": record.get("name"),
        "status": record.get("status"),
        "theme": record.get("theme"),
        "audience_query": json_load(record.get("audience_query_json"), {}),
        "provenance": json_load(record.get("provenance_json"), {}),
        "recipients": int(db.scalar("SELECT COUNT(*) FROM campaign_recipients WHERE campaign_id=?",
                                    [campaign_id], 0)),
        "selected": selected,
        "by_status": {str(r.status): int(r.n) for r in counts.itertuples()},
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def update_message(
    db: Database,
    campaign_id: str,
    siape: str,
    *,
    subject: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
    selected: bool | None = None,
) -> None:
    """Edit one recipient's draft. Never touches any other recipient's message."""
    now = utcnow()
    # Measured BEFORE the write connection is opened: DuckDB takes a
    # process-level write lock, so a read opened inside the transaction below
    # fails, and these numbers go into the body of an email.
    corpus_stats = _corpus_stats(db)
    with db.connect() as con:
        sets: list[str] = []
        params: list[Any] = []
        if subject is not None:
            sets.append("subject=?"); params.append(subject)
        if body_text is not None:
            sets.append("body_text=?"); params.append(body_text)
        if body_html is not None:
            sets.append("body_html=?"); params.append(body_html)
        if sets:
            sets.append("is_customized=TRUE")
            sets.append("rendered_at=?"); params.append(now)
            params.extend([campaign_id, siape])
            con.execute(f"UPDATE campaign_messages SET {', '.join(sets)} "
                        f"WHERE campaign_id=? AND siape=?", params)
        if selected is not None:
            con.execute("UPDATE campaign_recipients SET selected=? WHERE campaign_id=? AND siape=?",
                        [bool(selected), campaign_id, siape])
        con.execute("UPDATE campaigns SET updated_at=? WHERE campaign_id=?", [now, campaign_id])


def regenerate_html(config: AppConfig, db: Database, campaign_id: str, *, only_uncustomized: bool = True) -> int:
    """Re-render the HTML alternative from each stored plaintext body."""
    from .render import render_html, build_context

    profile = config.load_profile()
    signature = profile.get("campaign_signature") or ""
    theme = str(db.scalar("SELECT theme FROM campaigns WHERE campaign_id=?", [campaign_id],
                          "default_email.html"))
    rows = campaign_rows(db, campaign_id)
    updated = 0
    with db.connect() as con:
        for row in rows:
            if only_uncustomized and row.get("is_customized"):
                continue
            recipient = {
                "professor_name": row["professor_name"], "email": row["email"],
                "qualifying_opportunities": row["qualifying_evidence"],
                "rationale": row["rationale"],
            }
            context = build_context(recipient, signature, profile)
            html = render_html(row["body_text"] or "", {**context, "subject": row["subject"]}, theme=theme)
            con.execute("UPDATE campaign_messages SET body_html=? WHERE campaign_id=? AND siape=?",
                        [html, campaign_id, row["siape"]])
            updated += 1
    return updated


def export_campaign(db: Database, campaign_id: str, path: Path) -> Path:
    rows = campaign_rows(db, campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["siape", "professor_name", "email", "selection_score", "selected", "status",
              "subject", "body_text", "rationale", "qualifying_evidence"]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                **{k: row.get(k, "") for k in fields},
                "rationale": json.dumps(row.get("rationale", {}), ensure_ascii=False),
                "qualifying_evidence": json.dumps(row.get("qualifying_evidence", []), ensure_ascii=False),
            })
    return path


def write_preview(db: Database, campaign_id: str, path: Path, *, limit: int = 25) -> Path:
    """Dump the rendered HTML drafts to one local file for eyeball review."""
    rows = campaign_rows(db, campaign_id)[:limit]
    parts = [
        "<!doctype html><meta charset='utf-8'><title>PULSAR campaign preview</title>"
        "<body style='font-family:system-ui;background:#eef1f4;margin:0;padding:24px'>"
        f"<h1 style='font:600 20px system-ui'>Campaign {campaign_id} — {len(rows)} drafts</h1>"
    ]
    for row in rows:
        parts.append(
            f"<section style='margin:18px 0;background:#fff;border:1px solid #d6dde4;border-radius:10px;padding:16px'>"
            f"<div style='font:600 14px system-ui'>{row['professor_name']} &lt;{row['email']}&gt;</div>"
            f"<div style='font:12px system-ui;color:#5a6b7b'>score {row['selection_score']} · "
            f"{'selected' if row['selected'] else 'deselected'} · {row['status']}</div>"
            f"<div style='font:600 13px system-ui;margin-top:10px'>{row['subject']}</div>"
            f"<iframe style='width:100%;height:520px;border:1px solid #e5eaef;border-radius:8px;margin-top:8px' "
            f"srcdoc=\"{(row['body_html'] or '').replace('&', '&amp;').replace(chr(34), '&quot;')}\"></iframe>"
            f"</section>"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
