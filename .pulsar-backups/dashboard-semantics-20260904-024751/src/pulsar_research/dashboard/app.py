from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from pulsar_research.config import AppConfig
from pulsar_research.db import Database
from pulsar_research.outreach.campaigns import campaign_rows, create_campaign, set_recipient_selected, update_message
from pulsar_research.outreach.mailer import SMTPMailer

st.set_page_config(page_title="PULSAR", layout="wide")

@st.cache_resource
def load_state():
    cfg = AppConfig.load(os.getenv("PULSAR_HOME") or Path.cwd())
    db = Database(cfg.paths.database); db.initialize()
    return cfg, db

cfg, db = load_state()

st.title("PULSAR")
st.caption("SIGAA acquisition · opportunity intelligence · professor intelligence · campaigns")

with st.sidebar:
    st.write(f"**Database:** `{cfg.paths.database}`")
    if st.button("Clear dashboard cache"):
        st.cache_data.clear(); st.rerun()

@st.cache_data(ttl=30)
def q(sql: str, params=()):
    return db.query_df(sql, list(params))

overview, opportunities_tab, professors_tab, semantic_tab, campaigns_tab = st.tabs([
    "Overview", "Opportunities", "Professors", "Semantic landscape", "Campaigns"
])

with overview:
    counts = db.counts()
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Professors", counts.get("professors",0))
    c2.metric("Opportunities", counts.get("opportunities",0))
    funded = q("SELECT COUNT(*) n, COALESCE(SUM(funded_slots),0) slots FROM opportunities WHERE has_funding")
    c3.metric("Funded opportunities", int(funded.iloc[0]["n"]) if len(funded) else 0)
    c4.metric("Funded slots", int(funded.iloc[0]["slots"]) if len(funded) else 0)
    gm = q("SELECT metric,value FROM global_metrics ORDER BY metric")
    if len(gm):
        st.subheader("System metrics")
        st.dataframe(gm, use_container_width=True, hide_index=True)
    by_center = q("SELECT center,COUNT(*) opportunities,SUM(CASE WHEN has_funding THEN 1 ELSE 0 END) funded FROM opportunities GROUP BY center ORDER BY opportunities DESC")
    if len(by_center): st.bar_chart(by_center.set_index("center")[["opportunities","funded"]])
    top_topics = q("""SELECT t.topic_id,t.label,ROUND(AVG(et.weight),4) mean_weight,COUNT(*) entities
                      FROM entity_topics et JOIN analysis_topics t USING(topic_id)
                      WHERE et.entity_type='opportunity' GROUP BY t.topic_id,t.label ORDER BY mean_weight DESC""")
    if len(top_topics):
        st.subheader("Opportunity topic factors")
        st.dataframe(top_topics, use_container_width=True, hide_index=True)

with opportunities_tab:
    centers = [x[0] for x in db.query_df("SELECT DISTINCT center FROM opportunities WHERE COALESCE(center,'')<>'' ORDER BY center").itertuples(index=False, name=None)]
    col1,col2,col3 = st.columns(3)
    funded_only = col1.checkbox("Funded only", value=False)
    center_filter = col2.multiselect("Centers", centers)
    min_fit = col3.slider("Minimum semantic fit", 0.0, 1.0, 0.0, 0.01)
    where=["1=1"]; params=[]
    if funded_only: where.append("o.has_funding")
    if center_filter:
        where.append("o.center IN ("+",".join("?" for _ in center_filter)+")"); params += center_filter
    if min_fit > 0: where.append("COALESCE(s.combined_score,0)>=?"); params.append(min_fit)
    df = q(f"""SELECT o.id_opportunity,o.project_code,o.project_title,o.plan_title,o.professor_name,o.center,o.funded_slots,o.edital,o.status,ROUND(COALESCE(s.combined_score,0),4) fit
              FROM opportunities o LEFT JOIN analysis_scores s ON s.entity_type='opportunity' AND s.entity_id=o.id_opportunity
              WHERE {' AND '.join(where)} ORDER BY fit DESC,o.professor_name""", tuple(params))
    st.dataframe(df, use_container_width=True, hide_index=True)
    if len(df):
        labels={f"{r.id_opportunity} · {r.plan_title[:70]}":r.id_opportunity for r in df.itertuples()}
        olab=st.selectbox("Inspect opportunity",list(labels)); oid=labels[olab]
        od=q("SELECT * FROM opportunities WHERE id_opportunity=?",(oid,))
        if len(od):
            row=od.iloc[0].to_dict()
            st.markdown(f"### {row.get('plan_title','')}")
            st.caption(f"{row.get('project_code','')} · {row.get('professor_name','')} · {row.get('edital','')}")
            c1,c2=st.columns(2)
            c1.markdown("**Objectives**"); c1.write(row.get('objectives') or '—')
            c2.markdown("**Methodology**"); c2.write(row.get('methodology') or '—')

