"""Repository results, shaped into JSON the console can hold entirely in memory.

The corpus is small — 187 work plans, 65 professors, 89 projects — which is the
single most important fact about this front end. Everything the operator filters,
sorts, searches and cross-links fits in a payload of a few hundred kilobytes, so
the browser can do all of it without a round trip. The server answers a handful
of list endpoints once and then gets out of the way; only the per-entity detail
views, which carry full plan text and evidence, are fetched lazily.

Column selection here is deliberate rather than `SELECT *`: a payload that
carries every column of every join is both slower and impossible to reason about
from the client.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from ..dashboard.queries import Repository, lorenz
from ..db import Database, json_load
from ..outreach.nomes import accented_names
from ..semantics.normalize import display_person_name


def clean(value: Any) -> Any:
    """Make a value JSON-safe without lying about missing data.

    NaN, NaT and numpy scalars all reach here from pandas. NaN becomes ``None``
    rather than ``0``: a percentile that was never computed is not a percentile
    of zero, and the difference decides whether the UI draws a bar or a dash.
    """
    if value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    # `int` before `np.integer`: pandas hands back plain Python ints for some
    # columns and numpy scalars for others, and only checking the numpy type
    # sent the plain ones down to the `str(value)` fallback, which turned every
    # count in the payload into a string the front end then could not sort.
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    if isinstance(value, (np.str_, str)):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [clean(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): clean(v) for k, v in value.items()}
    if value is pd.NaT or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def records(frame: pd.DataFrame, columns: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """DataFrame to a list of JSON-safe dicts, keeping only `columns` that exist."""
    if frame is None or not len(frame):
        return []
    if columns:
        keep = [c for c in columns if c in frame.columns]
        frame = frame[keep]
    return [{k: clean(v) for k, v in row.items()} for row in frame.to_dict("records")]


OPPORTUNITY_COLUMNS = (
    "id_opportunity", "project_code", "project_title", "plan_title", "professor_siape",
    "professor_name", "center", "department", "area", "edital", "has_funding",
    "funded_slots", "application_status", "rank_pct", "x", "y",
    "domain_fused_pct", "methods_fused_pct", "skills_fused_pct", "skills_skill_match_pct",
)

PROFESSOR_COLUMNS = (
    "siape", "canonical_name", "center", "department", "email", "lattes_id",
    "opportunities", "funded_opportunities", "funded_slots", "current_projects",
    "publications", "orientations", "collaborators", "portfolio_items",
    "latest_evidence_year", "current_fused_pct", "trajectory_fused_pct", "x", "y",
)

#: What the outreach view actually renders from each frozen evidence record.
EVIDENCE_COLUMNS = frozenset({
    "id_opportunity", "project_code", "plan_title", "project_title",
    "funded_slots", "has_funding", "edital", "opportunity_percentile",
})

PROJECT_COLUMNS = (
    "project_code", "project_title", "professor_siape", "professor_name", "center",
    "area", "work_plans", "funded_slots", "x", "y", "topic_label", "topic_id", "fused_pct",
)


class Payloads:
    """Every JSON document the console can ask for."""

    def __init__(self, db: Database):
        self.db = db
        self.repo = Repository(db)
        self._names: dict[str, str] | None = None

    # `professors.canonical_name` is a matching key: lowercased, accents
    # stripped. Showing it raw makes the console address people as "diego
    # figueiredo nobrega". The Lattes record has the real spelling, and the
    # outreach layer already knows how to recover it safely.
    def display_name(self, siape: Any, fallback: Any = "") -> str:
        if self._names is None:
            try:
                self._names = accented_names(self.db)
            except Exception:
                self._names = {}
        recovered = self._names.get(str(siape))
        return recovered or display_person_name(str(fallback or ""))

    # -- shell ------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        """What the shell needs before it can render anything: provenance and scale."""
        space = self.repo.space_record()
        run = self.repo.run_record()
        counts = self.db.counts()
        campaigns = records(self.repo.campaigns())
        return {
            "space_id": self.repo.space_id or None,
            "run_id": self.repo.run_id or None,
            "space_created_at": clean(space.get("created_at")),
            "corpus_fingerprint": clean(space.get("corpus_fingerprint")),
            "counts": {k: int(v) for k, v in counts.items()},
            "metrics": {k: clean(v) for k, v in self.repo.global_metrics().items()},
            "channels": self.repo.channels(),
            "campaigns": campaigns,
            "ready": bool(self.repo.space_id and self.repo.run_id),
            "profile": clean(run.get("profile", {})),
        }

    # -- lists ------------------------------------------------------------

    def opportunities(self) -> dict[str, Any]:
        frame = self.repo.opportunities()
        skills = self.repo.skills("opportunity")
        by_entity: dict[str, list[str]] = {}
        if len(skills):
            for row in skills[~skills["generic"]].itertuples():
                by_entity.setdefault(str(row.entity_id), []).append(str(row.label))
        rows = records(frame, OPPORTUNITY_COLUMNS)
        for row in rows:
            row["skills"] = by_entity.get(str(row.get("id_opportunity")), [])[:8]
            row["professor_name"] = self.display_name(row.get("professor_siape"),
                                                      row.get("professor_name"))
        return {
            "rows": rows,
            "skill_options": sorted({s for v in by_entity.values() for s in v}),
            "centers": sorted({r["center"] for r in rows if r.get("center")}),
            "editais": sorted({r["edital"] for r in rows if r.get("edital")}),
            "statuses": sorted({r["application_status"] for r in rows if r.get("application_status")}),
        }

    def professors(self) -> dict[str, Any]:
        rows = records(self.repo.professors(), PROFESSOR_COLUMNS)
        for row in rows:
            row["canonical_name"] = self.display_name(row.get("siape"), row.get("canonical_name"))
        return {
            "rows": rows,
            "centers": sorted({r["center"] for r in rows if r.get("center")}),
        }

    def landscape(self, facet: str = "domain") -> dict[str, Any]:
        projects = self.repo.projects()
        topics = self.repo.topics(facet)
        topic_rows = []
        for row in topics.itertuples() if len(topics) else ():
            topic_rows.append({
                "topic_id": clean(row.topic_id), "parent_id": clean(row.parent_id),
                "depth": int(row.depth), "label": clean(row.label),
                "terms": json_load(row.terms_json, []),
            })
        supply = pd.DataFrame()
        opp = self.repo.opportunities()
        if len(opp):
            supply = opp.groupby("professor_siape", dropna=True).agg(
                opportunities=("id_opportunity", "count"),
                slots=("funded_slots", "sum")).reset_index()
        curves = {}
        for column, name in (("opportunities", "work_plans"), ("slots", "funded_slots")):
            frame = lorenz(supply[column]) if len(supply) else lorenz([])
            curves[name] = records(frame, ("population", "share"))
        skills = self.repo.skill_landscape("opportunity")
        return {
            "facet": facet,
            "projects": records(projects, PROJECT_COLUMNS),
            "topics": topic_rows,
            "composition": records(self.repo.topic_composition(facet, "project")),
            "map": {k: clean(v) for k, v in self.repo.map_diagnostics().items()},
            "skills": records(skills[~skills["generic"]] if len(skills) else skills),
            "generic_skills": records(skills[skills["generic"]] if len(skills) else skills),
            "lorenz": curves,
        }

    def engine(self) -> dict[str, Any]:
        space = self.repo.space_record()
        run = self.repo.run_record()
        stats = space.get("stats", {}) or {}
        topic_quality = {}
        for facet in ("domain", "methods"):
            facet_stats = (stats.get("topics", {}) or {}).get(facet, {}) or {}
            topic_quality[facet] = clean(facet_stats)
        return {
            "space": {"space_id": clean(space.get("space_id")),
                      "created_at": clean(space.get("created_at")),
                      "fingerprint": clean(space.get("corpus_fingerprint")),
                      "identity": clean(space.get("identity", {})),
                      "versions": clean(space.get("versions", {})),
                      "stats": clean(stats)},
            "run": {"run_id": clean(run.get("run_id")),
                    "created_at": clean(run.get("created_at")),
                    "profile": clean(run.get("profile", {}))},
            "benchmarks": records(self.repo.benchmarks()),
            "map": {k: clean(v) for k, v in self.repo.map_diagnostics().items()},
            "topic_quality": topic_quality,
        }

    # -- details ----------------------------------------------------------

    def opportunity(self, oid: str) -> dict[str, Any]:
        frame = self.db.query_df("SELECT * FROM opportunities WHERE id_opportunity=?", [oid])
        if not len(frame):
            return {}
        row = {k: clean(v) for k, v in frame.iloc[0].to_dict().items()}
        scores = self.repo.entity_scores("opportunity", oid)
        return {
            "record": row,
            "scores": records(scores),
            "skills": records(self.repo.skills("opportunity", oid)),
            "topics": {facet: records(self.repo.entity_topics("opportunity", oid, facet))
                       for facet in ("domain", "methods")},
        }

    def professor(self, siape: str) -> dict[str, Any]:
        frame = self.db.query_df("SELECT * FROM professors WHERE siape=?", [siape])
        if not len(frame):
            return {}
        row = {k: clean(v) for k, v in frame.iloc[0].to_dict().items()}
        row["canonical_name"] = self.display_name(siape, row.get("canonical_name"))
        evidence = self.repo.evidence(siape)
        return {
            "record": row,
            "scores": records(self.repo.entity_scores("professor", siape)),
            "evidence": records(evidence, ("scope", "evidence_rank", "kind", "label",
                                           "year", "score", "weight")),
            "skills": records(self.repo.skills("professor", siape)),
            "topics": {facet: records(self.repo.entity_topics("professor", siape, facet))
                       for facet in ("domain", "methods")},
            "opportunities": records(self.db.query_df(
                "SELECT id_opportunity, plan_title, project_title, has_funding, funded_slots, "
                "edital, status FROM opportunities WHERE professor_siape=? "
                "ORDER BY has_funding DESC, funded_slots DESC", [siape])),
            "collaborators": records(self.repo.collaborators(siape)),
        }

    # -- campaigns --------------------------------------------------------

    def campaign(self, campaign_id: str) -> dict[str, Any]:
        """One campaign: the frozen audience, every draft, and where it now stands."""
        from ..outreach import outcomes as oc
        from ..outreach.campaigns import campaign_rows, campaign_summary

        summary = campaign_summary(self.db, campaign_id)
        recorded = oc.outcomes(self.db, campaign_id)
        elsewhere = oc.indicated(self.db)
        rows = []
        for row in campaign_rows(self.db, campaign_id):
            siape = str(row["siape"])
            outcome = recorded.get((campaign_id, siape), {})
            rationale = row.get("rationale") or {}
            rows.append({
                "siape": siape,
                "professor_name": self.display_name(siape, row.get("professor_name")),
                "email": clean(row.get("email")),
                "subject": clean(row.get("subject")),
                "body_text": clean(row.get("body_text")),
                "status": clean(row.get("status")),
                "selected": bool(row.get("selected")),
                "sent_at": clean(row.get("sent_at")),
                "error": clean(row.get("error")),
                "selection_score": clean(row.get("selection_score")),
                "is_customized": bool(row.get("is_customized")),
                "state": outcome.get("state") or (
                    oc.DEFAULT_STATE if row.get("status") == "sent" else None),
                "note": outcome.get("note") or "",
                "replied_at": outcome.get("replied_at"),
                "primary_title": clean(rationale.get("primary_title")),
                "matched_skills": clean(rationale.get("matched_skills") or []),
                "opportunity_percentile": clean(rationale.get("opportunity_percentile")),
                "funded_slots": clean(rationale.get("funded_slots")),
                # The frozen evidence carries whole opportunity records, which was
                # 73% of this payload and ten times what the view renders.
                "qualifying_evidence": [
                    {k: clean(v) for k, v in item.items() if k in EVIDENCE_COLUMNS}
                    for item in (row.get("qualifying_evidence") or [])
                ],
                "rationale": clean(rationale),
            })
        return {
            "campaign_id": campaign_id,
            "summary": clean(summary),
            "rows": rows,
            "funnel": oc.funnel(self.db, campaign_id),
            "states": [{"key": s, "label": oc.LABELS[s]} for s in oc.STATES],
            "indicated": None if not elsewhere else {
                "campaign_id": elsewhere[0], "siape": elsewhere[1]},
            # Sending stays a deliberate act at a terminal. A local web page that
            # can put 32 messages in front of 32 professors with one click is one
            # misplaced click away from an irreversible mistake, so the console
            # shows the exact command instead of running it.
            "send_command": f"pulsar campaign send {campaign_id} --confirm",
        }
