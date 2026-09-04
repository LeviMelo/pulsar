from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from pulsar_research.config import AppConfig
from pulsar_research.db import Database
from pulsar_research.outreach.campaigns import campaign_rows, create_campaign, update_message
from pulsar_research.outreach.mailer import SMTPMailer


st.set_page_config(page_title="PULSAR", page_icon="◉", layout="wide")


@st.cache_resource
def load_state():
    cfg = AppConfig.load(os.getenv("PULSAR_HOME") or Path.cwd())
    db = Database(cfg.paths.database)
    db.initialize()
    return cfg, db


cfg, db = load_state()


@st.cache_data(ttl=30)
def q(sql: str, params=()):
    return db.query_df(sql, list(params))


def clear_cache() -> None:
    st.cache_data.clear()


def fmt_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def topic_summary(entity_type: str) -> pd.DataFrame:
    """Summarize normalized NMF topic shares for one entity family."""
    return q(
        """
        WITH ranked AS (
            SELECT
                entity_id,
                topic_id,
                weight,
                ROW_NUMBER() OVER (
                    PARTITION BY entity_id
                    ORDER BY weight DESC, topic_id
                ) AS topic_rank
            FROM entity_topics
            WHERE entity_type=?
        )
        SELECT
            t.topic_id,
            t.label AS topic,
            ROUND(AVG(r.weight), 4) AS mean_share,
            SUM(CASE WHEN r.topic_rank=1 THEN 1 ELSE 0 END) AS dominant_entities,
            SUM(CASE WHEN r.weight>=0.15 THEN 1 ELSE 0 END) AS entities_ge_15pct
        FROM ranked r
        JOIN analysis_topics t USING(topic_id)
        GROUP BY t.topic_id, t.label
        ORDER BY dominant_entities DESC, mean_share DESC, t.topic_id
        """,
        (entity_type,),
    )


st.title("PULSAR")
st.caption("Research opportunity intelligence · professor intelligence · semantic landscape · campaigns")

with st.sidebar:
    st.markdown("### Runtime")
    st.caption("Database")
    st.code(str(cfg.paths.database), language=None)
    counts = db.counts()
    st.caption(
        f"{counts.get('professors', 0)} professors · "
        f"{counts.get('opportunities', 0)} opportunities · "
        f"{counts.get('campaigns', 0)} campaigns"
    )
    if st.button("Clear dashboard cache", use_container_width=True):
        clear_cache()
        st.rerun()

    st.divider()
    st.markdown("### Integrations")
    sigaa_ok = bool(os.getenv("UFAL_SIGAA_USERNAME") and os.getenv("UFAL_SIGAA_PASSWORD"))
    smtp_ok = bool(cfg.smtp.get("host"))
    st.write(f"SIGAA credentials: {'configured' if sigaa_ok else 'not configured'}")
    st.write(f"SMTP: {'configured' if smtp_ok else 'not configured'}")


overview, opportunities_tab, professors_tab, semantic_tab, campaigns_tab = st.tabs(
    ["Overview", "Opportunities", "Professors", "Semantic landscape", "Campaigns"]
)


