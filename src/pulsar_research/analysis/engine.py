from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from ..config import AppConfig
from ..db import Database, json_text, utcnow
from .metrics import gini, hhi
from .text import analyze_corpus, clean_text, text_hash


def _opportunity_document(row: dict[str, Any]) -> str:
    parts = [
        f"Projeto: {row.get('project_title') or ''}",
        f"Plano: {row.get('plan_title') or ''}",
        f"Área: {row.get('large_area') or ''} {row.get('area') or ''}",
        f"Justificativa: {row.get('introduction_justification') or ''}",
        f"Objetivos: {row.get('objectives') or ''}",
        f"Metodologia: {row.get('methodology') or ''}",
        f"Habilidades: {row.get('acquired_skills') or ''}",
    ]
    return clean_text(" ".join(p for p in parts if p.strip()))


def build_documents(db: Database, max_professor_chars: int = 120000) -> list[dict[str, str]]:
    docs: list[dict[str, str]] = []
    with db.connect(read_only=True) as con:
        opp_cols = [d[0] for d in con.execute("SELECT * FROM opportunities LIMIT 0").description]
        opp_rows = [dict(zip(opp_cols, r)) for r in con.execute("SELECT * FROM opportunities ORDER BY id_opportunity").fetchall()]
        by_prof: dict[str, list[str]] = defaultdict(list)
        for row in opp_rows:
            text = _opportunity_document(row)
            if text:
                docs.append({"entity_type": "opportunity", "entity_id": str(row["id_opportunity"]), "text": text})
                if row.get("professor_siape"):
                    by_prof[str(row["professor_siape"])].append(text)

        prof_rows = con.execute("SELECT siape,canonical_name,profile_summary FROM professors ORDER BY siape").fetchall()
        for siape, name, summary in prof_rows:
            chunks = [f"Professor: {name or ''}", summary or ""]
            chunks.extend(by_prof.get(str(siape), []))
            # Public SIGAA pages: research, production, extension and teaching are strong signals.
            for table_query in [
                ("sigaa_public_pages", "SELECT visible_text FROM sigaa_public_pages WHERE siape=? AND page_type IN ('pesquisa','producao','extensao','disciplinas')"),
                ("sigaa_public_lattes_flat", "SELECT path,value_text FROM sigaa_public_lattes_flat WHERE siape=? AND value_type='string'"),
            ]:
                table, sql = table_query
                exists = con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name=?", [table]).fetchone()[0]
                if not exists:
                    continue
                rows = con.execute(sql, [siape]).fetchall()
                if table.endswith("lattes_flat"):
                    for path, value in rows:
                        lp = (path or "").lower()
                        if any(k in lp for k in ["resumocv", "projet", "artigo", "producaobibliografica", "areasdoconhecimento", "orienta", "atuacao", "formacao"]):
                            chunks.append(value or "")
                else:
                    chunks.extend((r[0] or "") for r in rows)
            text = clean_text(" ".join(chunks))[:max_professor_chars]
            if text:
                docs.append({"entity_type": "professor", "entity_id": str(siape), "text": text})
    return docs


def rebuild_analysis(config: AppConfig) -> dict[str, Any]:
    db = Database(config.paths.database); db.initialize()
    profile = config.load_profile()
    query = profile.get("semantic_profile") or "pesquisa científica saúde"
    a = config.analysis
    docs = build_documents(db, int(a.get("max_professor_text_chars", 120000)))
    if not docs:
        return {"documents": 0, "message": "No analytical documents available"}
    texts = [d["text"] for d in docs]
    result = analyze_corpus(
        texts, query,
        word_max_features=int(a.get("word_max_features", 18000)),
        char_max_features=int(a.get("char_max_features", 18000)),
        lsa_components=int(a.get("lsa_components", 64)),
        nmf_topics=int(a.get("nmf_topics", 8)),
        clusters=int(a.get("clusters", 8)),
        weight_lsa=float(a.get("weight_lsa", 0.55)),
        weight_tfidf=float(a.get("weight_tfidf", 0.25)),
        weight_bm25=float(a.get("weight_bm25", 0.20)),
    )
    now = utcnow()
    with db.connect() as con:
        con.execute("DELETE FROM analysis_documents")
        con.execute("DELETE FROM analysis_scores")
        con.execute("DELETE FROM analysis_topics")
        con.execute("DELETE FROM entity_topics")
        for i, doc in enumerate(docs):
            con.execute("INSERT INTO analysis_documents VALUES (?,?,?,?,?)", [doc["entity_type"], doc["entity_id"], doc["text"], text_hash(doc["text"]), now])
            x = float(result.coords[i, 0]) if result.coords.shape[1] else 0.0
            y = float(result.coords[i, 1]) if result.coords.shape[1] > 1 else 0.0
            con.execute(
                "INSERT INTO analysis_scores VALUES (?,?,?,?,?,?,?,?,?,?)",
                [doc["entity_type"], doc["entity_id"], float(result.tfidf_similarity[i]), float(result.lsa_similarity[i]), float(result.bm25_similarity[i]), float(result.combined_score[i]), int(result.clusters[i]), x, y, now],
            )
            for topic_id, weight in enumerate(result.topic_weights[i]):
                con.execute("INSERT INTO entity_topics VALUES (?,?,?,?,?)", [doc["entity_type"], doc["entity_id"], topic_id, float(weight), now])
        for topic_id, terms in enumerate(result.topic_terms):
            con.execute("INSERT INTO analysis_topics VALUES (?,?,?,?)", [topic_id, " / ".join(terms[:3]), json_text(terms), now])

    calculate_metrics(db)
    return {"documents": len(docs), "opportunities": sum(d["entity_type"] == "opportunity" for d in docs), "professors": sum(d["entity_type"] == "professor" for d in docs), "topics": len(result.topic_terms)}


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

