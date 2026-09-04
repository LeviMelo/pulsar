"""Dashboard data access.

All SQL lives here so the Streamlit layer stays presentational. Every function
returns a DataFrame or a plain dict — nothing Streamlit-specific — which makes
the same layer reusable from notebooks, the CLI or a future frontend.
"""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np
import pandas as pd

from ..db import Database, json_load


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # -- provenance ---------------------------------------------------------

    def meta(self, key: str, default: str = "") -> str:
        return str(self.db.scalar("SELECT value FROM meta WHERE key=?", [key], default))

    @property
    def space_id(self) -> str:
        return self.meta("current_semantic_space_id")

    @property
    def run_id(self) -> str:
        return self.meta("current_profile_run_id")

    def space_record(self) -> dict[str, Any]:
        df = self.db.query_df("SELECT * FROM semantic_spaces WHERE space_id=?", [self.space_id])
        if not len(df):
            return {}
        record = df.iloc[0].to_dict()
        record["identity"] = json_load(record.pop("identity_json"), {})
        record["stats"] = json_load(record.pop("stats_json"), {})
        record["versions"] = json_load(record.pop("versions_json"), {})
        return record

    def run_record(self) -> dict[str, Any]:
        df = self.db.query_df("SELECT * FROM profile_runs WHERE run_id=?", [self.run_id])
        if not len(df):
            return {}
        record = df.iloc[0].to_dict()
        record["profile"] = json_load(record.pop("profile_json"), {})
        record["stats"] = json_load(record.pop("stats_json"), {})
        return record

    def channels(self) -> list[str]:
        df = self.db.query_df(
            "SELECT DISTINCT channel FROM entity_scores WHERE run_id=? ORDER BY channel", [self.run_id])
        return [str(c) for c in df["channel"]] if len(df) else []

    # -- scores -------------------------------------------------------------

    def scores_wide(self, entity_type: str, facet: str) -> pd.DataFrame:
        """One row per entity, one column per channel (score and percentile)."""
        df = self.db.query_df(
            "SELECT entity_id, channel, score, percentile FROM entity_scores "
            "WHERE run_id=? AND entity_type=? AND facet=?", [self.run_id, entity_type, facet])
        if not len(df):
            return pd.DataFrame(columns=["entity_id"])
        score = df.pivot_table(index="entity_id", columns="channel", values="score", aggfunc="first")
        pct = df.pivot_table(index="entity_id", columns="channel", values="percentile", aggfunc="first")
        pct.columns = [f"{c}_pct" for c in pct.columns]
        return score.join(pct).reset_index()

    def score_matrix(self, entity_type: str) -> pd.DataFrame:
        """Long-form scores for every facet and channel."""
        return self.db.query_df(
            "SELECT entity_id, facet, channel, score, percentile FROM entity_scores "
            "WHERE run_id=? AND entity_type=?", [self.run_id, entity_type])

    def entity_scores(self, entity_type: str, entity_id: str) -> pd.DataFrame:
        return self.db.query_df(
            "SELECT facet, channel, ROUND(score,4) AS score, ROUND(percentile,1) AS percentile "
            "FROM entity_scores WHERE run_id=? AND entity_type=? AND entity_id=? "
            "ORDER BY facet, channel", [self.run_id, entity_type, entity_id])

    # -- entities -----------------------------------------------------------

    def opportunities(self) -> pd.DataFrame:
        base = self.db.query_df(
            """
            SELECT o.id_opportunity, o.project_code, o.project_title, o.plan_title, o.professor_siape,
                   o.professor_name, o.center, o.department, o.area, o.large_area, o.edital, o.quota,
                   COALESCE(o.has_funding,FALSE) AS has_funding, COALESCE(o.funded_slots,0) AS funded_slots,
                   COALESCE(a.status,'não inscrito') AS application_status
            FROM opportunities o LEFT JOIN applications a USING (id_opportunity)
            """
        )
        geo = self.db.query_df(
            "SELECT entity_id, x, y, cluster_id FROM entity_geometry "
            "WHERE space_id=? AND entity_type='opportunity'", [self.space_id])
        overall = self.scores_wide("opportunity", "overall")
        domain = self.scores_wide("opportunity", "domain")
        methods = self.scores_wide("opportunity", "methods")
        skills = self.scores_wide("opportunity", "skills")
        out = base.merge(geo, left_on="id_opportunity", right_on="entity_id", how="left")
        for frame, prefix in ((overall, "overall"), (domain, "domain"),
                              (methods, "methods"), (skills, "skills")):
            if len(frame):
                cols = {c: f"{prefix}_{c}" for c in frame.columns if c != "entity_id"}
                out = out.merge(frame.rename(columns=cols), left_on="id_opportunity",
                                right_on="entity_id", how="left", suffixes=("", f"_{prefix}"))
        out = out.loc[:, ~out.columns.duplicated()]
        out["rank_pct"] = out.get("overall_fused_pct", pd.Series(np.nan, index=out.index))
        return out

    def projects(self) -> pd.DataFrame:
        base = self.db.query_df(
            """
            SELECT o.project_code, ANY_VALUE(o.project_title) AS project_title,
                   ANY_VALUE(o.professor_siape) AS professor_siape,
                   ANY_VALUE(o.professor_name) AS professor_name,
                   ANY_VALUE(o.center) AS center, ANY_VALUE(o.area) AS area,
                   COUNT(*) AS work_plans, COALESCE(SUM(o.funded_slots),0) AS funded_slots
            FROM opportunities o WHERE COALESCE(o.project_code,'')<>'' GROUP BY o.project_code
            """
        )
        geo = self.db.query_df(
            "SELECT entity_id, x, y, cluster_id FROM entity_geometry "
            "WHERE space_id=? AND entity_type='project'", [self.space_id])
        scores = self.scores_wide("project", "overall")
        out = base.merge(geo, left_on="project_code", right_on="entity_id", how="left")
        if len(scores):
            out = out.merge(scores, left_on="project_code", right_on="entity_id",
                            how="left", suffixes=("", "_score"))
        topics = self.db.query_df(
            "SELECT et.entity_id, t.label AS topic_label, et.topic_id FROM entity_topics et "
            "JOIN semantic_topics t ON t.space_id=et.space_id AND t.facet=et.facet AND t.topic_id=et.topic_id "
            "WHERE et.space_id=? AND et.entity_type='project' AND et.facet='domain' AND et.is_dominant "
            "AND t.depth=0", [self.space_id])
        if len(topics):
            out = out.merge(topics.drop_duplicates("entity_id"), left_on="project_code",
                            right_on="entity_id", how="left", suffixes=("", "_topic"))
        return out.loc[:, ~out.columns.duplicated()]

    def professors(self) -> pd.DataFrame:
        base = self.db.query_df(
            """
            SELECT p.siape, p.canonical_name, p.center, p.department, p.email, p.profile_summary,
                   p.lattes_id,
                   COALESCE(m.opportunity_count,0) AS opportunities,
                   COALESCE(m.funded_opportunity_count,0) AS funded_opportunities,
                   COALESCE(m.funded_slots,0) AS funded_slots,
                   COALESCE(m.current_project_count,0) AS current_projects,
                   COALESCE(m.public_project_count,0) AS public_projects,
                   COALESCE(m.lattes_project_count,0) AS lattes_projects,
                   COALESCE(m.publication_count,0) AS publications,
                   COALESCE(m.orientation_count,0) AS orientations,
                   COALESCE(m.funding_agency_count,0) AS funders,
                   COALESCE(m.collaborator_count,0) AS collaborators,
                   COALESCE(m.atom_count,0) AS portfolio_items,
                   m.latest_evidence_year
            FROM professors p LEFT JOIN professor_metrics m USING (siape)
            """
        )
        geo = self.db.query_df(
            "SELECT entity_id, x, y, cluster_id FROM entity_geometry "
            "WHERE space_id=? AND entity_type='professor'", [self.space_id])
        out = base.merge(geo, left_on="siape", right_on="entity_id", how="left")
        for facet in ("current", "trajectory", "methods", "skills"):
            frame = self.scores_wide("professor", facet)
            if len(frame):
                cols = {c: f"{facet}_{c}" for c in frame.columns if c != "entity_id"}
                out = out.merge(frame.rename(columns=cols), left_on="siape",
                                right_on="entity_id", how="left", suffixes=("", f"_{facet}"))
        return out.loc[:, ~out.columns.duplicated()]

    # -- structure ----------------------------------------------------------

    def topics(self, facet: str = "domain") -> pd.DataFrame:
        return self.db.query_df(
            "SELECT topic_id, parent_id, depth, label, terms_json, diagnostics_json "
            "FROM semantic_topics WHERE space_id=? AND facet=? ORDER BY topic_id",
            [self.space_id, facet])

    def topic_composition(self, facet: str = "domain", entity_type: str = "project") -> pd.DataFrame:
        return self.db.query_df(
            """
            SELECT t.topic_id, t.label, t.depth,
                   SUM(CASE WHEN et.is_dominant THEN 1 ELSE 0 END) AS dominant,
                   ROUND(AVG(et.weight),4) AS mean_share
            FROM entity_topics et
            JOIN semantic_topics t ON t.space_id=et.space_id AND t.facet=et.facet AND t.topic_id=et.topic_id
            WHERE et.space_id=? AND et.facet=? AND et.entity_type=?
            GROUP BY t.topic_id, t.label, t.depth
            ORDER BY dominant DESC, mean_share DESC
            """, [self.space_id, facet, entity_type])

    def entity_topics(self, entity_type: str, entity_id: str, facet: str) -> pd.DataFrame:
        return self.db.query_df(
            "SELECT t.label, et.topic_id, ROUND(et.weight,4) AS share, et.is_dominant "
            "FROM entity_topics et JOIN semantic_topics t "
            "ON t.space_id=et.space_id AND t.facet=et.facet AND t.topic_id=et.topic_id "
            "WHERE et.space_id=? AND et.entity_type=? AND et.entity_id=? AND et.facet=? "
            "ORDER BY et.weight DESC LIMIT 12",
            [self.space_id, entity_type, entity_id, facet])

    def skills(self, entity_type: str, entity_id: str | None = None) -> pd.DataFrame:
        sql = ("SELECT entity_id, skill_id, label, category, generic, mentions FROM entity_skills "
               "WHERE space_id=? AND entity_type=?")
        params: list[Any] = [self.space_id, entity_type]
        if entity_id is not None:
            sql += " AND entity_id=?"
            params.append(entity_id)
        return self.db.query_df(sql + " ORDER BY generic, mentions DESC", params)

    def skill_landscape(self, entity_type: str = "opportunity") -> pd.DataFrame:
        return self.db.query_df(
            "SELECT label, category, generic, COUNT(DISTINCT entity_id) AS entities "
            "FROM entity_skills WHERE space_id=? AND entity_type=? "
            "GROUP BY label, category, generic ORDER BY entities DESC",
            [self.space_id, entity_type])

    def evidence(self, siape: str, scope: str | None = None) -> pd.DataFrame:
        sql = ("SELECT scope, evidence_rank, kind, label, year, score, weight, payload_json "
               "FROM professor_evidence WHERE run_id=? AND siape=?")
        params: list[Any] = [self.run_id, siape]
        if scope:
            sql += " AND scope=?"
            params.append(scope)
        df = self.db.query_df(sql + " ORDER BY scope, evidence_rank", params)
        if len(df):
            df["payload"] = df["payload_json"].map(lambda v: json_load(v, {}))
            df = df.drop(columns=["payload_json"])
        return df

    # -- diagnostics --------------------------------------------------------

    def benchmarks(self) -> pd.DataFrame:
        return self.db.query_df(
            "SELECT benchmark, channel, metric, value, note FROM semantic_benchmarks WHERE space_id=?",
            [self.space_id])

    def map_diagnostics(self) -> dict[str, float]:
        df = self.db.query_df(
            "SELECT metric, value FROM map_diagnostics WHERE space_id=? AND entity_type='project'",
            [self.space_id])
        return {str(r.metric): float(r.value) for r in df.itertuples()}

    def global_metrics(self) -> dict[str, float]:
        df = self.db.query_df("SELECT metric, value FROM global_metrics")
        return {str(r.metric): float(r.value) for r in df.itertuples()}

    def collaborators(self, siape: str) -> pd.DataFrame:
        return self.db.query_df(
            "SELECT collaborator_name, target_siape, weight FROM collaboration_edges "
            "WHERE source_siape=? ORDER BY weight DESC LIMIT 40", [siape])

    def public_pages(self, siape: str) -> pd.DataFrame:
        if not self.db.table_exists("sigaa_public_pages"):
            return pd.DataFrame()
        return self.db.query_df(
            "SELECT page_type, visible_text FROM sigaa_public_pages "
            "WHERE siape=? AND page_type IN ('pesquisa','producao','extensao','disciplinas') "
            "ORDER BY page_type", [siape])

    # -- campaigns ----------------------------------------------------------

    def campaigns(self) -> pd.DataFrame:
        return self.db.query_df(
            "SELECT c.campaign_id, c.name, c.status, c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM campaign_recipients r WHERE r.campaign_id=c.campaign_id) AS recipients, "
            "(SELECT COUNT(*) FROM campaign_recipients r WHERE r.campaign_id=c.campaign_id AND r.selected) AS selected, "
            "(SELECT COUNT(*) FROM campaign_messages m WHERE m.campaign_id=c.campaign_id AND m.status='sent') AS sent "
            "FROM campaigns c ORDER BY c.created_at DESC")


def lorenz(values: Sequence[float]) -> pd.DataFrame:
    """Cumulative share curve; the 45° line is perfect equality."""
    arr = np.asarray(list(values), dtype=float)
    arr = np.maximum(arr[np.isfinite(arr)], 0)
    if len(arr) == 0 or arr.sum() <= 0:
        return pd.DataFrame({"population": [0.0, 1.0], "share": [0.0, 1.0]})
    arr = np.sort(arr)
    y = np.concatenate([[0.0], np.cumsum(arr) / arr.sum()])
    return pd.DataFrame({"population": np.linspace(0.0, 1.0, len(y)), "share": y})