with professors_tab:
    search = st.text_input("Professor search")
    df = q("""SELECT p.siape,p.canonical_name,p.department,p.email,
                    COALESCE(m.opportunity_count,0) opportunities,COALESCE(m.funded_opportunity_count,0) funded,
                    COALESCE(m.funded_slots,0) funded_slots,COALESCE(m.public_project_count,0) public_projects,
                    COALESCE(m.publication_count,0) publications,COALESCE(m.funding_agency_count,0) funders,
                    COALESCE(m.collaborator_count,0) collaborators,ROUND(COALESCE(m.semantic_fit,0),4) fit
             FROM professors p LEFT JOIN professor_metrics m USING(siape)
             ORDER BY fit DESC,p.canonical_name""")
    if search:
        mask = df.astype(str).apply(lambda c: c.str.contains(search, case=False, na=False)).any(axis=1); df=df[mask]
    st.dataframe(df, use_container_width=True, hide_index=True)
    if len(df):
        options = {f"{r.canonical_name} · {r.siape}": r.siape for r in df.itertuples()}
        selected_label = st.selectbox("Inspect professor", list(options))
        siape = options[selected_label]
        p = q("SELECT * FROM professors WHERE siape=?", (siape,))
        st.json(p.iloc[0].to_dict() if len(p) else {})
        st.subheader("Current opportunities")
        st.dataframe(q("SELECT id_opportunity,project_title,plan_title,funded_slots,edital,status FROM opportunities WHERE professor_siape=? ORDER BY funded_slots DESC", (siape,)), use_container_width=True, hide_index=True)
        st.subheader("Semantic/topic profile")
        st.dataframe(q("""SELECT t.topic_id,t.label,ROUND(et.weight,4) weight FROM entity_topics et JOIN analysis_topics t USING(topic_id)
                          WHERE et.entity_type='professor' AND et.entity_id=? ORDER BY weight DESC""",(siape,)), use_container_width=True, hide_index=True)
        collab=q("SELECT collaborator_name,target_siape,weight FROM collaboration_edges WHERE source_siape=? ORDER BY weight DESC LIMIT 30",(siape,))
        if len(collab):
            st.subheader("Lattes project collaborators")
            st.dataframe(collab,use_container_width=True,hide_index=True)
        public_text=q("SELECT page_type,visible_text FROM sigaa_public_pages WHERE siape=? AND page_type IN ('pesquisa','producao','extensao','disciplinas') ORDER BY page_type",(siape,)) if db.table_exists('sigaa_public_pages') else pd.DataFrame()
        if len(public_text):
            st.subheader("Public SIGAA corpus")
            for rr in public_text.itertuples():
                with st.expander(str(rr.page_type).title()): st.text(rr.visible_text[:30000])

with semantic_tab:
    entity_type = st.radio("Entities", ["opportunity","professor"], horizontal=True)
    sdf = q("SELECT entity_id,combined_score,cluster_id,x,y FROM analysis_scores WHERE entity_type=?", (entity_type,))
    if len(sdf):
        if entity_type=='opportunity':
            names=q("SELECT id_opportunity entity_id,plan_title label,professor_name secondary FROM opportunities")
        else:
            names=q("SELECT siape entity_id,canonical_name label,department secondary FROM professors")
        sdf=sdf.merge(names,on='entity_id',how='left')
        st.scatter_chart(sdf, x="x", y="y", color="cluster_id", size="combined_score")
        st.dataframe(sdf.sort_values('combined_score',ascending=False),use_container_width=True,hide_index=True)
        topics = q("SELECT topic_id,label,top_terms_json FROM analysis_topics ORDER BY topic_id")
        st.dataframe(topics, use_container_width=True, hide_index=True)
    else:
        st.info("Run `pulsar analyze rebuild` first.")

