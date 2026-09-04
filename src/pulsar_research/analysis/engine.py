from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Mapping, Sequence

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from ..config import AppConfig
from ..db import Database, json_text, utcnow
from .metrics import gini, hhi
from .text import clean_text, text_hash
from .v2 import (
    FixedSparseSpace,
    benchmark_opportunity_retrieval,
    choose_hierarchical_clusters,
    corpus_hash,
    fit_topic_space,
    opportunity_views,
    pcoa_from_cosine,
    profile_queries,
    project_records,
    rrf,
    score_ad_hoc_opportunities,
    score_opportunities,
)

SEMANTIC_ENGINE_VERSION = "2.0.0"


def _fetch_opportunities(db: Database) -> list[dict[str, Any]]:
    with db.connect(read_only=True) as con:
        cols = [d[0] for d in con.execute("SELECT * FROM opportunities LIMIT 0").description]
        rows = [dict(zip(cols, r)) for r in con.execute("SELECT * FROM opportunities ORDER BY id_opportunity").fetchall()]
    return rows


def _model_config(config: AppConfig) -> dict[str, Any]:
    a = config.analysis
    return {
        "engine_version": SEMANTIC_ENGINE_VERSION,
        "word_max_features": int(a.get("word_max_features", 24000)),
        "char_max_features": int(a.get("char_max_features", 24000)),
        "lsa_components": int(a.get("lsa_components", 48)),
        "rrf_k": int(a.get("rrf_k", 60)),
        "topic_min_k": int(a.get("topic_min_k", 6)),
        "topic_max_k": int(a.get("topic_max_k", 14)),
        "topic_min_project_support": int(a.get("topic_min_project_support", 2)),
        "cluster_min_k": int(a.get("cluster_min_k", 3)),
        "cluster_max_k": int(a.get("cluster_max_k", 12)),
        "professor_evidence_top_k": int(a.get("professor_evidence_top_k", 3)),
    }


