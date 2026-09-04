"""PULSAR operator console.

Design intent: analytical density with navigability. Every number that is a
*ranking* says so, every map states its distortion, and every recommendation can
be expanded into the exact record that produced it. The console is a place to
decide something, not a dataframe browser.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from pulsar_research.config import AppConfig
from pulsar_research.dashboard.queries import Repository, lorenz
from pulsar_research.db import Database

st.set_page_config(page_title="PULSAR · Research Intelligence", page_icon="✦",
                   layout="wide", initial_sidebar_state="expanded")


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


@st.cache_resource
def boot():
    config = AppConfig.load(os.getenv("PULSAR_HOME") or Path.cwd())
    db = Database(config.paths.database)
    db.initialize()
    return config, db, Repository(db)


cfg, db, repo = boot()


@st.cache_data(ttl=60, show_spinner=False)
def cached(name: str, *args):
    return getattr(repo, name)(*args)


def refresh() -> None:
    st.cache_data.clear()


CSS = """
<style>
:root{--p-bg:#07111f;--p-panel:#0d1a2c;--p-border:#233650;--p-text:#e8eef7;
      --p-muted:#8ea1b8;--p-cyan:#42c7ff;--p-green:#45d6a8;--p-amber:#f2c66d;--p-red:#ff7d86;}
.stApp{background:radial-gradient(circle at 20% 0%,#0d2239 0%,var(--p-bg) 33%,#060d18 100%);color:var(--p-text);}
[data-testid="stSidebar"]{background:#091525;border-right:1px solid var(--p-border);}
.block-container{max-width:1560px;padding-top:1rem;padding-bottom:3rem;}
h1,h2,h3{letter-spacing:-.025em;} h1{font-size:1.9rem!important;} h2{font-size:1.3rem!important;}
p,label,.stCaption{color:var(--p-muted);}
.pulsar-head{border:1px solid var(--p-border);background:linear-gradient(135deg,rgba(16,31,52,.92),rgba(9,21,37,.85));
  padding:16px 22px;border-radius:14px;margin-bottom:14px;}
.pulsar-brand{font-size:26px;font-weight:800;color:#fff;letter-spacing:-.04em;}
.pulsar-sub{margin-top:2px;font-size:13px;color:var(--p-muted);}
.pulsar-badges{display:flex;gap:8px;flex-wrap:wrap;margin-top:11px;}
.pulsar-badge{border:1px solid var(--p-border);border-radius:999px;padding:4px 10px;font-size:11px;color:#b8c8da;background:#0a1728;}
.pulsar-badge.ok{color:var(--p-green);border-color:rgba(69,214,168,.35);}
.pulsar-badge.warn{color:var(--p-amber);border-color:rgba(242,198,109,.35);}
.pulsar-badge.bad{color:var(--p-red);border-color:rgba(255,125,134,.4);}
[data-testid="stMetric"]{background:linear-gradient(160deg,rgba(16,31,52,.95),rgba(10,23,40,.92));
  border:1px solid var(--p-border);border-radius:11px;padding:12px 14px;}
[data-testid="stMetricLabel"]{color:var(--p-muted);}
[data-testid="stMetricValue"]{color:var(--p-cyan);font-weight:750;}
.pulsar-card{background:rgba(13,26,44,.92);border:1px solid var(--p-border);border-radius:12px;
  padding:12px 15px;margin:2px 0 10px 0;}
.pulsar-title{color:#edf4fc;font-size:14px;font-weight:700;}
.pulsar-note{color:var(--p-muted);font-size:12px;margin-top:3px;line-height:1.45;}
[data-testid="stDataFrame"]{border:1px solid var(--p-border);border-radius:10px;overflow:hidden;}
[data-testid="stExpander"]{border:1px solid var(--p-border);background:rgba(13,26,44,.72);border-radius:10px;}
[data-testid="stForm"]{border:1px solid var(--p-border);background:rgba(13,26,44,.6);border-radius:12px;padding:14px;}
hr{border-color:var(--p-border)!important;}
.stButton>button{border-radius:8px;border:1px solid #2c4564;background:#10223a;color:#dce9f7;}
.stButton>button:hover{border-color:var(--p-cyan);color:#fff;}
div[data-baseweb="select"]>div,input,textarea{background-color:#091626!important;}
.ev{border-left:2px solid #2c4564;padding:2px 0 2px 10px;margin:6px 0;font-size:12.5px;color:#cfdcea;}
.ev b{color:#fff;} .ev span{color:var(--p-muted);}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


def header(title: str, subtitle: str) -> None:
    space_id, run_id = repo.space_id, repo.run_id
    sigaa_ok = bool(os.getenv(cfg.sigaa.get("username_env", "UFAL_SIGAA_USERNAME")))
    smtp_ok = bool(cfg.smtp.get("host"))
    fresh = st.session_state.get("_fresh")
    badges = [
        f'<span class="pulsar-badge">{space_id or "no semantic space"}</span>',
        f'<span class="pulsar-badge">{run_id or "no profile run"}</span>',
        f'<span class="pulsar-badge {"ok" if sigaa_ok else "warn"}">SIGAA {"ready" if sigaa_ok else "offline"}</span>',
        f'<span class="pulsar-badge {"ok" if smtp_ok else "warn"}">SMTP {"ready" if smtp_ok else "offline"}</span>',
    ]
    if fresh is False:
        badges.append('<span class="pulsar-badge bad">semantics STALE</span>')
    st.markdown(
        f'<div class="pulsar-head"><div class="pulsar-brand">{title}</div>'
        f'<div class="pulsar-sub">{subtitle}</div>'
        f'<div class="pulsar-badges">{"".join(badges)}</div></div>',
        unsafe_allow_html=True,
    )


def card(title: str, note: str | None = None) -> None:
    if note:
        st.markdown(f'<div class="pulsar-card"><div class="pulsar-title">{title}</div>'
                    f'<div class="pulsar-note">{note}</div></div>', unsafe_allow_html=True)
    else:
        st.markdown(f"### {title}")


AXIS = {"labelColor": "#8ea1b8", "titleColor": "#9fb1c6", "gridColor": "#1a2b42",
        "domainColor": "#30455f", "tickColor": "#30455f"}


def vega(data: pd.DataFrame, spec: dict, *, height: int = 340) -> None:
    if data is None or not len(data):
        st.info("No data for this view.")
        return
    spec = dict(spec)
    spec.setdefault("height", height)
    spec.setdefault("background", "transparent")
    spec.setdefault("config", {})
    spec["config"].setdefault("axis", AXIS)
    spec["config"].setdefault("legend", {"labelColor": "#a8bad0", "titleColor": "#c7d5e5"})
    spec["config"].setdefault("view", {"stroke": "transparent"})
    st.vega_lite_chart(data, spec, use_container_width=True)


def pct(value) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "—"


def has_semantics() -> bool:
    if not repo.space_id:
        st.warning("No semantic space yet. Run `pulsar semantics build` in the project root.")
        return False
    if not repo.run_id:
        st.warning("No profile run yet. Run `pulsar semantics profile`.")
        return False
    return True


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

counts = db.counts()
with st.sidebar:
    st.markdown("## ✦ PULSAR")
    st.caption("Local research intelligence")
    page = st.radio("Navigate",
                    ["Overview", "Opportunities", "Professors", "Landscape",
                     "Semantic Engine", "Campaigns"],
                    label_visibility="collapsed")
    st.divider()
    st.markdown("**Corpus**")
    st.caption(f"{counts.get('professors',0)} professors · {counts.get('opportunities',0)} opportunities\n\n"
               f"{counts.get('embedding_cache',0)} cached embeddings")
    if repo.space_id:
        st.caption(f"space `{repo.space_id}`")
    st.divider()
    if st.button("Refresh data", use_container_width=True):
        refresh()
        st.rerun()
    st.caption(str(cfg.paths.database))


# ---------------------------------------------------------------------------
# OVERVIEW
# ---------------------------------------------------------------------------

if page == "Overview":
    header("PULSAR · Research Intelligence",
           "The UFAL research landscape, ranked opportunities and professor portfolios")
    if not has_semantics():
        st.stop()

    gm = cached("global_metrics")
    opp = cached("opportunities")
    projects = cached("projects")
    fidelity = cached("map_diagnostics")

    m = st.columns(6)
    m[0].metric("Projects", int(gm.get("projects_total", len(projects))))
    m[1].metric("Work plans", int(gm.get("opportunities_total", len(opp))))
    m[2].metric("Professors", int(gm.get("professors_total", 0)))
    m[3].metric("Funded plans", int(gm.get("funded_opportunities", 0)))
    m[4].metric("Funded slots", int(gm.get("funded_slots", 0)))
    m[5].metric("Portfolio items", int(gm.get("atoms_total", 0)))

    left, right = st.columns(2)
    with left:
        card("Research landscape",
             f"Project-level map, {fidelity.get('method','?')}. "
             f"Kruskal stress {fidelity.get('stress',float('nan')):.3f} · "
             f"Spearman distance correlation {fidelity.get('spearman',float('nan')):.3f}. "
             "Read position as neighbourhood, not as a distance ruler.")
        land = projects.dropna(subset=["x", "y"]).copy()
        if len(land):
            land["topic"] = land.get("topic_label", pd.Series("—", index=land.index)).fillna("—")
            land["size"] = 40 + land.get("fused_pct", pd.Series(0.0, index=land.index)).fillna(0) * 1.6
            vega(land, {
                "mark": {"type": "circle", "opacity": .8, "stroke": "#07111f", "strokeWidth": .8},
                "encoding": {
                    "x": {"field": "x", "type": "quantitative", "title": "Semantic axis 1"},
                    "y": {"field": "y", "type": "quantitative", "title": "Semantic axis 2"},
                    "color": {"field": "topic", "type": "nominal", "title": "Dominant field",
                              "legend": {"columns": 1, "labelLimit": 220}},
                    "size": {"field": "size", "type": "quantitative", "legend": None,
                             "scale": {"range": [40, 260]}},
                    "tooltip": [{"field": "project_title", "title": "Project"},
                                {"field": "professor_name", "title": "Professor"},
                                {"field": "topic", "title": "Field"},
                                {"field": "funded_slots", "title": "Funded slots"},
                                {"field": "fused_pct", "title": "Profile percentile", "format": ".1f"}],
                }}, height=430)
    with right:
        card("Strategic opportunity matrix",
             "Profile rank percentile against funded slots. Top-right is where an "
             "actionable, well-matched opportunity lives; percentile is a within-corpus rank.")
        matrix = opp.dropna(subset=["rank_pct"]).copy()
        if len(matrix):
            matrix["funding"] = np.where(matrix["has_funding"], "Funded", "Unfunded")
            vega(matrix, {
                "mark": {"type": "circle", "opacity": .72},
                "encoding": {
                    "x": {"field": "rank_pct", "type": "quantitative",
                          "title": "Profile rank percentile", "scale": {"domain": [0, 100]}},
                    "y": {"field": "funded_slots", "type": "quantitative", "title": "Funded slots"},
                    "color": {"field": "center", "type": "nominal", "title": "Center"},
                    "shape": {"field": "funding", "type": "nominal", "title": None},
                    "size": {"value": 90},
                    "tooltip": [{"field": "plan_title", "title": "Work plan"},
                                {"field": "professor_name", "title": "Professor"},
                                {"field": "rank_pct", "title": "Percentile", "format": ".1f"},
                                {"field": "funded_slots", "title": "Slots"},
                                {"field": "application_status", "title": "Application"}],
                }}, height=430)

    left, right = st.columns(2)
    with left:
        card("Methodology landscape",
             "Which techniques the corpus actually uses, from the deterministic skill "
             "taxonomy. Generic scholarly competencies are excluded — every plan claims them.")
        sk = cached("skill_landscape", "opportunity")
        sk = sk[~sk["generic"]].head(18) if len(sk) else sk
        vega(sk.sort_values("entities"), {
            "mark": {"type": "bar", "cornerRadiusEnd": 4},
            "encoding": {
                "y": {"field": "label", "type": "nominal", "sort": "-x", "title": None,
                      "axis": {"labelLimit": 260}},
                "x": {"field": "entities", "type": "quantitative", "title": "Work plans"},
                "color": {"field": "category", "type": "nominal", "title": "Category"},
                "tooltip": [{"field": "label"}, {"field": "category"}, {"field": "entities"}],
            }}, height=430)
    with right:
        card("Concentration of opportunity supply",
             f"Gini of funded slots per professor: {gm.get('gini_funded_slots_by_professor',0):.3f}. "
             f"Top 10 professors hold {gm.get('top10_share_funded_slots',0)*100:.0f}% of all slots.")
        supply = opp.groupby("professor_siape", dropna=True).agg(
            opportunities=("id_opportunity", "count"), slots=("funded_slots", "sum")).reset_index()
        curves = []
        for column, name in (("opportunities", "Work plans"), ("slots", "Funded slots")):
            frame = lorenz(supply[column]) if len(supply) else lorenz([])
            frame["series"] = name
            curves.append(frame)
        eq = pd.DataFrame({"population": np.linspace(0, 1, 101), "share": np.linspace(0, 1, 101),
                           "series": "Perfect equality"})
        vega(pd.concat(curves + [eq], ignore_index=True), {
            "mark": {"type": "line", "strokeWidth": 2},
            "encoding": {
                "x": {"field": "population", "type": "quantitative",
                      "title": "Cumulative share of professors", "axis": {"format": "%"}},
                "y": {"field": "share", "type": "quantitative", "title": "Cumulative share",
                      "axis": {"format": "%"}},
                "color": {"field": "series", "type": "nominal", "title": None,
                          "scale": {"domain": ["Work plans", "Funded slots", "Perfect equality"],
                                    "range": ["#42c7ff", "#45d6a8", "#60748c"]}},
            }}, height=430)

    card("Priority opportunities",
         "Highest-ranked work plans in the current profile run. Percentiles are relative to this corpus.")
    top = opp.sort_values("rank_pct", ascending=False).head(40)
    st.dataframe(
        top[["rank_pct", "id_opportunity", "plan_title", "professor_name", "center",
             "funded_slots", "application_status"]],
        use_container_width=True, hide_index=True, height=470,
        column_config={"rank_pct": st.column_config.ProgressColumn("Profile percentile", min_value=0,
                                                                   max_value=100, format="%.1f"),
                       "id_opportunity": "ID", "plan_title": "Work plan",
                       "professor_name": "Professor", "funded_slots": "Slots",
                       "application_status": "Application"})


# ---------------------------------------------------------------------------
# OPPORTUNITIES
# ---------------------------------------------------------------------------

elif page == "Opportunities":
    header("Opportunity Explorer", "Filter, rank, compare and inspect every work plan")
    if not has_semantics():
        st.stop()

    opp = cached("opportunities")
    skills_all = cached("skills", "opportunity")

    f = st.columns([1, 1.2, 1.2, 1.4, 1.2, 1.6])
    funded_only = f[0].checkbox("Funded only")
    centers = f[1].multiselect("Centers", sorted(opp["center"].dropna().unique().tolist()))
    statuses = f[2].multiselect("Application", sorted(opp["application_status"].dropna().unique().tolist()))
    min_pct = f[3].slider("Min profile percentile", 0, 100, 0, 5)
    skill_opts = sorted(skills_all.loc[~skills_all["generic"], "label"].unique().tolist()) if len(skills_all) else []
    want_skills = f[4].multiselect("Requires skill", skill_opts)
    text = f[5].text_input("Search title / professor / project")

    mask = pd.Series(True, index=opp.index)
    if funded_only:
        mask &= opp["has_funding"].fillna(False)
    if centers:
        mask &= opp["center"].isin(centers)
    if statuses:
        mask &= opp["application_status"].isin(statuses)
    if min_pct:
        mask &= opp["rank_pct"].fillna(0) >= min_pct
    if want_skills and len(skills_all):
        ok = set(skills_all.loc[skills_all["label"].isin(want_skills), "entity_id"])
        mask &= opp["id_opportunity"].isin(ok)
    if text.strip():
        needle = text.lower().strip()
        hay = (opp["project_title"].fillna("") + " " + opp["plan_title"].fillna("") + " " +
               opp["professor_name"].fillna("") + " " + opp["project_code"].fillna("")).str.lower()
        mask &= hay.str.contains(needle, regex=False)
    view = opp[mask].sort_values(["rank_pct", "funded_slots"], ascending=[False, False])

    k = st.columns(4)
    k[0].metric("Matching", len(view))
    k[1].metric("Funded", int(view["has_funding"].fillna(False).sum()) if len(view) else 0)
    k[2].metric("Funded slots", int(pd.to_numeric(view["funded_slots"], errors="coerce").fillna(0).sum()) if len(view) else 0)
    k[3].metric("Top decile", int((view["rank_pct"].fillna(0) >= 90).sum()) if len(view) else 0)

    st.dataframe(
        view[["rank_pct", "id_opportunity", "plan_title", "professor_name", "center",
              "funded_slots", "application_status"]],
        use_container_width=True, hide_index=True, height=380,
        column_config={"rank_pct": st.column_config.ProgressColumn("Percentile", min_value=0,
                                                                   max_value=100, format="%.1f"),
                       "id_opportunity": "ID", "plan_title": "Work plan", "professor_name": "Professor"})

    if len(view):
        options = {f"p{0 if pd.isna(r.rank_pct) else int(r.rank_pct):02d} · {r.id_opportunity} · "
                   f"{str(r.plan_title or r.project_title or '')[:90]}": r.id_opportunity
                   for r in view.itertuples()}
        chosen = st.selectbox("Inspect work plan", list(options))
        oid = options[chosen]
        record = db.query_df("SELECT * FROM opportunities WHERE id_opportunity=?", [oid])
        if len(record):
            row = record.iloc[0].to_dict()
            srow = view[view["id_opportunity"] == oid].iloc[0]
            st.markdown(f"## {row.get('plan_title') or row.get('project_title') or oid}")
            st.caption(f"{row.get('project_code') or '—'} · {row.get('professor_name') or '—'} · "
                       f"{row.get('center') or '—'} · edital {row.get('edital') or '—'} · "
                       f"{row.get('area') or '—'}")
            kk = st.columns(5)
            kk[0].metric("Overall percentile", pct(srow.get("rank_pct")))
            kk[1].metric("Domain", pct(srow.get("domain_fused_pct")))
            kk[2].metric("Methods", pct(srow.get("methods_fused_pct")))
            kk[3].metric("Skill match", pct(srow.get("skills_skill_match_pct")))
            kk[4].metric("Funded slots", int(row.get("funded_slots") or 0))

            tabs = st.tabs(["Research brief", "Why this ranking", "Structure", "Full source"])
            with tabs[0]:
                a, b = st.columns(2)
                with a:
                    card("Objectives"); st.write(row.get("objectives") or "—")
                    card("Acquired skills"); st.write(row.get("acquired_skills") or "—")
                with b:
                    card("Methodology"); st.write(row.get("methodology") or "—")
            with tabs[1]:
                card("Retrieval channels",
                     "Channels answer different questions. Lexical channels reward literal "
                     "vocabulary overlap; latent and neural channels reward thematic adjacency "
                     "even when the wording differs. They are never averaged into one number.")
                chan = repo.entity_scores("opportunity", oid)
                if len(chan):
                    st.dataframe(chan.pivot_table(index="facet", columns="channel", values="percentile",
                                                  aggfunc="first").reset_index(),
                                 use_container_width=True, hide_index=True)
                    vega(chan, {
                        "mark": {"type": "bar"},
                        "encoding": {
                            "y": {"field": "channel", "type": "nominal", "title": None},
                            "x": {"field": "percentile", "type": "quantitative", "title": "Percentile",
                                  "scale": {"domain": [0, 100]}},
                            "color": {"field": "facet", "type": "nominal", "title": "Facet"},
                            "yOffset": {"field": "facet"},
                        }}, height=300)
                sk = cached("skills", "opportunity", oid)
                if len(sk):
                    card("Extracted skills", "Deterministic gazetteer labels with occurrence counts.")
                    st.dataframe(sk[["label", "category", "generic", "mentions"]],
                                 use_container_width=True, hide_index=True)
            with tabs[2]:
                for facet in ("domain", "methods"):
                    card(f"{facet.title()} topics")
                    td = repo.entity_topics("opportunity", oid, facet)
                    if len(td):
                        vega(td.head(8), {
                            "mark": {"type": "bar", "cornerRadiusEnd": 3, "color": "#42c7ff"},
                            "encoding": {
                                "y": {"field": "label", "type": "nominal", "sort": "-x", "title": None,
                                      "axis": {"labelLimit": 300}},
                                "x": {"field": "share", "type": "quantitative", "title": "Share"},
                            }}, height=230)
            with tabs[3]:
                card("Introduction / justification"); st.write(row.get("introduction_justification") or "—")
                card("References"); st.write(row.get("references_text") or "—")


# ---------------------------------------------------------------------------
# PROFESSORS
# ---------------------------------------------------------------------------

elif page == "Professors":
    header("Professor Intelligence",
           "Research portfolios, current supervision capacity and the evidence behind every rank")
    if not has_semantics():
        st.stop()

    profs = cached("professors")
    scope = st.radio("Rank by", ["current", "trajectory"], horizontal=True,
                     format_func=lambda s: "Current opportunity fit" if s == "current"
                     else "Research trajectory fit")
    st.caption("**Current** uses only open work plans, active SIGAA projects and active research "
               "lines — who can supervise this now. **Trajectory** uses the whole publication and "
               "project history — who thinks about the same problems.")

    rank_col = f"{scope}_fused_pct"
    if rank_col not in profs.columns:
        rank_col = f"{scope}_latent_pct"
    profs = profs.copy()
    profs["rank_pct"] = profs.get(rank_col, pd.Series(np.nan, index=profs.index))

    f = st.columns([1.8, 1.2, 1.1, 1.1])
    search = f[0].text_input("Search professor / department")
    centers = f[1].multiselect("Centers", sorted([c for c in profs["center"].dropna().unique() if str(c).strip()]))
    funded_only = f[2].checkbox("Has funded opportunity")
    min_pct = f[3].slider("Min percentile", 0, 100, 0, 5)

    mask = pd.Series(True, index=profs.index)
    if search.strip():
        needle = search.lower().strip()
        hay = (profs["canonical_name"].fillna("") + " " + profs["department"].fillna("")).str.lower()
        mask &= hay.str.contains(needle, regex=False)
    if centers:
        mask &= profs["center"].isin(centers)
    if funded_only:
        mask &= profs["funded_opportunities"] > 0
    if min_pct:
        mask &= profs["rank_pct"].fillna(0) >= min_pct
    view = profs[mask].sort_values(["rank_pct", "funded_slots"], ascending=[False, False])

    st.dataframe(
        view[["rank_pct", "siape", "canonical_name", "department", "opportunities",
              "funded_opportunities", "funded_slots", "current_projects", "publications",
              "portfolio_items", "latest_evidence_year"]],
        use_container_width=True, hide_index=True, height=400,
        column_config={"rank_pct": st.column_config.ProgressColumn("Percentile", min_value=0,
                                                                   max_value=100, format="%.1f"),
                       "canonical_name": "Professor", "funded_opportunities": "Funded opps",
                       "funded_slots": "Slots", "current_projects": "Active projects",
                       "portfolio_items": "Portfolio", "latest_evidence_year": "Latest"})

    if len(view):
        options = {f"p{0 if pd.isna(r.rank_pct) else int(r.rank_pct):02d} · {r.canonical_name} · {r.siape}": r.siape
                   for r in view.itertuples()}
        chosen = st.selectbox("Inspect professor", list(options))
        siape = options[chosen]
        row = view[view["siape"] == siape].iloc[0]
        st.markdown(f"## {row['canonical_name']}")
        st.caption(f"SIAPE {siape} · {row.get('center') or '—'} · {row.get('department') or '—'} · "
                   f"{row.get('email') or 'no email on file'}")
        if row.get("profile_summary"):
            with st.expander("Lattes summary"):
                st.write(row["profile_summary"])

        kk = st.columns(6)
        kk[0].metric("Current fit", pct(row.get("current_fused_pct")))
        kk[1].metric("Trajectory fit", pct(row.get("trajectory_fused_pct")))
        kk[2].metric("Funded slots", int(row.get("funded_slots") or 0))
        kk[3].metric("Active projects", int(row.get("current_projects") or 0))
        kk[4].metric("Publications", int(row.get("publications") or 0))
        kk[5].metric("Portfolio items", int(row.get("portfolio_items") or 0))

        tabs = st.tabs(["Why this professor", "Current opportunities", "Skills & topics", "Network & corpus"])
        with tabs[0]:
            evidence = cached("evidence", siape)
            for label, key, note in (
                ("Current supervision capacity", "current",
                 "Open work plans, active SIGAA projects and active research lines only."),
                ("Research trajectory", "trajectory",
                 "The whole portfolio. Weighted by specificity, recency and how rare the "
                 "vocabulary is in this corpus, so a broad area like “Medicina” cannot become "
                 "the reason someone was recommended."),
            ):
                card(label, note)
                sub = evidence[evidence["scope"] == key] if len(evidence) else evidence
                if not len(sub):
                    st.caption("No evidence in this scope.")
                    continue
                for r in sub.itertuples():
                    payload = r.payload or {}
                    year = "" if pd.isna(r.year) else f" · {int(r.year)}"
                    st.markdown(
                        f'<div class="ev"><b>{r.label}</b><br>'
                        f'<span>{r.kind}{year} · match {r.score:.3f} · evidence weight {r.weight:.3f} '
                        f'· specificity {payload.get("specificity","?")} '
                        f'· recency {payload.get("recency","?")}</span></div>',
                        unsafe_allow_html=True)
        with tabs[1]:
            st.dataframe(db.query_df(
                "SELECT id_opportunity, plan_title, project_title, has_funding, funded_slots, edital, status "
                "FROM opportunities WHERE professor_siape=? ORDER BY has_funding DESC, funded_slots DESC",
                [siape]), use_container_width=True, hide_index=True)
        with tabs[2]:
            a, b = st.columns(2)
            with a:
                card("Skills across the portfolio")
                sk = cached("skills", "professor", siape)
                if len(sk):
                    st.dataframe(sk[["label", "category", "generic", "mentions"]],
                                 use_container_width=True, hide_index=True, height=380)
            with b:
                facet = st.radio("Topic facet", ["domain", "methods"], horizontal=True, key="prof_facet")
                td = repo.entity_topics("professor", siape, facet)
                if not len(td):
                    st.caption("Professor topic shares are derived from their work plans; "
                               "this professor has none in the current corpus.")
                else:
                    vega(td.head(10), {
                        "mark": {"type": "bar", "cornerRadiusEnd": 3, "color": "#45d6a8"},
                        "encoding": {
                            "y": {"field": "label", "type": "nominal", "sort": "-x", "title": None,
                                  "axis": {"labelLimit": 300}},
                            "x": {"field": "share", "type": "quantitative", "title": "Share"},
                        }}, height=380)
        with tabs[3]:
            collab = cached("collaborators", siape)
            if len(collab):
                card("Lattes project collaborators")
                st.dataframe(collab, use_container_width=True, hide_index=True, height=260)
            for r in cached("public_pages", siape).itertuples():
                with st.expander(f"Public SIGAA · {str(r.page_type).title()}"):
                    st.text((r.visible_text or "")[:30000])


# ---------------------------------------------------------------------------
# LANDSCAPE
# ---------------------------------------------------------------------------

elif page == "Landscape":
    header("Research Landscape", "What this ecosystem studies, how it is organized, and where the gaps are")
    if not has_semantics():
        st.stop()

    facet = st.radio("Facet", ["domain", "methods"], horizontal=True)
    projects = cached("projects")
    topics = cached("topics", facet)
    composition = cached("topic_composition", facet, "project")

    left, right = st.columns([1.15, 1])
    with left:
        card("Topic hierarchy",
             "NMF factors over project-level documents. These are non-negative bases over terms "
             "that happen to be interpretable — a navigation aid, not an ontology of science.")
        if len(topics):
            tree = topics.copy()
            tree["indent"] = tree["depth"].map(lambda d: "    " * int(d) + ("└ " if int(d) else ""))
            tree["topic"] = tree["indent"] + tree["label"]
            merged = tree.merge(composition[["topic_id", "dominant"]], on="topic_id", how="left")
            st.dataframe(merged[["topic_id", "topic", "depth", "dominant"]].fillna({"dominant": 0}),
                         use_container_width=True, hide_index=True, height=420,
                         column_config={"topic": "Topic", "dominant": "Projects"})
            selected = st.selectbox("Topic terms", topics["topic_id"].tolist(),
                                    format_func=lambda t: f"{t} · {topics.loc[topics.topic_id==t,'label'].iloc[0]}")
            terms = json.loads(topics.loc[topics.topic_id == selected, "terms_json"].iloc[0] or "[]")
            st.code(", ".join(terms), language=None)
    with right:
        card("Field composition", "How many projects each root factor dominates.")
        root = composition[composition["depth"] == 0] if len(composition) else composition
        vega(root, {
            "mark": {"type": "bar", "cornerRadiusEnd": 4, "color": "#42c7ff"},
            "encoding": {
                "y": {"field": "label", "type": "nominal", "sort": "-x", "title": None,
                      "axis": {"labelLimit": 300}},
                "x": {"field": "dominant", "type": "quantitative", "title": "Projects"},
                "tooltip": [{"field": "label"}, {"field": "dominant"},
                            {"field": "mean_share", "format": ".3f"}],
            }}, height=420)

    fidelity = cached("map_diagnostics")
    card("Semantic map",
         f"{fidelity.get('method','?')} · stress {fidelity.get('stress',float('nan')):.3f} · "
         f"Pearson {fidelity.get('pearson',float('nan')):.3f} · "
         f"Spearman {fidelity.get('spearman',float('nan')):.3f} over "
         f"{int(fidelity.get('points',0))} projects. Stress below ~0.15 is a faithful map; "
         "higher means the plot is a sketch of neighbourhoods, not a metric space.")
    land = projects.dropna(subset=["x", "y"]).copy()
    if len(land):
        land["topic"] = land.get("topic_label", pd.Series("—", index=land.index)).fillna("—")
        vega(land, {
            "mark": {"type": "circle", "opacity": .82, "size": 130, "stroke": "#07111f"},
            "encoding": {
                "x": {"field": "x", "type": "quantitative", "title": "Axis 1"},
                "y": {"field": "y", "type": "quantitative", "title": "Axis 2"},
                "color": {"field": "topic", "type": "nominal", "title": "Field",
                          "legend": {"columns": 1, "labelLimit": 240}},
                "tooltip": [{"field": "project_title", "title": "Project"},
                            {"field": "professor_name", "title": "Professor"},
                            {"field": "work_plans", "title": "Work plans"},
                            {"field": "funded_slots", "title": "Funded slots"}],
            }}, height=560)

    card("Method / skill supply", "Techniques available across the corpus, by category.")
    sk = cached("skill_landscape", "opportunity")
    if len(sk):
        st.dataframe(sk, use_container_width=True, hide_index=True, height=340)


# ---------------------------------------------------------------------------
# SEMANTIC ENGINE
# ---------------------------------------------------------------------------

elif page == "Semantic Engine":
    header("Semantic Engine", "Provenance, benchmarks, model quality and what each channel is good at")
    if not repo.space_id:
        st.warning("No semantic space. Run `pulsar semantics build`.")
        st.stop()

    space = repo.space_record()
    run = repo.run_record()
    stats = space.get("stats", {})
    k = st.columns(5)
    k[0].metric("Space", repo.space_id.split("-")[-1][:10])
    k[1].metric("Run", (repo.run_id or "—").split("-")[-1][:10])
    k[2].metric("Training documents", int(stats.get("training_documents", 0)))
    k[3].metric("Atoms", int(stats.get("atoms", 0)))
    k[4].metric("Channels", len(stats.get("channels", [])))

    tabs = st.tabs(["Benchmarks", "Map fidelity", "Topic quality", "Provenance"])
    with tabs[0]:
        card("Semantic regression battery",
             "Deterministic, self-supervised retrieval tasks built from PULSAR's own corpus. "
             "Different tasks have different winners on purpose: literal recall and thematic "
             "affinity are different problems, and a channel that wins both is unlikely to exist.")
        bench = cached("benchmarks")
        if len(bench):
            metric = st.selectbox("Metric", sorted(bench["metric"].unique()),
                                  index=sorted(bench["metric"].unique()).index("mrr")
                                  if "mrr" in set(bench["metric"]) else 0)
            sub = bench[bench["metric"] == metric]
            pivot = sub.pivot_table(index="benchmark", columns="channel", values="value",
                                    aggfunc="first").reset_index()
            st.dataframe(pivot, use_container_width=True, hide_index=True)
            vega(sub, {
                "mark": {"type": "bar"},
                "encoding": {
                    "y": {"field": "benchmark", "type": "nominal", "title": None,
                          "axis": {"labelLimit": 220}},
                    "x": {"field": "value", "type": "quantitative", "title": metric.upper()},
                    "color": {"field": "channel", "type": "nominal", "title": "Channel"},
                    "yOffset": {"field": "channel"},
                }}, height=430)
            card("What each task measures")
            notes = sub.drop_duplicates("benchmark")[["benchmark", "note"]]
            st.dataframe(notes, use_container_width=True, hide_index=True,
                         column_config={"note": st.column_config.TextColumn("Meaning", width="large")})
    with tabs[1]:
        fidelity = cached("map_diagnostics")
        st.dataframe(pd.DataFrame([{"metric": k2, "value": round(v, 4)} for k2, v in fidelity.items()]),
                     use_container_width=True, hide_index=True)
        st.caption("Stress is Kruskal stress-1 after optimal rescaling: 0 is perfect, "
                   "0.05–0.15 is good, above 0.2 means the 2-D plot should be read as "
                   "neighbourhoods only.")
    with tabs[2]:
        for facet in ("domain", "methods"):
            topics = cached("topics", facet)
            if not len(topics):
                continue
            diag = json.loads(topics["diagnostics_json"].iloc[0] or "{}")
            card(f"{facet.title()} topic model",
                 "Model selection weights coherence, stability under deterministic perturbation, "
                 "independent support and exclusivity — never reconstruction error, which always "
                 "improves with more factors and therefore only measures capacity.")
            space_stats = stats.get("topics", {}).get(facet, {})
            cols = st.columns(4)
            cols[0].metric("k", int(space_stats.get("selected_k", 0)))
            cols[1].metric("NPMI coherence", f"{space_stats.get('npmi', 0):.3f}")
            cols[2].metric("Stability", f"{space_stats.get('stability', 0):.3f}")
            cols[3].metric("Exclusivity", f"{space_stats.get('exclusivity', 0):.3f}")
            candidates = space_stats.get("candidates", [])
            if candidates:
                frame = pd.DataFrame(candidates)
                vega(frame.melt(id_vars="k",
                                value_vars=[c for c in ("score", "npmi", "stability", "exclusivity",
                                                        "support_fraction") if c in frame],
                                var_name="criterion", value_name="value"), {
                    "mark": {"type": "line", "point": True},
                    "encoding": {
                        "x": {"field": "k", "type": "quantitative", "title": "Number of factors"},
                        "y": {"field": "value", "type": "quantitative"},
                        "color": {"field": "criterion", "type": "nominal", "title": None},
                    }}, height=280)
                st.caption(space_stats.get("selection_reason", ""))
    with tabs[3]:
        a, b = st.columns(2)
        with a:
            card("Semantic space identity",
                 "Corpus + preprocessing + architecture + embedding model + library versions.")
            st.json(space.get("identity", {}))
        with b:
            card("Profile run",
                 "Space + operator interests. Editing config/profile.yaml creates a new run and "
                 "leaves topics, geometry and benchmarks untouched.")
            st.json(run.get("profile", {}))
        st.caption(f"space built {space.get('created_at')} · corpus fingerprint "
                   f"{str(space.get('corpus_fingerprint'))[:24]}")


# ---------------------------------------------------------------------------
# CAMPAIGNS
# ---------------------------------------------------------------------------

elif page == "Campaigns":
    header("Campaign Studio", "Evidence-backed audiences → individual drafts → review → explicit send")
    smtp_ready = bool(cfg.smtp.get("host"))
    if not smtp_ready:
        st.info("SMTP is not configured. Audiences, drafts, editing and previews work normally; "
                "sending stays disabled.")
    if not has_semantics():
        st.stop()

    from pulsar_research.outreach.campaigns import (campaign_rows, campaign_summary, create_campaign,
                                                    update_message, write_preview)
    from pulsar_research.outreach.selectors import AudienceQuery, select_audience
    from pulsar_research.semantics.corpus import load_corpus

    skills_all = cached("skills", "opportunity")
    topic_rows = cached("topics", "domain")

    with st.expander("Build an audience", expanded=True):
        c = st.columns(4)
        opp = cached("opportunities")
        centers = c[0].multiselect("Centers", sorted(opp["center"].dropna().unique().tolist()))
        funded = c[1].checkbox("Require a funded opportunity", value=True)
        exclude = c[2].checkbox("Exclude previously contacted", value=True)
        limit = c[3].number_input("Max recipients", min_value=0, value=0, step=5,
                                  help="0 = no limit")
        d = st.columns(3)
        min_opp = d[0].slider("Min opportunity percentile", 0, 100, 60, 5)
        min_cur = d[1].slider("Min professor current-fit percentile", 0, 100, 0, 5)
        min_traj = d[2].slider("Min professor trajectory percentile", 0, 100, 0, 5)
        e = st.columns(3)
        skill_opts = sorted(skills_all.loc[~skills_all["generic"], "skill_id"].unique().tolist()) if len(skills_all) else []
        want_skills = e[0].multiselect("Opportunity requires skill", skill_opts)
        topic_map = {f"{r.topic_id} · {r.label}": r.topic_id
                     for r in topic_rows.itertuples() if int(r.depth) == 0} if len(topic_rows) else {}
        want_topics = e[1].multiselect("Domain topics", list(topic_map))
        keywords = e[2].text_input("Required keywords (comma separated)")

        query = AudienceQuery(
            funded_only=funded, centers=centers, exclude_already_contacted=exclude,
            min_opportunity_percentile=float(min_opp) or None,
            min_current_percentile=float(min_cur) or None,
            min_trajectory_percentile=float(min_traj) or None,
            skill_ids=want_skills, topic_ids=[topic_map[t] for t in want_topics],
            keywords=[k.strip() for k in keywords.split(",") if k.strip()],
            limit=int(limit) or None,
        )
        audience = select_audience(db, query)
        st.markdown(f"**{len(audience)}** professors qualify.")
        if audience:
            st.dataframe(pd.DataFrame([{
                "score": r["selection_score"], "professor": r["professor_name"], "email": r["email"],
                "current_pct": round(r["current_percentile"], 1),
                "trajectory_pct": round(r["trajectory_percentile"], 1),
                "opportunities": len(r["qualifying_opportunities"]),
                "why": r["rationale"]["primary_title"][:70],
                "skills": ", ".join(r["rationale"]["matched_skills"][:5]),
            } for r in audience]), use_container_width=True, hide_index=True, height=280)

        with st.form("create_campaign"):
            name = st.text_input("Campaign name")
            submitted = st.form_submit_button("Freeze this audience and generate drafts",
                                              type="primary", disabled=not audience)
        if submitted:
            if not name.strip():
                st.error("A campaign name is required.")
            else:
                try:
                    cid, n = create_campaign(cfg, db, name.strip(), query,
                                             corpus_fingerprint=load_corpus(db).fingerprint())
                    st.success(f"Campaign {cid}: {n} drafts created. Nothing has been sent.")
                    refresh()
                except Exception as exc:
                    st.error(str(exc))

    campaigns = cached("campaigns")
    if not len(campaigns):
        st.caption("No campaigns yet.")
        st.stop()

    card("Campaign ledger")
    st.dataframe(campaigns, use_container_width=True, hide_index=True)
    cmap = {f"{r['name']} · {r['campaign_id']}": r["campaign_id"] for _, r in campaigns.iterrows()}
    cid = cmap[st.selectbox("Open campaign", list(cmap))]
    summary = campaign_summary(db, cid)
    k = st.columns(5)
    k[0].metric("Recipients", summary["recipients"])
    k[1].metric("Selected", summary["selected"])
    k[2].metric("Sent", summary["by_status"].get("sent", 0))
    k[3].metric("Failed", summary["by_status"].get("failed", 0))
    k[4].metric("Status", summary["status"])
    with st.expander("Frozen provenance and audience query"):
        st.json({"provenance": summary["provenance"], "audience_query": summary["audience_query"]})

    rows = campaign_rows(db, cid)
    st.dataframe(pd.DataFrame([{
        "selected": r["selected"], "status": r["status"], "score": r["selection_score"],
        "professor": r["professor_name"], "email": r["email"], "subject": r["subject"],
    } for r in rows]), use_container_width=True, hide_index=True, height=260)

    choices = {f"{r['professor_name']} · {r['siape']} · {r['status']}": r for r in rows}
    rec = choices[st.selectbox("Review a recipient", list(choices))]

    left, right = st.columns([1, 1.35])
    with left:
        card("Why this recipient qualified",
             "Frozen at campaign creation. Later corpus changes cannot rewrite this.")
        st.json(rec["rationale"])
        if rec["qualifying_evidence"]:
            st.dataframe(pd.DataFrame(rec["qualifying_evidence"])[
                [c for c in ["id_opportunity", "project_code", "plan_title", "funded_slots",
                             "edital", "opportunity_percentile"]
                 if c in pd.DataFrame(rec["qualifying_evidence"]).columns]],
                use_container_width=True, hide_index=True)
    with right:
        card("Individual draft", "Editing this message changes nothing for any other recipient.")
        selected = st.checkbox("Selected for sending", value=bool(rec["selected"]),
                               key=f"sel_{cid}_{rec['siape']}")
        subject = st.text_input("Subject", value=rec["subject"] or "", key=f"sub_{cid}_{rec['siape']}")
        body = st.text_area("Body (plain text)", value=rec["body_text"] or "", height=300,
                            key=f"body_{cid}_{rec['siape']}")
        b1, b2 = st.columns(2)
        if b1.button("Save draft", use_container_width=True):
            update_message(db, cid, rec["siape"], subject=subject, body_text=body, selected=selected)
            from pulsar_research.outreach.campaigns import regenerate_html
            regenerate_html(cfg, db, cid, only_uncustomized=False)
            st.success("Saved")
            refresh()
        if b2.button("Write HTML preview file", use_container_width=True):
            path = write_preview(db, cid, cfg.paths.exports_dir / f"campaign_{cid}.html")
            st.success(f"Preview written to {path}")
        if rec["body_html"]:
            with st.expander("HTML rendering"):
                st.components.v1.html(rec["body_html"], height=540, scrolling=True)

    st.divider()
    card("Sending", "Only recipients marked *selected* and not already sent are delivered. "
                    "Messages are sent exactly as stored — templates are never re-rendered at send time.")
    pending = sum(1 for r in rows if r["selected"] and r["status"] != "sent")
    st.markdown(f"**{pending}** message(s) would be sent.")
    confirm = st.checkbox("I reviewed every selected draft and explicitly authorize sending",
                          value=False, key=f"confirm_{cid}", disabled=not smtp_ready)
    if st.button("Send selected drafts", type="primary",
                 disabled=(not confirm) or (not smtp_ready) or pending == 0):
        from pulsar_research.outreach.mailer import send_campaign
        try:
            result = send_campaign(cfg, db, cid, confirm=True)
            st.success(f"Sent {result['sent']}, failed {result['failed']}.")
            refresh()
        except Exception as exc:
            st.error(str(exc))