with overview:
    counts = db.counts()
    funded = q(
        "SELECT COUNT(*) AS n, COALESCE(SUM(funded_slots),0) AS slots "
        "FROM opportunities WHERE has_funding"
    )
    funded_n = fmt_int(funded.iloc[0]["n"]) if len(funded) else 0
    funded_slots = fmt_int(funded.iloc[0]["slots"]) if len(funded) else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Professors", counts.get("professors", 0))
    c2.metric("Projects", counts.get("projects", 0))
    c3.metric("Opportunities", counts.get("opportunities", 0))
    c4.metric("Funded opportunities", funded_n)
    c5.metric("Funded slots", funded_slots)

    st.subheader("Opportunity landscape by center")
    by_center = q(
        """
        SELECT
            COALESCE(NULLIF(center,''),'Unknown') AS center,
            COUNT(*) AS opportunities,
            SUM(CASE WHEN has_funding THEN 1 ELSE 0 END) AS funded_opportunities,
            COALESCE(SUM(funded_slots),0) AS funded_slots,
            ROUND(AVG(CASE WHEN has_funding THEN 1.0 ELSE 0.0 END),4) AS funded_fraction
        FROM opportunities
        GROUP BY 1
        ORDER BY opportunities DESC
        """
    )
    if len(by_center):
        st.dataframe(by_center, use_container_width=True, hide_index=True)
        st.bar_chart(by_center.set_index("center")[["opportunities", "funded_opportunities"]])

    left, right = st.columns([1.25, 1])
    with left:
        st.subheader("Opportunity topic factors")
        topics = topic_summary("opportunity") if db.table_exists("entity_topics") else pd.DataFrame()
        if len(topics):
            st.caption(
                "NMF factors are defined from the opportunity corpus. Topic weights are normalized "
                "within each entity; `dominant_entities` counts opportunities for which the factor is #1."
            )
            st.dataframe(topics, use_container_width=True, hide_index=True)
        else:
            st.info("No topic model yet. Run `pulsar analyze rebuild`.")

    with right:
        st.subheader("System metrics")
        gm = q("SELECT metric,value FROM global_metrics ORDER BY metric")
        if len(gm):
            metric_labels = {
                "opportunities_total": "Opportunities",
                "funded_opportunities": "Funded opportunities",
                "funded_slots": "Funded slots",
                "funded_opportunity_fraction": "Fraction of opportunities funded",
                "gini_opportunities_by_professor": "Gini — opportunities by professor",
                "hhi_opportunities_by_professor": "HHI — opportunities by professor",
                "gini_funded_slots_by_professor": "Gini — funded slots by professor",
                "hhi_funded_slots_by_professor": "HHI — funded slots by professor",
            }
            gm["metric"] = gm["metric"].map(metric_labels).fillna(gm["metric"])
            st.dataframe(gm, use_container_width=True, hide_index=True)


