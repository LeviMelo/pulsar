"""Semantic engine orchestration.

Two verbs, matching the two provenance identities:

``build_space``
    Fit every representation, topic model, skill index, landscape and benchmark
    from the corpus. Expensive, cached on disk, identified by ``space_id``.

``run_profile``
    Score entities against the operator's interests using an existing space.
    Cheap, identified by ``run_id``. Editing a personal interest re-runs this and
    leaves topics and geometry untouched.

Scores are kept in named channels — ``lexical_word``, ``lexical_char``,
``bm25f``, ``latent``, ``neural``, ``fused``, ``skill_match`` — because the
benchmark shows they answer different questions. "Find explicit DATASUS/SIH
projects" is a lexical query; "find work adjacent to quantitative population
health" is a latent one. Collapsing them into one number destroys exactly the
information the operator needs.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from ..config import AppConfig
from ..db import Database, json_text, utcnow
from .benchmark import build_retrieval_tasks, map_fidelity, run_benchmarks, BM25Candidate, RepresentationCandidate
from .corpus import FACET_BM25_WEIGHTS, FACET_FIELDS, SemanticCorpus, load_corpus, opportunity_views
from .evidence import score_portfolios
from .landscape import build_map
from .normalize import clean_text, join_unique
from .provenance import (ProfileRunIdentity, SemanticSpaceIdentity, library_versions,
                         record_run, record_space)
from .representations import (BlockFusion, JointSVDFusion, LSARepresentation,
                              LexicalRepresentation, NeuralRepresentation,
                              PPMIRepresentation, ViewSpec, cosine)
from .retrieval import BM25FIndex, percentile_rank
from .skills import SKILLS_BY_ID, extract_skills, skill_match, skill_profile

FACETS = ("domain", "methods", "skills")
LEXICAL_CHANNELS = ("lexical_word", "lexical_char")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def engine_config(config: AppConfig) -> dict[str, Any]:
    s = config.raw.get("semantics", {})
    return {
        "engine_version": "3.0.0",
        "word_max_features": int(s.get("word_max_features", 40000)),
        "char_max_features": int(s.get("char_max_features", 60000)),
        "word_min_df": int(s.get("word_min_df", 2)),
        "char_min_df": int(s.get("char_min_df", 3)),
        "latent_model": str(s.get("latent_model", "ppmi")),
        "lsa_components": int(s.get("lsa_components", 384)),
        "ppmi_components": int(s.get("ppmi_components", 300)),
        "ppmi_window": int(s.get("ppmi_window", 6)),
        "ppmi_min_count": int(s.get("ppmi_min_count", 4)),
        "ppmi_shift": float(s.get("ppmi_shift", 1.0)),
        "use_neural": bool(s.get("use_neural", True)),
        "neural_dimension": int(s.get("neural_dimension", 0)) or None,
        "fusion": str(s.get("fusion", "block")),
        "fusion_weights": dict(s.get("fusion_weights", {"latent": 1.0, "neural": 1.0, "lexical_word": 0.5})),
        "joint_svd_components": int(s.get("joint_svd_components", 192)),
        "topic_min_k": int(s.get("topic_min_k", 3)),
        "topic_max_k": int(s.get("topic_max_k", 9)),
        "topic_min_support": int(s.get("topic_min_support", 3)),
        "evidence_top_k": int(s.get("evidence_top_k", 3)),
        "evidence_per_scope": int(s.get("evidence_per_scope", 6)),
        "map_refine": bool(s.get("map_refine", True)),
        "overall_weights": dict(s.get("overall_weights", {"domain": 1.0, "methods": 0.6, "skills": 0.4})),
    }


def profile_facet_queries(profile: Mapping[str, Any]) -> dict[str, str]:
    """Turn the operator profile into one query per semantic facet."""
    fallback = clean_text(profile.get("semantic_profile") or "pesquisa científica em saúde")

    def section(*names: str) -> str:
        values: list[str] = []
        for name in names:
            raw = profile.get(name)
            if isinstance(raw, str):
                values.append(raw)
            elif isinstance(raw, (list, tuple)):
                values.extend(str(x) for x in raw if x)
        return clean_text("; ".join(values)) or fallback

    return {
        "domain": section("domain_interests", "domains", "research_interests"),
        "methods": section("method_interests", "methods", "methodology_interests"),
        "skills": section("skill_interests", "technical_skills", "skills"),
        "overall": fallback,
    }


# ---------------------------------------------------------------------------
# Space
# ---------------------------------------------------------------------------


@dataclass
class SemanticSpace:
    space_id: str
    identity: SemanticSpaceIdentity
    config: dict[str, Any]
    corpus: SemanticCorpus
    representations: dict[str, Any] = field(default_factory=dict)
    topics: dict[str, Any] = field(default_factory=dict)
    geometry: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    # -- text views ----------------------------------------------------------

    def facet_texts(self, entity_type: str, facet: str) -> list[str]:
        if entity_type == "opportunity":
            return [
                join_unique(opportunity_views(r)[f] for f in FACET_FIELDS[facet]) or "vazio"
                for r in self.corpus.opportunities
            ]
        if entity_type == "project":
            return [clean_text(p.get(facet)) or "vazio" for p in self.corpus.projects]
        raise ValueError(f"no facet texts for {entity_type}")

    def entity_ids(self, entity_type: str) -> list[str]:
        return {
            "opportunity": self.corpus.opportunity_ids,
            "project": self.corpus.project_codes,
            "professor": self.corpus.siapes,
        }[entity_type]

    def channel_names(self) -> list[str]:
        return [n for n in ("lexical_word", "lexical_char", "latent", "neural", "fused")
                if n in self.representations]

    # -- scoring -------------------------------------------------------------

    def score_texts(self, query: str, texts: Sequence[str]) -> dict[str, np.ndarray]:
        """Per-channel query→text similarity, no fusion applied."""
        out: dict[str, np.ndarray] = {}
        texts = list(texts)
        for name, rep in self.representations.items():
            similarity = getattr(rep, "similarity", None)
            if callable(similarity):  # fusions score per view, never concatenated
                sim = similarity([query], texts)
            else:
                sim = cosine(rep.encode([query]), rep.encode(texts))
            out[name] = np.clip(np.asarray(sim).ravel(), 0.0, 1.0)
        return out

    # -- persistence ---------------------------------------------------------

    def cache_dir(self, config: AppConfig) -> Path:
        return config.paths.data_dir / "cache" / "semantic_spaces" / self.space_id

    def save(self, config: AppConfig) -> Path:
        import joblib
        path = self.cache_dir(config)
        path.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {"representations": self.representations, "topics": self.topics,
             "geometry": self.geometry, "config": self.config,
             "identity": self.identity, "diagnostics": self.diagnostics},
            path / "space.joblib", compress=3,
        )
        (path / "manifest.json").write_text(
            json.dumps({"space_id": self.space_id, "created_at": utcnow(),
                        "config": self.config, "identity": self.identity.payload()},
                       ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, config: AppConfig, db: Database, space_id: str) -> "SemanticSpace":
        import joblib
        path = config.paths.data_dir / "cache" / "semantic_spaces" / space_id / "space.joblib"
        if not path.exists():
            raise FileNotFoundError(
                f"Semantic space {space_id} has no cached artifacts at {path}. "
                f"Run `pulsar semantics build`."
            )
        blob = joblib.load(path)
        return cls(space_id=space_id, identity=blob["identity"], config=blob["config"],
                   corpus=load_corpus(db), representations=blob["representations"],
                   topics=blob["topics"], geometry=blob["geometry"],
                   diagnostics=blob.get("diagnostics", {}))


def _build_representations(cfg: Mapping[str, Any], training: Sequence[str], db: Database,
                           app_config: AppConfig, log) -> tuple[dict[str, Any], dict[str, Any]]:
    reps: dict[str, Any] = {}
    embedding_identity: dict[str, Any] = {}

    log("fitting word TF-IDF")
    word = LexicalRepresentation(analyzer="word", max_features=cfg["word_max_features"],
                                 min_df=cfg["word_min_df"], name="lexical_word").fit(training)
    reps["lexical_word"] = word
    log("fitting character TF-IDF")
    reps["lexical_char"] = LexicalRepresentation(analyzer="char", max_features=cfg["char_max_features"],
                                                 min_df=cfg["char_min_df"], name="lexical_char").fit(training)

    if cfg["latent_model"] == "lsa":
        log(f"fitting LSA (k={cfg['lsa_components']})")
        reps["latent"] = LSARepresentation(n_components=cfg["lsa_components"], base=word,
                                           name="latent").fit(training, base_fitted=True)
    else:
        log(f"fitting SPPMI+SVD (k={cfg['ppmi_components']}, window={cfg['ppmi_window']})")
        reps["latent"] = PPMIRepresentation(
            n_components=cfg["ppmi_components"], window=cfg["ppmi_window"],
            min_count=cfg["ppmi_min_count"], shift=cfg["ppmi_shift"], name="latent",
        ).fit(training)

    if cfg["use_neural"]:
        from .embedding import EmbeddingCache, EmbeddingUnavailable, provider_from_config
        provider = provider_from_config(app_config, db)
        health = provider.health()
        if health.ok:
            truncated = cfg["neural_dimension"]
            log(f"neural embeddings via {health.model} ({health.dimension}d"
                + (f" truncated to {truncated}d)" if truncated else ")"))
            neural = NeuralRepresentation(provider, dimension=truncated, name="neural")
            neural.fit([])
            reps["neural"] = neural
            embedding_identity = provider.identity()
        else:
            log(f"neural embeddings unavailable: {health.detail}")

    fusion = cfg["fusion"]
    if fusion != "none":
        views = {
            name: ViewSpec(reps[name], float(weight))
            for name, weight in cfg["fusion_weights"].items()
            if name in reps and float(weight) > 0
        }
        if len(views) >= 2:
            if fusion == "joint_svd":
                log("fitting joint-SVD consensus embedding")
                reps["fused"] = JointSVDFusion(views, n_components=cfg["joint_svd_components"],
                                               name="fused").fit(training)
            else:
                reps["fused"] = BlockFusion(views, name="fused")
    return reps, embedding_identity


def build_space(
    config: AppConfig,
    db: Database,
    *,
    benchmark: bool = True,
    log=print,
) -> SemanticSpace:
    """Fit and persist a complete semantic space from the current corpus."""
    cfg = engine_config(config)
    log("loading corpus")
    corpus = load_corpus(db)
    training = corpus.training_documents()
    log(f"corpus: {len(corpus.opportunities)} opportunities, {len(corpus.projects)} projects, "
        f"{len(corpus.professors)} professors, {len(corpus.atoms)} atoms, {len(training)} training documents")

    reps, embedding_identity = _build_representations(cfg, training, db, config, log)

    architecture = {"engine": cfg["engine_version"],
                    **{name: rep.signature() for name, rep in reps.items()},
                    "topics": {"min_k": cfg["topic_min_k"], "max_k": cfg["topic_max_k"]}}
    identity = SemanticSpaceIdentity(
        corpus_fingerprint=corpus.fingerprint(),
        architecture=architecture,
        embedding_identity=embedding_identity,
        versions=library_versions(),
    )
    space = SemanticSpace(space_id=identity.space_id, identity=identity, config=cfg, corpus=corpus,
                          representations=reps)

    # -- topics ------------------------------------------------------------
    from .topics import fit_topic_hierarchy
    for facet in ("domain", "methods"):
        log(f"fitting {facet} topic hierarchy")
        space.topics[facet] = fit_topic_hierarchy(
            [clean_text(p.get(facet)) or "vazio" for p in corpus.projects],
            facet=facet, min_k=cfg["topic_min_k"], max_k=cfg["topic_max_k"],
            min_support=cfg["topic_min_support"],
        )
        d = space.topics[facet].diagnostics
        log(f"  k={d.get('selected_k')} npmi={d.get('npmi', 0):.3f} "
            f"stability={d.get('stability', 0):.3f} levels={d.get('levels')}")

    # -- geometry ----------------------------------------------------------
    geometry_rep = reps.get("fused") or reps.get("latent") or reps["lexical_word"]
    log(f"building project landscape from '{getattr(geometry_rep, 'name', '?')}'")
    project_vectors = geometry_rep.encode([p["document"] for p in corpus.projects])
    project_map = build_map(project_vectors, refine=cfg["map_refine"])
    log(f"  method={project_map.diagnostics['method']} stress={project_map.diagnostics['stress']:.3f} "
        f"pearson={project_map.diagnostics['pearson']:.3f}")
    space.geometry["project"] = {
        "coords": project_map.coords,
        "diagnostics": project_map.diagnostics,
        "vectors": project_vectors if not hasattr(project_vectors, "todense") else None,
    }

    # Clusters come from the topic hierarchy, not a separate k-means: the
    # dominant topic path *is* the honest discrete grouping, and inventing a
    # second one only creates two answers to the same question.
    space.geometry["project_cluster"] = list(space.topics["domain"].assignment)

    # -- skills -------------------------------------------------------------
    log("extracting skills")
    space.diagnostics["skills"] = {
        "opportunity": [skill_profile([
            clean_text(r.get("acquired_skills")), clean_text(r.get("methodology"))
        ]) for r in corpus.opportunities],
        "project": [skill_profile([p.get("skills"), p.get("methods")]) for p in corpus.projects],
    }
    atoms_by_prof = corpus.atoms_by_siape()
    space.diagnostics["skills"]["professor"] = [
        skill_profile([a.text for a in atoms_by_prof.get(s, [])]) for s in corpus.siapes
    ]

    # -- benchmarks ---------------------------------------------------------
    bench_rows: list[dict[str, Any]] = []
    if benchmark:
        log("running the semantic regression battery")
        tasks = build_retrieval_tasks(corpus)
        candidates: dict[str, Any] = {name: RepresentationCandidate(rep, name)
                                      for name, rep in reps.items()}
        candidates["bm25"] = BM25Candidate()
        report = run_benchmarks(tasks, candidates)
        bench_rows = report.to_rows()
        space.diagnostics["benchmark"] = report.results
        space.diagnostics["benchmark_notes"] = report.notes
        log("\n" + report.table("mrr"))

    fidelity = map_fidelity(project_vectors, project_map.coords)
    space.diagnostics["map"] = {**project_map.diagnostics, **fidelity}

    stats = {
        "opportunities": len(corpus.opportunities),
        "projects": len(corpus.projects),
        "professors": len(corpus.professors),
        "atoms": len(corpus.atoms),
        "training_documents": len(training),
        "channels": space.channel_names(),
        "topics": {f: m.diagnostics for f, m in space.topics.items()},
        "map": space.diagnostics["map"],
    }
    record_space(db, identity, stats)
    _persist_space(db, space, bench_rows)
    path = space.save(config)
    log(f"semantic space {space.space_id} stored ({path})")
    _prune_space_cache(config, keep=space.space_id)
    return space


def _persist_space(db: Database, space: SemanticSpace, bench_rows: Sequence[Mapping[str, Any]]) -> None:
    now = utcnow()
    sid = space.space_id
    corpus = space.corpus
    with db.connect() as con:
        for table in ("entity_geometry", "semantic_topics", "entity_topics", "entity_skills",
                      "semantic_benchmarks", "map_diagnostics"):
            con.execute(f"DELETE FROM {table} WHERE space_id=?", [sid])

        coords = space.geometry["project"]["coords"]
        clusters = space.geometry["project_cluster"]
        project_xy: dict[str, tuple[float, float, str]] = {}
        for i, project in enumerate(corpus.projects):
            code = str(project["project_code"])
            project_xy[code] = (float(coords[i, 0]), float(coords[i, 1]), str(clusters[i]))
            con.execute("INSERT INTO entity_geometry VALUES (?,?,?,?,?,?,?)",
                        [sid, "project", code, *project_xy[code], now])
        # Opportunities and professors inherit their project's position: the
        # landscape is a map of *research*, and five work plans of one project
        # are one point on it.
        for row in corpus.opportunities:
            x, y, c = project_xy.get(str(row.get("project_code") or ""), (0.0, 0.0, ""))
            con.execute("INSERT INTO entity_geometry VALUES (?,?,?,?,?,?,?)",
                        [sid, "opportunity", str(row.get("id_opportunity") or ""), x, y, c, now])
        by_prof: dict[str, list[tuple[float, float, str]]] = {}
        for project in corpus.projects:
            siape = str(project.get("professor_siape") or "")
            if siape:
                by_prof.setdefault(siape, []).append(project_xy[str(project["project_code"])])
        for siape, points in by_prof.items():
            x = float(np.mean([p[0] for p in points]))
            y = float(np.mean([p[1] for p in points]))
            cluster = max(set(p[2] for p in points), key=lambda c: sum(1 for p in points if p[2] == c))
            con.execute("INSERT INTO entity_geometry VALUES (?,?,?,?,?,?,?)",
                        [sid, "professor", siape, x, y, cluster, now])

        for facet, model in space.topics.items():
            for node in model.nodes:
                con.execute("INSERT INTO semantic_topics VALUES (?,?,?,?,?,?,?,?,?)",
                            [sid, facet, node.topic_id, node.parent_id, node.depth, node.label,
                             json_text(node.terms), json_text(node.diagnostics), now])
            shares = model.root_weights
            for i, project in enumerate(corpus.projects):
                code = str(project["project_code"])
                dominant = model.assignment[i]
                for t in range(shares.shape[1]):
                    tid = f"t{t}"
                    con.execute("INSERT INTO entity_topics VALUES (?,?,?,?,?,?,?,?)",
                                [sid, "project", code, facet, tid, float(shares[i, t]),
                                 dominant.startswith(tid), now])
                if dominant and "." in dominant:
                    con.execute("INSERT INTO entity_topics VALUES (?,?,?,?,?,?,?,?)",
                                [sid, "project", code, facet, dominant, 1.0, True, now])
            opp_shares = model.transform([
                join_unique(opportunity_views(r, topic_clean=True)[f] for f in FACET_FIELDS[facet])
                for r in corpus.opportunities
            ])
            for i, row in enumerate(corpus.opportunities):
                oid = str(row.get("id_opportunity") or "")
                best = int(np.argmax(opp_shares[i])) if opp_shares.shape[1] else 0
                for t in range(opp_shares.shape[1]):
                    con.execute("INSERT INTO entity_topics VALUES (?,?,?,?,?,?,?,?)",
                                [sid, "opportunity", oid, facet, f"t{t}", float(opp_shares[i, t]),
                                 t == best, now])

        skills = space.diagnostics["skills"]
        for entity_type, ids in (("opportunity", corpus.opportunity_ids),
                                 ("project", corpus.project_codes),
                                 ("professor", corpus.siapes)):
            for entity_id, profile in zip(ids, skills[entity_type]):
                for skill_id, mentions in sorted(profile.items()):
                    skill = SKILLS_BY_ID[skill_id]
                    con.execute("INSERT INTO entity_skills VALUES (?,?,?,?,?,?,?,?,?,?)",
                                [sid, entity_type, entity_id, skill_id, skill.label, skill.category,
                                 skill.generic, int(mentions), "", now])

        notes = space.diagnostics.get("benchmark_notes", {})
        for row in bench_rows:
            con.execute("INSERT INTO semantic_benchmarks VALUES (?,?,?,?,?,?,?)",
                        [sid, row["benchmark"], row["channel"], row["metric"], float(row["value"]),
                         notes.get(row["benchmark"], ""), now])
        for metric, value in space.diagnostics["map"].items():
            if isinstance(value, (int, float)):
                con.execute("INSERT INTO map_diagnostics VALUES (?,?,?,?,?)",
                            [sid, "project", metric, float(value), now])


def _prune_space_cache(config: AppConfig, *, keep: str, max_spaces: int = 3) -> None:
    """Keep the most recent cached spaces; older artifacts are regenerable."""
    root = config.paths.data_dir / "cache" / "semantic_spaces"
    if not root.exists():
        return
    dirs = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True)
    for stale in dirs[max_spaces:]:
        if stale.name != keep:
            shutil.rmtree(stale, ignore_errors=True)


# ---------------------------------------------------------------------------
# Profile run
# ---------------------------------------------------------------------------


def run_profile(config: AppConfig, db: Database, space: SemanticSpace, *, log=print) -> dict[str, Any]:
    """Score every entity against the operator's interests within a space."""
    cfg = space.config
    profile = config.load_profile()
    queries = profile_facet_queries(profile)
    operator_skills = [s for s in (profile.get("skill_ids") or []) if s in SKILLS_BY_ID]
    identity = ProfileRunIdentity(
        space_id=space.space_id,
        profile={"queries": queries, "skill_ids": sorted(operator_skills),
                 "overall_weights": cfg["overall_weights"]},
    )
    run_id = identity.run_id
    now = utcnow()
    corpus = space.corpus
    rows: list[tuple] = []

    def emit(entity_type: str, ids: Sequence[str], facet: str, channel: str, values: np.ndarray) -> None:
        pct = percentile_rank(np.asarray(values, dtype=float))
        for i, entity_id in enumerate(ids):
            rows.append((run_id, entity_type, entity_id, facet, channel,
                         float(values[i]), float(pct[i]), now))

    skills_by_entity = space.diagnostics["skills"]

    for entity_type in ("opportunity", "project"):
        ids = space.entity_ids(entity_type)
        log(f"scoring {len(ids)} {_plural(entity_type, len(ids))}")
        per_facet_fused: dict[str, np.ndarray] = {}
        for facet in FACETS:
            texts = space.facet_texts(entity_type, facet)
            for channel, values in space.score_texts(queries[facet], texts).items():
                emit(entity_type, ids, facet, channel, values)
                if channel == "fused" or (channel == "latent" and "fused" not in space.representations):
                    per_facet_fused[facet] = values
            if entity_type == "opportunity":
                records = [opportunity_views(r) for r in corpus.opportunities]
                bm25 = BM25FIndex(FACET_BM25_WEIGHTS[facet]).fit(records).score(queries[facet])
                emit(entity_type, ids, facet, "bm25f", bm25)
        match = np.array([skill_match(p, operator_skills)[0] for p in skills_by_entity[entity_type]])
        emit(entity_type, ids, "skills", "skill_match", match)
        _emit_overall(emit, entity_type, ids, per_facet_fused, cfg["overall_weights"])

    # -- professors ---------------------------------------------------------
    log(f"scoring {len(corpus.professors)} professor portfolios")
    atoms = corpus.atoms
    atom_channels = space.score_texts(queries["overall"], [a.text for a in atoms]) if atoms else {}
    portfolio = score_portfolios(atoms, corpus.siapes, atom_channels,
                                 top_k=cfg["evidence_top_k"],
                                 evidence_per_scope=cfg["evidence_per_scope"])
    for scope, channels in portfolio.scores.items():
        for channel, values in channels.items():
            emit("professor", corpus.siapes, scope, channel, values)

    # Method and skill fit for a professor is only meaningful through the work
    # plans they are actually offering, so it is aggregated from opportunities.
    opp_index = {oid: i for i, oid in enumerate(corpus.opportunity_ids)}
    prof_opps: dict[str, list[int]] = {}
    for i, row in enumerate(corpus.opportunities):
        siape = str(row.get("professor_siape") or "")
        if siape:
            prof_opps.setdefault(siape, []).append(i)
    for facet in ("methods", "skills"):
        texts = space.facet_texts("opportunity", facet)
        opp_scores = space.score_texts(queries[facet], texts)
        for channel, values in opp_scores.items():
            agg = np.zeros(len(corpus.siapes))
            for j, siape in enumerate(corpus.siapes):
                idxs = prof_opps.get(siape, [])
                if idxs:
                    top = sorted((float(values[i]) for i in idxs), reverse=True)[: cfg["evidence_top_k"]]
                    agg[j] = float(np.mean(top))
            emit("professor", corpus.siapes, facet, channel, agg)
    prof_match = np.array([skill_match(p, operator_skills)[0] for p in skills_by_entity["professor"]])
    emit("professor", corpus.siapes, "skills", "skill_match", prof_match)

    with db.connect() as con:
        con.execute("DELETE FROM entity_scores WHERE run_id=?", [run_id])
        con.execute("DELETE FROM professor_evidence WHERE run_id=?", [run_id])
        con.executemany("INSERT INTO entity_scores VALUES (?,?,?,?,?,?,?,?)", rows)
        con.executemany(
            "INSERT INTO professor_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(run_id, e.siape, e.scope, e.rank, e.atom.atom_id, e.atom.kind, e.atom.label,
              e.atom.year, e.score, e.weight, json_text(e.payload()), now)
             for e in portfolio.evidence],
        )
    stats = {"entities_scored": len(rows), "evidence_rows": len(portfolio.evidence),
             "channels": space.channel_names(), "queries": queries,
             "operator_skills": operator_skills}
    record_run(db, identity, stats)
    log(f"profile run {run_id} stored ({len(rows)} score rows)")
    return {"run_id": run_id, "space_id": space.space_id, **stats}


def _plural(word: str, count: int) -> str:
    if count == 1:
        return word
    return word[:-1] + "ies" if word.endswith("y") else word + "s"


def _emit_overall(emit, entity_type: str, ids: Sequence[str],
                  per_facet: Mapping[str, np.ndarray], weights: Mapping[str, float]) -> None:
    """Blend facets in *rank* space, not score space.

    Cosines from different facets are not on a common scale, so averaging them
    would let whichever facet happens to have a wider spread dominate. Averaging
    percentiles is scale-free and honest about being a ranking, not a probability.
    """
    if not per_facet:
        return
    total = sum(float(weights.get(f, 0.0)) for f in per_facet) or 1.0
    blended = np.zeros(len(ids))
    for facet, values in per_facet.items():
        blended += float(weights.get(facet, 0.0)) / total * percentile_rank(np.asarray(values))
    emit(entity_type, ids, "overall", "fused", blended / 100.0)