with campaigns_tab:
    st.subheader("Create campaign")
    with st.form("new_campaign"):
        name = st.text_input("Campaign name")
        centers = [x[0] for x in db.query_df("SELECT DISTINCT center FROM opportunities WHERE COALESCE(center,'')<>'' ORDER BY center").itertuples(index=False, name=None)]
        ccenters = st.multiselect("Centers", centers)
        funded = st.checkbox("Require funded opportunity", value=True)
        min_opp = st.slider("Minimum opportunity fit", 0.0, 1.0, 0.0, 0.01)
        min_prof = st.slider("Minimum professor fit", 0.0, 1.0, 0.0, 0.01)
        semantic_query = st.text_input("Campaign semantic query (optional; sparse TF-IDF/LSA/BM25)")
        min_query_score = st.slider("Minimum campaign-query score", 0.0, 1.0, 0.0, 0.01)
        keyword_text = st.text_input("Required keywords (comma-separated; optional)")
        available_clusters = [int(x[0]) for x in db.query_df("SELECT DISTINCT cluster_id FROM analysis_scores WHERE entity_type='opportunity' ORDER BY cluster_id").itertuples(index=False, name=None)]
        campaign_clusters = st.multiselect("Semantic clusters (optional)", available_clusters)
        with st.expander("Professor intelligence filters"):
            min_public_projects = st.number_input("Minimum public SIGAA projects", min_value=0, value=0, step=1)
            min_publications = st.number_input("Minimum Lattes publication-title records", min_value=0, value=0, step=1)
            min_funders = st.number_input("Minimum distinct project funding agencies", min_value=0, value=0, step=1)
        exclude = st.checkbox("Exclude professors already sent any prior campaign", value=True)
        create = st.form_submit_button("Create drafts")
    if create and name.strip():
        cid,n = create_campaign(cfg,name.strip(),funded_only=funded,centers=ccenters,min_opportunity_fit=min_opp or None,min_professor_fit=min_prof or None,exclude_already_contacted=exclude,keywords=[x.strip() for x in keyword_text.split(",") if x.strip()],clusters=campaign_clusters,semantic_query=semantic_query or None,min_query_score=min_query_score or None,min_public_projects=min_public_projects or None,min_publications=min_publications or None,min_funders=min_funders or None)
        st.success(f"Campaign {cid}: {n} recipients/drafts created")
        st.cache_data.clear()

    campaigns = q("SELECT campaign_id,name,status,created_at,updated_at FROM campaigns ORDER BY created_at DESC")
    if len(campaigns):
        st.dataframe(campaigns, use_container_width=True, hide_index=True)
        cmap={f"{r['name']} · {r['campaign_id']}":r['campaign_id'] for _,r in campaigns.iterrows()}
        label=st.selectbox("Open campaign",list(cmap)); cid=cmap[label]
        rows=campaign_rows(db,cid)
        if rows:
            summary=pd.DataFrame([{k:r.get(k) for k in ["siape","professor_name","email","selection_score","selected","status"]} for r in rows])
            st.dataframe(summary,use_container_width=True,hide_index=True)
            choices={f"{r['professor_name']} · {r['siape']}":r for r in rows}; rlabel=st.selectbox("Edit recipient draft",list(choices)); rec=choices[rlabel]
            evidence=rec.get("qualifying_evidence",[])
            st.caption("Qualifying evidence")
            st.json(evidence)
            selected=st.checkbox("Selected for sending",value=bool(rec.get("selected")),key=f"sel_{cid}_{rec['siape']}")
            subject=st.text_input("Subject",value=rec.get("subject","") or "",key=f"sub_{cid}_{rec['siape']}")
            body=st.text_area("Body",value=rec.get("body","") or "",height=360,key=f"body_{cid}_{rec['siape']}")
            if st.button("Save this draft"):
                update_message(db,cid,rec['siape'],subject=subject,body=body,selected=selected); st.success("Saved"); st.cache_data.clear()
            st.divider()
            confirm=st.checkbox("I reviewed this campaign and explicitly authorize sending selected drafts",value=False,key=f"confirm_{cid}")
            if st.button("Send selected drafts",type="primary",disabled=not confirm):
                try:
                    result=SMTPMailer(cfg).send_campaign(db,cid); st.success(f"Sent {result['sent']}; failed {result['failed']}")
                except Exception as exc:
                    st.error(str(exc))