with opportunities_tab:
    st.subheader("Opportunity explorer")
    centers = [
        x[0]
        for x in db.query_df(
            "SELECT DISTINCT center FROM opportunities WHERE COALESCE(center,'')<>'' ORDER BY center"
        ).itertuples(index=False, name=None)
    ]

    c1, c2, c3, c4 = st.columns([1, 1.4, 1.2, 1.2])
    funded_only = c1.checkbox("Funded only", value=False)
    center_filter = c2.multiselect("Centers", centers)
    min_fit = c3.slider("Minimum profile fit", 0.0, 1.0, 0.0, 0.01)
    text_filter = c4.text_input("Search title/professor")

    where = ["1=1"]
    params: list[object] = []
    if funded_only:
        where.append("o.has_funding=TRUE")
    if center_filter:
        where.append("o.center IN (" + ",".join("?" for _ in center_filter) + ")")
        params += center_filter
    if min_fit > 0:
        where.append("COALESCE(s.combined_score,0)>=?")
        params.append(min_fit)
    if text_filter.strip():
        where.append(
            "LOWER(COALESCE(o.project_title,'') || ' ' || COALESCE(o.plan_title,'') || ' ' || "
            "COALESCE(o.professor_name,'')) LIKE ?"
        )
        params.append("%" + text_filter.lower().strip() + "%")

    df = q(
        f"""
        SELECT
            o.id_opportunity,
            o.project_code,
            o.project_title,
            o.plan_title,
            o.professor_name,
            o.center,
            o.has_funding,
            o.funded_slots,
            o.edital,
            o.status,
            ROUND(COALESCE(s.combined_score,0),4) AS profile_fit
        FROM opportunities o
        LEFT JOIN analysis_scores s
          ON s.entity_type='opportunity' AND s.entity_id=o.id_opportunity
        WHERE {' AND '.join(where)}
        ORDER BY profile_fit DESC, o.professor_name, o.id_opportunity
        """,
        tuple(params),
    )
    st.caption(f"{len(df)} matching opportunities")
    st.dataframe(df, use_container_width=True, hide_index=True)

    if len(df):
        labels = {
            f"{r.id_opportunity} · {str(r.plan_title or r.project_title or '')[:90]}": r.id_opportunity
            for r in df.itertuples()
        }
        olab = st.selectbox("Inspect opportunity", list(labels))
        oid = labels[olab]
        od = q("SELECT * FROM opportunities WHERE id_opportunity=?", (oid,))
        if len(od):
            row = od.iloc[0].to_dict()
            st.markdown(f"### {row.get('plan_title') or row.get('project_title') or oid}")
            st.caption(
                f"{row.get('project_code') or '—'} · {row.get('professor_name') or '—'} · "
                f"{row.get('center') or '—'} · {row.get('edital') or '—'}"
            )
            a, b, c = st.columns(3)
            a.metric("Funded slots", fmt_int(row.get("funded_slots")))
            b.metric("Funding", "Yes" if row.get("has_funding") else "No")
            app = q("SELECT status,raw_status,applied_at,synced_at FROM applications WHERE id_opportunity=?", (oid,))
            c.metric("Application", str(app.iloc[0]["status"]) if len(app) else "unknown")

            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Introduction / justification**")
                st.write(row.get("introduction_justification") or "—")
                st.markdown("**Objectives**")
                st.write(row.get("objectives") or "—")
            with c2:
                st.markdown("**Methodology**")
                st.write(row.get("methodology") or "—")
                st.markdown("**Acquired skills**")
                st.write(row.get("acquired_skills") or "—")

            opp_topics = q(
                """
                SELECT t.topic_id, t.label AS topic, ROUND(et.weight,4) AS share
                FROM entity_topics et
                JOIN analysis_topics t USING(topic_id)
                WHERE et.entity_type='opportunity' AND et.entity_id=?
                ORDER BY et.weight DESC
                """,
                (oid,),
            )
            if len(opp_topics):
                st.markdown("**Topic profile**")
                st.dataframe(opp_topics, use_container_width=True, hide_index=True)