def _model_id(corpus_digest: str, model_cfg: Mapping[str, Any], queries: Mapping[str, str]) -> str:
    payload = json.dumps(
        {"corpus": corpus_digest, "config": model_cfg, "queries": dict(queries)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "semv2-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _normalize_display(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    mx = float(np.nanmax(values)) if len(values) else 0.0
    return values / mx if mx > 0 else np.zeros_like(values)


def _aggregate_top(values: Sequence[float], k: int) -> float:
    vals = sorted((float(v) for v in values if np.isfinite(v)), reverse=True)
    if not vals:
        return 0.0
    return float(np.mean(vals[: max(1, int(k))]))


def _mode_int(values: Sequence[int]) -> int:
    vals = [int(v) for v in values]
    if not vals:
        return -1
    counts = Counter(vals)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _professor_portfolio_items(db: Database, opportunities: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for row in opportunities:
        siape = str(row.get("professor_siape") or "")
        if not siape:
            continue
        label = clean_text(str(row.get("plan_title") or row.get("project_title") or row.get("id_opportunity") or ""))
        text = "\n".join(
            x for x in [
                clean_text(str(row.get("project_title") or "")),
                clean_text(str(row.get("plan_title") or "")),
                clean_text(str(row.get("introduction_justification") or "")),
                clean_text(str(row.get("objectives") or "")),
            ] if x
        )
        key = (siape, text.casefold())
        if text and key not in seen:
            seen.add(key)
            items.append({
                "siape": siape,
                "item_type": "opportunity",
                "item_id": str(row.get("id_opportunity") or ""),
                "label": label,
                "text": text,
            })

    with db.connect(read_only=True) as con:
        profs = con.execute("SELECT siape,canonical_name,profile_summary FROM professors ORDER BY siape").fetchall()
        for siape, name, summary in profs:
            text = clean_text(str(summary or ""))
            if text:
                key = (str(siape), text.casefold())
                if key not in seen:
                    seen.add(key)
                    items.append({
                        "siape": str(siape), "item_type": "profile", "item_id": str(siape),
                        "label": clean_text(str(name or siape)), "text": text,
                    })

        has_lattes = bool(con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='sigaa_public_lattes_flat'"
        ).fetchone()[0])
        if has_lattes:
            rows = con.execute(
                """
                SELECT siape,path,value_text
                FROM sigaa_public_lattes_flat
                WHERE value_type='string' AND COALESCE(value_text,'')<>''
                  AND (
                    LOWER(path) LIKE '%nomedoprojeto%'
                    OR LOWER(path) LIKE '%titulodoartigo%'
                    OR LOWER(path) LIKE '%titulodotrabalho%'
                    OR LOWER(path) LIKE '%titulodolivro%'
                    OR LOWER(path) LIKE '%titulodocapitulo%'
                    OR LOWER(path) LIKE '%nomedaareadoconhecimento%'
                  )
                ORDER BY siape,path
                """
            ).fetchall()
            for siape, path, value in rows:
                text = clean_text(str(value or ""))
                if len(text) < 8:
                    continue
                key = (str(siape), text.casefold())
                if key in seen:
                    continue
                seen.add(key)
                lp = str(path or "").lower()
                if "nomedoprojeto" in lp:
                    typ = "lattes_project"
                elif "areadoconhecimento" in lp:
                    typ = "lattes_area"
                else:
                    typ = "publication"
                items.append({
                    "siape": str(siape), "item_type": typ,
                    "item_id": hashlib.sha1((str(siape) + "|" + str(path) + "|" + text).encode("utf-8")).hexdigest()[:16],
                    "label": text[:500], "text": text,
                })
    return items


def _professor_scores(
    db: Database,
    opportunities: Sequence[Mapping[str, Any]],
    opportunity_result,
    queries: Mapping[str, str],
    coords_by_opp: Mapping[str, tuple[float, float]],
    clusters_by_opp: Mapping[str, int],
    model_cfg: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with db.connect(read_only=True) as con:
        profs = con.execute("SELECT siape,canonical_name FROM professors ORDER BY siape").fetchall()
    siapes = [str(x[0]) for x in profs]
    pindex = {s: i for i, s in enumerate(siapes)}
    n = len(siapes)
    top_k = int(model_cfg["professor_evidence_top_k"])

    opp_by_prof: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(opportunities):
        s = str(row.get("professor_siape") or "")
        if s in pindex:
            opp_by_prof[s].append(i)

    # Domain evidence is portfolio-based: each project/publication/profile item
    # remains atomic so the system can preserve *why* a professor matched.
    evidence_items = _professor_portfolio_items(db, opportunities)
    evidence_rows: list[dict[str, Any]] = []
    domain_word = np.zeros(n); domain_char = np.zeros(n); domain_lsa = np.zeros(n); domain_fit = np.zeros(n)
    if evidence_items:
        space = FixedSparseSpace(
            word_max_features=int(model_cfg["word_max_features"]),
            char_max_features=int(model_cfg["char_max_features"]),
            lsa_components=int(model_cfg["lsa_components"]),
        ).fit([x["text"] for x in evidence_items])
        ss = space.score(queries["domain"])
        fused = rrf([ss.word, ss.char, ss.lsa], weights=[1.0, 1.0, 0.75], k=int(model_cfg["rrf_k"]))
        per_prof_items: dict[str, list[int]] = defaultdict(list)
        for i, item in enumerate(evidence_items):
            per_prof_items[item["siape"]].append(i)
        for siape, inds in per_prof_items.items():
            if siape not in pindex:
                continue
            pi = pindex[siape]
            ranked = sorted(inds, key=lambda j: (-float(fused[j]), evidence_items[j]["item_id"]))
            chosen = ranked[:max(1, top_k)]
            domain_word[pi] = _aggregate_top([ss.word[j] for j in chosen], top_k)
            domain_char[pi] = _aggregate_top([ss.char[j] for j in chosen], top_k)
            domain_lsa[pi] = _aggregate_top([ss.lsa[j] for j in chosen], top_k)
            domain_fit[pi] = _aggregate_top([fused[j] for j in chosen], top_k)
            for rank, j in enumerate(ranked[:5], start=1):
                item = evidence_items[j]
                evidence_rows.append({
                    "siape": siape, "rank": rank, "item_type": item["item_type"],
                    "item_id": item["item_id"], "label": item["label"], "score": float(fused[j]),
                    "payload": {"word": float(ss.word[j]), "char": float(ss.char[j]), "lsa": float(ss.lsa[j])},
                })

    # Method/skill fits are opportunity-portfolio aggregates. We keep the top-k
    # mean rather than a giant professor document so breadth does not erase evidence.
    arrays = {
        "method_word": np.zeros(n), "method_char": np.zeros(n), "method_lsa": np.zeros(n),
        "method_bm25f": np.zeros(n), "method_fit": np.zeros(n),
        "skill_word": np.zeros(n), "skill_char": np.zeros(n), "skill_lsa": np.zeros(n),
        "skill_bm25f": np.zeros(n), "skill_fit": np.zeros(n),
    }
    professor_coords = [(0.0, 0.0)] * n
    professor_clusters = [-1] * n
    for siape, inds in opp_by_prof.items():
        pi = pindex[siape]
        for prefix, result in [("method", opportunity_result.methods), ("skill", opportunity_result.skills)]:
            for attr in ["word", "char", "lsa", "bm25f", "fit"]:
                vals = np.asarray(getattr(result, attr))[inds]
                arrays[f"{prefix}_{attr}"][pi] = _aggregate_top(vals, top_k)
        xy = [coords_by_opp.get(str(opportunities[j].get("id_opportunity") or ""), (0.0, 0.0)) for j in inds]
        professor_coords[pi] = (float(np.mean([p[0] for p in xy])), float(np.mean([p[1] for p in xy]))) if xy else (0.0, 0.0)
        professor_clusters[pi] = _mode_int([clusters_by_opp.get(str(opportunities[j].get("id_opportunity") or ""), -1) for j in inds])

    combined = rrf([domain_fit, arrays["method_fit"], arrays["skill_fit"]], k=int(model_cfg["rrf_k"])) if n else np.array([])
    rows: list[dict[str, Any]] = []
    for i, siape in enumerate(siapes):
        rows.append({
            "entity_type": "professor", "entity_id": siape,
            "domain_word": float(domain_word[i]), "domain_char": float(domain_char[i]),
            "domain_lsa": float(domain_lsa[i]), "domain_bm25f": 0.0, "domain_fit": float(domain_fit[i]),
            "method_word": float(arrays["method_word"][i]), "method_char": float(arrays["method_char"][i]),
            "method_lsa": float(arrays["method_lsa"][i]), "method_bm25f": float(arrays["method_bm25f"][i]),
            "method_fit": float(arrays["method_fit"][i]),
            "skill_word": float(arrays["skill_word"][i]), "skill_char": float(arrays["skill_char"][i]),
            "skill_lsa": float(arrays["skill_lsa"][i]), "skill_bm25f": float(arrays["skill_bm25f"][i]),
            "skill_fit": float(arrays["skill_fit"][i]), "combined_fit": float(combined[i]) if len(combined) else 0.0,
            "cluster_id": int(professor_clusters[i]), "x": float(professor_coords[i][0]), "y": float(professor_coords[i][1]),
        })
    return rows, evidence_rows


def _persist_benchmarks(con, model_id: str, benchmark: Mapping[str, Any], now: str) -> None:
    for bench_name in ["title_to_body", "sibling_plan"]:
        for channel, metrics in benchmark.get(bench_name, {}).items():
            for metric, value in metrics.items():
                con.execute(
                    "INSERT INTO semantic_benchmarks VALUES (?,?,?,?,?,?,?)",
                    [model_id, bench_name, channel, metric, float(value), "{}", now],
                )


def benchmark_analysis(config: AppConfig) -> dict[str, Any]:
    db = Database(config.paths.database); db.initialize()
    rows = _fetch_opportunities(db)
    result = benchmark_opportunity_retrieval(rows)
    return result


def rebuild_analysis(config: AppConfig) -> dict[str, Any]:
    db = Database(config.paths.database); db.initialize()
    opportunities = _fetch_opportunities(db)
    if not opportunities:
        return {"documents": 0, "message": "No analytical documents available"}

    profile = config.load_profile()
    queries = profile_queries(profile)
    mc = _model_config(config)
    digest = corpus_hash(opportunities)
    model_id = _model_id(digest, mc, queries)
    now = utcnow()

    # 1) Opportunity relevance: three independently interpretable facets.
    opp_result = score_opportunities(
        opportunities,
        queries,
        word_max_features=int(mc["word_max_features"]),
        char_max_features=int(mc["char_max_features"]),
        lsa_components=int(mc["lsa_components"]),
        rrf_k=int(mc["rrf_k"]),
    )

    # 2) Landscape geometry is project-level, not plan-level.
    projects = project_records(opportunities)
    project_space = FixedSparseSpace(
        word_max_features=int(mc["word_max_features"]),
        char_max_features=int(mc["char_max_features"]),
        lsa_components=int(mc["lsa_components"]),
        min_df=1,
    ).fit([str(p.get("domain") or "vazio") for p in projects])
    project_cos = project_space.cosine_matrix()
    project_coords = pcoa_from_cosine(project_cos)

    # 3) Topic discovery in separate domain/method/skill spaces; project-level fitting.
    topic_results = {}
    for space in ["domain", "methods", "skills"]:
        topic_results[space] = fit_topic_space(
            projects, opportunities, space=space,
            min_k=int(mc["topic_min_k"]), max_k=int(mc["topic_max_k"]),
            min_support=int(mc["topic_min_project_support"]),
            max_features=int(mc["word_max_features"]),
        )

    # Topic shares provide a cleaner basis for discrete cluster membership than
    # raw lexical distance, while PCoA keeps the continuous map independent.
    cluster_cos = cosine_similarity(topic_results["domain"].project_weights)
    project_clusters, cluster_diag = choose_hierarchical_clusters(
        cluster_cos,
        min_k=int(mc["cluster_min_k"]), max_k=int(mc["cluster_max_k"]),
    )
    project_index = {str(p["project_code"]): i for i, p in enumerate(projects)}
    coords_by_opp: dict[str, tuple[float, float]] = {}
    clusters_by_opp: dict[str, int] = {}
    for row in opportunities:
        oi = str(row.get("id_opportunity") or "")
        pi = project_index.get(str(row.get("project_code") or ""))
        if pi is None:
            coords_by_opp[oi] = (0.0, 0.0); clusters_by_opp[oi] = -1
        else:
            coords_by_opp[oi] = (float(project_coords[pi, 0]), float(project_coords[pi, 1]))
            clusters_by_opp[oi] = int(project_clusters[pi])

    # 4) Professor profiles are portfolios of atomic evidence, never mega-documents.
    professor_rows, evidence_rows = _professor_scores(
        db, opportunities, opp_result, queries, coords_by_opp, clusters_by_opp, mc,
    )

    benchmark = benchmark_opportunity_retrieval(opportunities)
    corpus_stats = {
        "opportunities": len(opportunities), "projects": len(projects),
        "professors": len(professor_rows), "cluster_diagnostics": cluster_diag,
        "topic_k": {k: v.n_topics for k, v in topic_results.items()},
    }

    # v2 opportunity score rows.
    v2_opp_rows: list[dict[str, Any]] = []
    for i, row in enumerate(opportunities):
        oid = str(row.get("id_opportunity") or "")
        x, y = coords_by_opp[oid]
        v2_opp_rows.append({
            "entity_type": "opportunity", "entity_id": oid,
            "domain_word": float(opp_result.domain.word[i]), "domain_char": float(opp_result.domain.char[i]),
            "domain_lsa": float(opp_result.domain.lsa[i]), "domain_bm25f": float(opp_result.domain.bm25f[i]),
            "domain_fit": float(opp_result.domain.fit[i]),
            "method_word": float(opp_result.methods.word[i]), "method_char": float(opp_result.methods.char[i]),
            "method_lsa": float(opp_result.methods.lsa[i]), "method_bm25f": float(opp_result.methods.bm25f[i]),
            "method_fit": float(opp_result.methods.fit[i]),
            "skill_word": float(opp_result.skills.word[i]), "skill_char": float(opp_result.skills.char[i]),
            "skill_lsa": float(opp_result.skills.lsa[i]), "skill_bm25f": float(opp_result.skills.bm25f[i]),
            "skill_fit": float(opp_result.skills.fit[i]), "combined_fit": float(opp_result.combined[i]),
            "cluster_id": int(clusters_by_opp[oid]), "x": x, "y": y,
        })

    all_score_rows = v2_opp_rows + professor_rows
    with db.connect() as con:
        # Keep historical v2 runs, but replace an identical deterministic run.
        for table in ["semantic_entity_scores", "semantic_topics", "semantic_entity_topics", "semantic_professor_evidence", "semantic_benchmarks"]:
            con.execute(f"DELETE FROM {table} WHERE model_id=?", [model_id])
        con.execute("DELETE FROM semantic_runs WHERE model_id=?", [model_id])
        con.execute(
            "INSERT INTO semantic_runs VALUES (?,?,?,?,?,?)",
            [model_id, digest, json_text(mc), json_text(corpus_stats), json_text(benchmark), now],
        )
        con.execute("INSERT OR REPLACE INTO meta VALUES ('current_semantic_model_id',?,?)", [model_id, now])

        for rec in all_score_rows:
            con.execute(
                "INSERT INTO semantic_entity_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    model_id, rec["entity_type"], rec["entity_id"],
                    rec["domain_word"], rec["domain_char"], rec["domain_lsa"], rec["domain_bm25f"], rec["domain_fit"],
                    rec["method_word"], rec["method_char"], rec["method_lsa"], rec["method_bm25f"], rec["method_fit"],
                    rec["skill_word"], rec["skill_char"], rec["skill_lsa"], rec["skill_bm25f"], rec["skill_fit"],
                    rec["combined_fit"], rec["cluster_id"], rec["x"], rec["y"], now,
                ],
            )

        for space, tr in topic_results.items():
            for tid in range(tr.n_topics):
                diag = {"space_diagnostics": tr.diagnostics}
                con.execute(
                    "INSERT INTO semantic_topics VALUES (?,?,?,?,?,?,?)",
                    [model_id, space, tid, tr.labels[tid], json_text(tr.top_terms[tid]), json_text(diag), now],
                )
            for i, project in enumerate(projects):
                pid = str(project["project_code"])
                for tid, weight in enumerate(tr.project_weights[i]):
                    con.execute(
                        "INSERT INTO semantic_entity_topics VALUES (?,?,?,?,?,?,?)",
                        [model_id, "project", pid, space, tid, float(weight), now],
                    )
            for i, row in enumerate(opportunities):
                oid = str(row.get("id_opportunity") or "")
                for tid, weight in enumerate(tr.opportunity_weights[i]):
                    con.execute(
                        "INSERT INTO semantic_entity_topics VALUES (?,?,?,?,?,?,?)",
                        [model_id, "opportunity", oid, space, tid, float(weight), now],
                    )

        # Aggregate topic shares for professors from their opportunity portfolio.
        professor_topics_by_space: dict[str, dict[str, np.ndarray]] = {}
        for space, tr in topic_results.items():
            per_prof_topic_space: dict[str, list[np.ndarray]] = defaultdict(list)
            for i, row in enumerate(opportunities):
                siape = str(row.get("professor_siape") or "")
                if siape:
                    per_prof_topic_space[siape].append(tr.opportunity_weights[i])
            aggregated: dict[str, np.ndarray] = {}
            for siape, mats in per_prof_topic_space.items():
                avg = np.mean(np.vstack(mats), axis=0)
                total = float(avg.sum())
                if total > 0: avg = avg / total
                aggregated[siape] = avg
                for tid, weight in enumerate(avg):
                    con.execute(
                        "INSERT INTO semantic_entity_topics VALUES (?,?,?,?,?,?,?)",
                        [model_id, "professor", siape, space, tid, float(weight), now],
                    )
            professor_topics_by_space[space] = aggregated

        for ev in evidence_rows:
            con.execute(
                "INSERT INTO semantic_professor_evidence VALUES (?,?,?,?,?,?,?,?,?)",
                [model_id, ev["siape"], ev["rank"], ev["item_type"], ev["item_id"], ev["label"], ev["score"], json_text(ev["payload"]), now],
            )
        _persist_benchmarks(con, model_id, benchmark, now)

        # Compatibility layer for the existing dashboard/CLI/campaigns.
        con.execute("DELETE FROM analysis_documents")
        con.execute("DELETE FROM analysis_scores")
        con.execute("DELETE FROM analysis_topics")
        con.execute("DELETE FROM entity_topics")

        bm25_display = _normalize_display(np.asarray(opp_result.domain.bm25f))
        opp_by_id = {str(r.get("id_opportunity") or ""): r for r in opportunities}
        for i, rec in enumerate(v2_opp_rows):
            oid = rec["entity_id"]
            row = opp_by_id[oid]
            views = opportunity_views(row)
            doc = "\n\n".join(x for x in [
                views["domain_title"], views["domain_intro"], views["domain_objectives"],
                views["method_methodology"], views["skill_skills"],
            ] if x)
            con.execute("INSERT INTO analysis_documents VALUES (?,?,?,?,?)", ["opportunity", oid, doc, text_hash(doc), now])
            lexical = (rec["domain_word"] + rec["domain_char"]) / 2.0
            con.execute(
                "INSERT INTO analysis_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
                ["opportunity", oid, lexical, rec["domain_lsa"], float(bm25_display[i]), rec["combined_fit"], rec["cluster_id"], rec["x"], rec["y"], now],
            )

        for rec in professor_rows:
            siape = rec["entity_id"]
            labels = [e["label"] for e in evidence_rows if e["siape"] == siape][:5]
            doc = "\n".join(labels) or siape
            con.execute("INSERT INTO analysis_documents VALUES (?,?,?,?,?)", ["professor", siape, doc, text_hash(doc), now])
            lexical = (rec["domain_word"] + rec["domain_char"]) / 2.0
            con.execute(
                "INSERT INTO analysis_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
                ["professor", siape, lexical, rec["domain_lsa"], 0.0, rec["combined_fit"], rec["cluster_id"], rec["x"], rec["y"], now],
            )

        # Legacy topic tables now mean DOMAIN topics only. Rich spaces live in semantic_*.
        domain = topic_results["domain"]
        for tid in range(domain.n_topics):
            con.execute("INSERT INTO analysis_topics VALUES (?,?,?,?)", [tid, domain.labels[tid], json_text(domain.top_terms[tid]), now])
        for i, row in enumerate(opportunities):
            oid = str(row.get("id_opportunity") or "")
            for tid, weight in enumerate(domain.opportunity_weights[i]):
                con.execute("INSERT INTO entity_topics VALUES (?,?,?,?,?)", ["opportunity", oid, tid, float(weight), now])
        for siape, avg in professor_topics_by_space.get("domain", {}).items():
            for tid, weight in enumerate(avg):
                con.execute("INSERT INTO entity_topics VALUES (?,?,?,?,?)", ["professor", siape, tid, float(weight), now])

    calculate_metrics(db)
    return {
        "engine": SEMANTIC_ENGINE_VERSION,
        "model_id": model_id,
        "opportunities": len(opportunities),
        "projects": len(projects),
        "professors": len(professor_rows),
        "topics": {k: v.n_topics for k, v in topic_results.items()},
        "clusters": cluster_diag.get("selected_k"),
        "benchmark": benchmark,
    }

def _norm_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()


def calculate_metrics(db: Database) -> None:
    now = utcnow()
    with db.connect() as con:
        con.execute("DELETE FROM professor_metrics")
        con.execute("DELETE FROM collaboration_edges")
        profs = con.execute("SELECT siape,canonical_name FROM professors").fetchall()
        known_names = {_norm_name(name or ""): siape for siape, name in profs if name}
        aliases = con.execute("SELECT normalized_alias,siape FROM professor_aliases WHERE COALESCE(siape,'')<>''").fetchall()
        for alias, siape in aliases:
            if alias:
                known_names[alias] = siape

        has_lattes = bool(con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name='sigaa_public_lattes_flat'").fetchone()[0])
        has_pages = bool(con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name='sigaa_public_pages'").fetchone()[0])

        for siape, canonical_name in profs:
            opp = con.execute(
                "SELECT COUNT(*), SUM(CASE WHEN has_funding THEN 1 ELSE 0 END), COALESCE(SUM(funded_slots),0), COUNT(DISTINCT NULLIF(area,'')) FROM opportunities WHERE professor_siape=?",
                [siape],
            ).fetchone()
            fitrow = con.execute("SELECT combined_score FROM analysis_scores WHERE entity_type='professor' AND entity_id=? LIMIT 1", [siape]).fetchone()

            public_count = current_year_count = 0
            if has_pages:
                prow = con.execute("SELECT visible_text FROM sigaa_public_pages WHERE siape=? AND page_type='pesquisa' LIMIT 1", [siape]).fetchone()
                if prow and prow[0]:
                    codes = set(re.findall(r"\b[A-Z]{3,}[0-9]{3,}-[0-9]{4}\b", prow[0]))
                    public_count = len(codes)
                    current_year = str(datetime.now().year)
                    current_year_count = sum(code.endswith('-' + current_year) for code in codes)

            lattes_count = lattes_projects = publications = funders = collaborator_count = 0
            lattes_area_count = 0
            if has_lattes:
                lattes_count = int(con.execute("SELECT COUNT(*) FROM sigaa_public_lattes_flat WHERE siape=?", [siape]).fetchone()[0])
                lattes_projects = int(con.execute(
                    "SELECT COUNT(*) FROM sigaa_public_lattes_flat WHERE siape=? AND value_type='string' AND LOWER(path) LIKE '%nomedoprojeto%' AND COALESCE(value_text,'')<>''",
                    [siape],
                ).fetchone()[0])
                publications = int(con.execute(
                    """SELECT COUNT(*) FROM sigaa_public_lattes_flat
                       WHERE siape=? AND value_type='string' AND LOWER(path) LIKE '%producaobibliografica%'
                         AND (LOWER(path) LIKE '%titulodoartigo%' OR LOWER(path) LIKE '%titulodotrabalho%' OR LOWER(path) LIKE '%titulodolivro%' OR LOWER(path) LIKE '%titulodocapitulo%')
                         AND COALESCE(value_text,'')<>''""",
                    [siape],
                ).fetchone()[0])
                funders = int(con.execute(
                    """SELECT COUNT(DISTINCT value_text) FROM sigaa_public_lattes_flat
                       WHERE siape=? AND value_type='string' AND LOWER(path) LIKE '%financiadoresdoprojeto%'
                         AND LOWER(path) LIKE '%nomeinstituicao%' AND COALESCE(value_text,'')<>''""",
                    [siape],
                ).fetchone()[0])
                lattes_area_count = int(con.execute(
                    """SELECT COUNT(DISTINCT value_text) FROM sigaa_public_lattes_flat
                       WHERE siape=? AND value_type='string' AND LOWER(path) LIKE '%areasdoconhecimento%'
                         AND LOWER(path) LIKE '%nomedaareadoconhecimento%' AND COALESCE(value_text,'')<>''""",
                    [siape],
                ).fetchone()[0])
                collab_rows = con.execute(
                    """SELECT value_text,COUNT(*) n FROM sigaa_public_lattes_flat
                       WHERE siape=? AND value_type='string' AND LOWER(path) LIKE '%equipedoprojeto%'
                         AND LOWER(path) LIKE '%nomecompleto%' AND COALESCE(value_text,'')<>''
                       GROUP BY value_text""",
                    [siape],
                ).fetchall()
                own_norm = _norm_name(canonical_name or "")
                collabs = [(name, int(weight)) for name, weight in collab_rows if _norm_name(name or "") and _norm_name(name or "") != own_norm]
                collaborator_count = len(collabs)
                for collaborator_name, weight in collabs:
                    target = known_names.get(_norm_name(collaborator_name or ""))
                    con.execute(
                        "INSERT INTO collaboration_edges VALUES (?,?,?,?,?)",
                        [siape, target or "", collaborator_name or "", weight, now],
                    )

            research_area_count = max(int(opp[3] or 0), lattes_area_count)
            con.execute(
                "INSERT OR REPLACE INTO professor_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    siape, int(opp[0] or 0), int(opp[1] or 0), int(opp[2] or 0),
                    current_year_count, public_count, lattes_projects, publications, funders,
                    collaborator_count, lattes_count, research_area_count,
                    float(fitrow[0]) if fitrow else None, now,
                ],
            )

        counts = [r[0] for r in con.execute("SELECT COUNT(*) FROM opportunities GROUP BY COALESCE(professor_siape, professor_name)").fetchall()]
        funded = [r[0] for r in con.execute("SELECT COALESCE(SUM(funded_slots),0) FROM opportunities GROUP BY COALESCE(professor_siape, professor_name)").fetchall()]
        total = int(con.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0])
        funded_plans = int(con.execute("SELECT COUNT(*) FROM opportunities WHERE has_funding").fetchone()[0])
        slots = int(con.execute("SELECT COALESCE(SUM(funded_slots),0) FROM opportunities").fetchone()[0])
        metrics = {
            "opportunities_total": float(total),
            "funded_opportunities": float(funded_plans),
            "funded_slots": float(slots),
            "funded_opportunity_fraction": float(funded_plans / total) if total else 0.0,
            "gini_opportunities_by_professor": gini(counts),
            "hhi_opportunities_by_professor": hhi(counts),
            "gini_funded_slots_by_professor": gini(funded),
            "hhi_funded_slots_by_professor": hhi(funded),
        }
        con.execute("DELETE FROM global_metrics")
        for key, value in metrics.items():
            con.execute("INSERT INTO global_metrics VALUES (?,?,?,?)", [key, value, "{}", now])