with professors_tab:
    st.subheader("Professor intelligence")
    centers = [
        x[0]
        for x in db.query_df(
            "SELECT DISTINCT center FROM professors WHERE COALESCE(center,'')<>'' ORDER BY center"
        ).itertuples(index=False, name=None)
    ]
    c1, c2, c3 = st.columns([1.6, 1.2, 1])
    search = c1.text_input("Professor search")
    pcenters = c2.multiselect("Professor centers", centers)
    funded_prof_only = c3.checkbox("Has funded opportunity")

    pwhere = ["1=1"]
    pparams: list[object] = []
    if pcenters:
        pwhere.append("p.center IN (" + ",".join("?" for _ in pcenters) + ")")
        pparams += pcenters
    if funded_prof_only:
        pwhere.append("COALESCE(m.funded_opportunity_count,0)>0")
    if search.strip():
        pwhere.append(
            "LOWER(COALESCE(p.canonical_name,'') || ' ' || COALESCE(p.department,'') || ' ' || "
            "COALESCE(p.email,'')) LIKE ?"
        )
        pparams.append("%" + search.lower().strip() + "%")

    pdf = q(
        f"""
        SELECT
            p.siape,p.canonical_name,p.center,p.department,p.email,
            COALESCE(m.opportunity_count,0) AS opportunities,
            COALESCE(m.funded_opportunity_count,0) AS funded_opportunities,
            COALESCE(m.funded_slots,0) AS funded_slots,
            COALESCE(m.public_project_count,0) AS public_projects,
            COALESCE(m.publication_count,0) AS publications,
            COALESCE(m.funding_agency_count,0) AS funders,
            COALESCE(m.collaborator_count,0) AS collaborators,
            ROUND(COALESCE(m.semantic_fit,0),4) AS profile_fit
        FROM professors p
        LEFT JOIN professor_metrics m USING(siape)
        WHERE {' AND '.join(pwhere)}
        ORDER BY profile_fit DESC,p.canonical_name
        """,
        tuple(pparams),
    )
    st.caption(f"{len(pdf)} matching professors")
    st.dataframe(pdf, use_container_width=True, hide_index=True)

    if len(pdf):
        options = {f"{r.canonical_name} · {r.siape}": r.siape for r in pdf.itertuples()}
        selected_label = st.selectbox("Inspect professor", list(options))
        siape = options[selected_label]
        p = q("SELECT * FROM professors WHERE siape=?", (siape,))
        pm = q("SELECT * FROM professor_metrics WHERE siape=?", (siape,))
        if len(p):
            prow = p.iloc[0].to_dict()
            st.markdown(f"### {prow.get('canonical_name') or siape}")
            st.caption(
                f"SIAPE {siape} · {prow.get('center') or '—'} · {prow.get('department') or '—'} · "
                f"{prow.get('email') or 'no email'}"
            )
            if prow.get("profile_summary"):
                st.write(prow["profile_summary"])
        if len(pm):
            m = pm.iloc[0]
            a, b, c, d, e = st.columns(5)
            a.metric("Opportunities", fmt_int(m.get("opportunity_count")))
            b.metric("Funded", fmt_int(m.get("funded_opportunity_count")))
            c.metric("Funded slots", fmt_int(m.get("funded_slots")))
            d.metric("Publications", fmt_int(m.get("publication_count")))
            e.metric("Collaborators", fmt_int(m.get("collaborator_count")))

        st.subheader("Current opportunities")
        st.dataframe(
            q(
                """
                SELECT id_opportunity,project_title,plan_title,has_funding,funded_slots,edital,status
                FROM opportunities
                WHERE professor_siape=?
                ORDER BY has_funding DESC,funded_slots DESC,id_opportunity
                """,
                (siape,),
            ),
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Topic profile")
        st.dataframe(
            q(
                """
                SELECT t.topic_id,t.label AS topic,ROUND(et.weight,4) AS share
                FROM entity_topics et
                JOIN analysis_topics t USING(topic_id)
                WHERE et.entity_type='professor' AND et.entity_id=?
                ORDER BY et.weight DESC
                """,
                (siape,),
            ),
            use_container_width=True,
            hide_index=True,
        )

        collab = q(
            "SELECT collaborator_name,target_siape,weight FROM collaboration_edges "
            "WHERE source_siape=? ORDER BY weight DESC LIMIT 30",
            (siape,),
        )
        if len(collab):
            st.subheader("Lattes project collaborators")
            st.dataframe(collab, use_container_width=True, hide_index=True)

        public_text = (
            q(
                """
                SELECT page_type,visible_text FROM sigaa_public_pages
                WHERE siape=? AND page_type IN ('pesquisa','producao','extensao','disciplinas')
                ORDER BY page_type
                """,
                (siape,),
            )
            if db.table_exists("sigaa_public_pages")
            else pd.DataFrame()
        )
        if len(public_text):
            st.subheader("Public SIGAA corpus")
            for rr in public_text.itertuples():
                with st.expander(str(rr.page_type).title()):
                    st.text((rr.visible_text or "")[:30000])


with semantic_tab:
    st.subheader("Semantic landscape")
    st.caption(
        "Profile fit is relevance to the semantic profile configured in `config/profile.yaml`; "
        "clusters and coordinates are corpus-native sparse/LSA outputs, not transformer embeddings."
    )
    entity_type = st.radio("Entities", ["opportunity", "professor"], horizontal=True)
    sdf = q(
        """
        SELECT entity_id,combined_score,tfidf_similarity,lsa_similarity,bm25_similarity,cluster_id,x,y
        FROM analysis_scores WHERE entity_type=?
        """,
        (entity_type,),
    )
    if len(sdf):
        if entity_type == "opportunity":
            names = q(
                """
                SELECT
                    id_opportunity AS entity_id,
                    COALESCE(NULLIF(plan_title,''), project_title, id_opportunity) AS display_label,
                    professor_name AS secondary
                FROM opportunities
                """
            )
        else:
            names = q(
                """
                SELECT
                    siape AS entity_id,
                    canonical_name AS display_label,
                    department AS secondary
                FROM professors
                """
            )
        sdf = sdf.merge(names, on="entity_id", how="left")
        sdf["cluster"] = sdf["cluster_id"].fillna(-1).astype(int).astype(str)
        sdf["plot_size"] = sdf["combined_score"].fillna(0).clip(lower=0) + 0.01

        st.scatter_chart(
            sdf,
            x="x",
            y="y",
            color="cluster",
            size="plot_size",
            use_container_width=True,
        )
        st.dataframe(
            sdf[
                [
                    "entity_id","display_label","secondary","combined_score","tfidf_similarity",
                    "lsa_similarity","bm25_similarity","cluster_id","x","y"
                ]
            ].sort_values("combined_score", ascending=False),
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Topic factors")
        ts = topic_summary(entity_type)
        st.dataframe(ts, use_container_width=True, hide_index=True)

        choices = {
            f"{str(r.display_label or r.entity_id)[:100]} · {r.entity_id}": r.entity_id
            for r in sdf.itertuples()
        }
        if choices:
            selected = st.selectbox("Inspect entity topic composition", list(choices))
            entity_id = choices[selected]
            detail = q(
                """
                SELECT t.topic_id,t.label AS topic,ROUND(et.weight,4) AS share
                FROM entity_topics et
                JOIN analysis_topics t USING(topic_id)
                WHERE et.entity_type=? AND et.entity_id=?
                ORDER BY et.weight DESC
                """,
                (entity_type, entity_id),
            )
            st.dataframe(detail, use_container_width=True, hide_index=True)
    else:
        st.info("Run `pulsar analyze rebuild` first.")


with campaigns_tab:
    st.subheader("Campaign studio")
    smtp_ready = bool(cfg.smtp.get("host"))
    if not smtp_ready:
        st.info("SMTP is not configured. Audience selection, draft generation and editing remain fully available; sending is disabled.")

    with st.form("new_campaign"):
        name = st.text_input("Campaign name")
        centers = [
            x[0]
            for x in db.query_df(
                "SELECT DISTINCT center FROM opportunities WHERE COALESCE(center,'')<>'' ORDER BY center"
            ).itertuples(index=False, name=None)
        ]
        ccenters = st.multiselect("Centers", centers)
        funded = st.checkbox("Require funded opportunity", value=True)

        a, b = st.columns(2)
        min_opp = a.slider("Minimum opportunity profile fit", 0.0, 1.0, 0.0, 0.01)
        min_prof = b.slider("Minimum professor profile fit", 0.0, 1.0, 0.0, 0.01)

        semantic_query = st.text_input("Campaign semantic query (optional; sparse TF-IDF/LSA/BM25)")
        min_query_score = st.slider("Minimum campaign-query score", 0.0, 1.0, 0.0, 0.01)
        keyword_text = st.text_input("Required keywords (comma-separated; optional)")

        available_clusters = [
            int(x[0])
            for x in db.query_df(
                "SELECT DISTINCT cluster_id FROM analysis_scores WHERE entity_type='opportunity' ORDER BY cluster_id"
            ).itertuples(index=False, name=None)
        ]
        campaign_clusters = st.multiselect("Semantic clusters (optional)", available_clusters)

        topic_rows = q("SELECT topic_id,label FROM analysis_topics ORDER BY topic_id")
        topic_map = {f"{int(r.topic_id)} · {r.label}": int(r.topic_id) for r in topic_rows.itertuples()}
        selected_topic_labels = st.multiselect("Opportunity topics (optional)", list(topic_map))
        selected_topic_ids = [topic_map[x] for x in selected_topic_labels]
        min_topic_weight = st.slider("Minimum selected-topic share", 0.0, 1.0, 0.15, 0.01)

        with st.expander("Professor intelligence filters"):
            min_public_projects = st.number_input("Minimum public SIGAA projects", min_value=0, value=0, step=1)
            min_publications = st.number_input("Minimum Lattes publication-title records", min_value=0, value=0, step=1)
            min_funders = st.number_input("Minimum distinct project funding agencies", min_value=0, value=0, step=1)

        exclude = st.checkbox("Exclude professors already sent any prior campaign", value=True)
        create = st.form_submit_button("Create drafts")

    if create:
        if not name.strip():
            st.error("Campaign name is required.")
        else:
            cid, n = create_campaign(
                cfg,
                name.strip(),
                funded_only=funded,
                centers=ccenters,
                min_opportunity_fit=min_opp or None,
                min_professor_fit=min_prof or None,
                exclude_already_contacted=exclude,
                keywords=[x.strip() for x in keyword_text.split(",") if x.strip()],
                clusters=campaign_clusters,
                topic_ids=selected_topic_ids,
                min_topic_weight=min_topic_weight,
                semantic_query=semantic_query or None,
                min_query_score=min_query_score or None,
                min_public_projects=min_public_projects or None,
                min_publications=min_publications or None,
                min_funders=min_funders or None,
            )
            st.success(f"Campaign {cid}: {n} recipients/drafts created")
            clear_cache()

    campaigns = q("SELECT campaign_id,name,status,created_at,updated_at FROM campaigns ORDER BY created_at DESC")
    if len(campaigns):
        st.dataframe(campaigns, use_container_width=True, hide_index=True)
        cmap = {f"{r['name']} · {r['campaign_id']}": r["campaign_id"] for _, r in campaigns.iterrows()}
        label = st.selectbox("Open campaign", list(cmap))
        cid = cmap[label]
        rows = campaign_rows(db, cid)
        if rows:
            summary = pd.DataFrame(
                [
                    {k: r.get(k) for k in ["siape", "professor_name", "email", "selection_score", "selected", "status"]}
                    for r in rows
                ]
            )
            st.dataframe(summary, use_container_width=True, hide_index=True)
            choices = {f"{r['professor_name']} · {r['siape']}": r for r in rows}
            rlabel = st.selectbox("Edit recipient draft", list(choices))
            rec = choices[rlabel]

            st.caption("Qualifying evidence")
            evidence = rec.get("qualifying_evidence", [])
            if evidence:
                evidence_df = pd.DataFrame(evidence)
                preferred_cols = [
                    "id_opportunity","project_code","project_title","plan_title","funded_slots",
                    "edital","quota","area","opportunity_fit","campaign_query_score"
                ]
                cols = [c for c in preferred_cols if c in evidence_df.columns]
                st.dataframe(evidence_df[cols], use_container_width=True, hide_index=True)
            else:
                st.write("No qualifying evidence recorded.")

            selected = st.checkbox(
                "Selected for sending",
                value=bool(rec.get("selected")),
                key=f"sel_{cid}_{rec['siape']}",
            )
            subject = st.text_input(
                "Subject",
                value=rec.get("subject", "") or "",
                key=f"sub_{cid}_{rec['siape']}",
            )
            body = st.text_area(
                "Body",
                value=rec.get("body", "") or "",
                height=360,
                key=f"body_{cid}_{rec['siape']}",
            )
            if st.button("Save this draft"):
                update_message(db, cid, rec["siape"], subject=subject, body=body, selected=selected)
                st.success("Saved")
                clear_cache()

            st.divider()
            confirm = st.checkbox(
                "I reviewed this campaign and explicitly authorize sending selected drafts",
                value=False,
                key=f"confirm_{cid}",
                disabled=not smtp_ready,
            )
            if st.button(
                "Send selected drafts",
                type="primary",
                disabled=(not confirm) or (not smtp_ready),
            ):
                try:
                    result = SMTPMailer(cfg).send_campaign(db, cid)
                    st.success(f"Sent {result['sent']}; failed {result['failed']}")
                    clear_cache()
                except Exception as exc:
                    st.error(str(exc))
    else:
        st.caption("No campaigns yet.")
