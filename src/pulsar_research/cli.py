"""PULSAR command line.

Command groups follow the product pipeline:

    sync        acquisition (authenticated SIGAA, public SIGAA/Lattes)
    semantics   build the semantic space, run a profile, benchmark, inspect
    opportunities / professors   query the intelligence
    campaign    audience → drafts → review → explicit send
    applications  authoritative state, plus the one explicit mutation

Two operations in this CLI change the outside world — applying to a SIGAA
opportunity and sending a campaign — and both require an explicit `--confirm`.
Everything else is read-only or local.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from uuid import uuid4
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .config import AppConfig
from .db import Database, json_load, json_text, utcnow

app = typer.Typer(help="PULSAR — local research intelligence and opportunity prospecting",
                  no_args_is_help=True)
sync_app = typer.Typer(help="Acquire and normalize source data", no_args_is_help=True)
sem_app = typer.Typer(help="Semantic space, profile runs, benchmarks and search", no_args_is_help=True)
prof_app = typer.Typer(help="Professor intelligence", no_args_is_help=True)
opp_app = typer.Typer(help="Opportunity intelligence", no_args_is_help=True)
campaign_app = typer.Typer(help="Audience, drafts, review and explicit sending", no_args_is_help=True)
applications_app = typer.Typer(help="SIGAA application state and explicit application",
                               no_args_is_help=True)
app.add_typer(sync_app, name="sync")
app.add_typer(sem_app, name="semantics")
app.add_typer(prof_app, name="professors")
app.add_typer(opp_app, name="opportunities")
app.add_typer(campaign_app, name="campaign")
app.add_typer(applications_app, name="applications")

console = Console()


def ctx() -> tuple[AppConfig, Database]:
    config = AppConfig.load()
    config.ensure_dirs()
    db = Database(config.paths.database)
    db.initialize()
    return config, db


def _last_sync_summary(db: Database) -> str:
    """Most recent journal entry per source, so a silent failure is visible."""
    rows = db.query_df(
        "SELECT source, status, finished_at FROM sync_runs r WHERE finished_at = "
        "(SELECT MAX(finished_at) FROM sync_runs x WHERE x.source = r.source) ORDER BY source")
    if not len(rows):
        return "[yellow]never — no acquisition has been journalled[/yellow]"
    parts = []
    for r in rows.itertuples():
        colour = "green" if r.status == "ok" else "red"
        parts.append(f"{r.source}: [{colour}]{r.status}[/{colour}] {str(r.finished_at)[:16]}")
    return " · ".join(parts)


def _smtp_summary(config: AppConfig) -> str:
    """SMTP readiness, split into the three things that are separately missing."""
    smtp = config.smtp
    host = smtp.get("host") or ""
    sender = smtp.get("from_address") or ""
    user = os.getenv(smtp.get("username_env", "PULSAR_SMTP_USER"), "")
    password = os.getenv(smtp.get("password_env", "PULSAR_SMTP_PASSWORD"), "")
    missing = [label for label, present in (
        ("host", host), ("from_address", sender),
        (smtp.get("username_env", "PULSAR_SMTP_USER"), user),
        (smtp.get("password_env", "PULSAR_SMTP_PASSWORD"), password),
    ) if not present]
    if missing:
        return f"[yellow]not ready — missing {', '.join(missing)}[/yellow]"
    # Present is not the same as accepted. Reporting "ready" on the strength of
    # four non-empty strings once sent an operator into a 62-message campaign
    # with a password the relay refuses, so doctor now authenticates for real.
    from .outreach.mailer import SMTPProvider

    ok, detail = SMTPProvider(config).verify_credentials()
    where = f"{sender} via {host}:{smtp.get('port', 587)}"
    if not ok:
        return f"[red]NOT ready[/red] {where} — {detail}"
    return f"[green]ready[/green] {where} — {detail}"


def _sync(db: Database, source: str, operation):
    """Run one acquisition step and journal it into `sync_runs`, either way.

    A crawl that died halfway is the run you most want a record of, so the
    failure path writes its row before re-raising.
    """
    run_id = uuid4().hex[:12]
    started = utcnow()

    def journal(status: str, details: dict) -> None:
        with db.connect() as con:
            con.execute(
                "INSERT INTO sync_runs (run_id, source, started_at, finished_at, status, details_json) "
                "VALUES (?,?,?,?,?,?)",
                [run_id, source, started, utcnow(), status, json_text(details)])

    try:
        result = operation()
    except Exception as exc:
        journal("failed", {"error": f"{type(exc).__name__}: {exc}"})
        raise
    journal("ok", result if isinstance(result, dict) else {"result": str(result)})
    return result


def _print_df(df, *, empty: str = "No rows.") -> None:
    if df is None or not len(df):
        console.print(f"[dim]{empty}[/dim]")
        return
    console.print(df.to_string(index=False))


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------


@app.command()
def init() -> None:
    """Create directories and the DuckDB schema, migrating older stores."""
    # Deliberately not `ctx()`: that already initializes, which would hide this
    # command's own migration report behind an idempotent second pass.
    config = AppConfig.load()
    config.ensure_dirs()
    report = Database(config.paths.database).initialize()
    console.print({"database": str(config.paths.database), **report})


@app.command()
def doctor() -> None:
    """Check configuration, credentials, data freshness and the embedding service."""
    config, db = ctx()
    from .semantics.corpus import load_corpus
    from .semantics.provenance import check_staleness, current_run_id, current_space_id

    counts = db.counts()
    corpus = load_corpus(db)
    stale = check_staleness(db, corpus.fingerprint())
    sigaa = config.sigaa

    table = Table(title="PULSAR doctor", show_lines=False)
    table.add_column("Check", style="bold")
    table.add_column("Value")
    rows = [
        ("Project root", str(config.root)),
        ("Database", str(config.paths.database)),
        ("Counts", json.dumps(counts)),
        ("Corpus", f"{len(corpus.opportunities)} opportunities · {len(corpus.projects)} projects · "
                   f"{len(corpus.professors)} professors · {len(corpus.atoms)} atoms"),
        ("Semantic space", current_space_id(db) or "[yellow]none — run `pulsar semantics build`[/yellow]"),
        ("Profile run", current_run_id(db) or "[yellow]none — run `pulsar semantics profile`[/yellow]"),
        ("Semantics freshness",
         "[green]current[/green]" if not stale.is_stale else f"[red]STALE[/red] — {stale.describe()}"),
        ("SIGAA username env",
         "set" if os.getenv(sigaa.get("username_env", "UFAL_SIGAA_USERNAME")) else "[yellow]missing[/yellow]"),
        ("SIGAA password env",
         "set" if os.getenv(sigaa.get("password_env", "UFAL_SIGAA_PASSWORD")) else "[yellow]missing[/yellow]"),
        ("Last sync", _last_sync_summary(db)),
        ("SMTP", _smtp_summary(config)),
        ("Professor seed CSV",
         str(config.paths.professors_csv) if config.paths.professors_csv.exists() else "[yellow]missing[/yellow]"),
    ]
    try:
        from .semantics.embedding import provider_from_config
        health = provider_from_config(config, db).health()
        rows.append(("Embedding service",
                     f"[green]{health.model} · {health.dimension}d[/green]" if health.ok
                     else f"[yellow]{health.detail}[/yellow]"))
    except Exception as exc:
        rows.append(("Embedding service", f"[yellow]{exc}[/yellow]"))
    for label, value in rows:
        table.add_row(label, str(value))
    console.print(table)


@app.command()
def report(
    out: Optional[Path] = typer.Option(None, "--out", help="Output directory"),
    no_pdf: bool = typer.Option(False, "--no-pdf", help="Write only the HTML"),
) -> None:
    """Generate the analytical report on the corpus and the retrieval evaluation."""
    config, db = ctx()
    from .report import build_report
    result = build_report(config, db, out, pdf=not no_pdf, log=console.print)
    console.print({k: str(v) for k, v in result.items()})


@app.command()
def dashboard(
    port: int = typer.Option(8787, "--port"),
    host: str = typer.Option("127.0.0.1", "--host", help="Loopback only unless you mean it."),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
    window: bool = typer.Option(
        False, "--window",
        help="Draw the console in a native window instead of a browser tab. "
             "Needs the `desktop` extra.",
    ),
    devtools: bool = typer.Option(False, "--devtools", help="Web inspector, with --window."),
) -> None:
    """Launch the local operator console.

    The browser is the default because every screen's state lives in the URL,
    and an address bar is how a view gets copied to someone else. `--window`
    trades that for a frame of its own; the window's View menu hands the current
    URL back to the browser when a link is what is wanted.
    """
    import webbrowser

    from .webapp import serve

    config, db = ctx()
    db.initialize()
    try:
        httpd = serve(config, db, host=host, port=port)
    except OSError as exc:
        console.print(f"[red]Cannot bind {host}:{port}[/] — {exc}")
        raise typer.Exit(1)
    url = f"http://{'localhost' if host == '127.0.0.1' else host}:{port}/"

    if window:
        from .webapp.desktop import DesktopUnavailable, run_window

        console.print(f"[bold]PULSAR console[/] → native window  (also at [cyan]{url}[/])")
        try:
            run_window(httpd, url, storage_path=config.paths.cache_dir / "webview",
                       debug=devtools)
        except DesktopUnavailable as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1)
        finally:
            httpd.server_close()
        console.print("stopped")
        return

    console.print(f"[bold]PULSAR console[/] → [cyan]{url}[/]  (ctrl-c to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        console.print("stopped")
    finally:
        httpd.server_close()


@app.command("import-ledger")
def import_ledger_cmd(path: Optional[Path] = typer.Option(None, "--path")) -> None:
    """Bootstrap DuckDB from an opportunity ledger JSON file."""
    config, db = ctx()
    from .acquisition.ledger import import_ledger_file, resolve_opportunity_professors
    ledger_path = path or config.paths.opportunity_ledger_json
    imported = import_ledger_file(db, ledger_path)
    console.print({"source": str(ledger_path), "opportunities": imported,
                   "resolved_professors": resolve_opportunity_professors(db)})


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


@sync_app.command("opportunities")
def sync_opportunities_cmd() -> None:
    """Authenticated SIGAA discovery and detail backfill. Never applies to anything."""
    config, db = ctx()
    from .acquisition.sigaa_authenticated import sync_opportunities
    console.print(_sync(db, "opportunities", lambda: sync_opportunities(config)))


@sync_app.command("applications")
def sync_applications_cmd() -> None:
    """Synchronize the authoritative 'Meus Registros de Interesse' state."""
    config, db = ctx()
    from .acquisition.sigaa_authenticated import sync_applications
    console.print(_sync(db, "applications", lambda: sync_applications(config)))


@sync_app.command("professors")
def sync_professors_cmd(
    input_csv: Optional[Path] = typer.Option(None, "--input"),
    refresh: bool = typer.Option(False, "--refresh", help="Re-fetch pages already archived"),
    detail_depth: Optional[int] = typer.Option(None, "--detail-depth"),
) -> None:
    """Archive the public professor corpus and import it (includes Lattes)."""
    config, db = ctx()
    from .acquisition.ledger import export_professors_csv_for_scraper
    from .acquisition.sigaa_public import sync_professors
    seed = input_csv or config.paths.professors_csv
    if input_csv is None:
        if int(db.scalar("SELECT COUNT(*) FROM opportunities", default=0)):
            export_professors_csv_for_scraper(db, seed)
        elif not seed.exists():
            raise typer.BadParameter("No opportunities in DuckDB and no professor seed CSV exists")
    console.print(_sync(db, "professors", lambda: sync_professors(
        config, input_csv=seed, refresh=refresh, detail_depth=detail_depth)))


@sync_app.command("all")
def sync_all_cmd(refresh_public: bool = typer.Option(False, "--refresh-public")) -> None:
    """Full pipeline: opportunities → professors → applications → semantics."""
    config, db = ctx()
    from .acquisition.ledger import export_professors_csv_for_scraper, resolve_opportunity_professors
    from .acquisition.sigaa_authenticated import sync_applications, sync_opportunities
    from .acquisition.sigaa_public import sync_professors

    console.rule("1/4 authenticated opportunities")
    console.print(_sync(db, "opportunities", lambda: sync_opportunities(config)))
    export_professors_csv_for_scraper(db, config.paths.professors_csv)
    console.rule("2/4 public professor corpus")
    console.print(_sync(db, "professors", lambda: sync_professors(
        config, input_csv=config.paths.professors_csv, refresh=refresh_public)))
    resolve_opportunity_professors(db)
    console.rule("3/4 applications")
    console.print(_sync(db, "applications", lambda: sync_applications(config)))
    console.rule("4/4 semantics")
    semantics_build()


# ---------------------------------------------------------------------------
# semantics
# ---------------------------------------------------------------------------


@sem_app.command("build")
def semantics_build(
    no_benchmark: bool = typer.Option(False, "--no-benchmark", help="Skip the regression battery"),
    no_neural: bool = typer.Option(False, "--no-neural", help="Skip pretrained embeddings"),
    no_profile: bool = typer.Option(False, "--no-profile", help="Build the space but do not score"),
) -> None:
    """Fit the semantic space, then score entities against the operator profile."""
    config, db = ctx()
    from .intelligence.metrics import calculate_metrics
    from .semantics.engine import build_space, run_profile

    if no_neural:
        config.raw.setdefault("semantics", {})["use_neural"] = False
    space = build_space(config, db, benchmark=not no_benchmark, log=console.print)
    if not no_profile:
        run_profile(config, db, space, log=console.print)
    metrics = calculate_metrics(db, space.corpus)
    console.print({"space_id": space.space_id, **{k: round(v, 4) for k, v in metrics.items()}})


@sem_app.command("profile")
def semantics_profile() -> None:
    """Re-score entities against config/profile.yaml without refitting the space."""
    config, db = ctx()
    from .semantics.engine import SemanticSpace, run_profile
    from .semantics.provenance import current_space_id
    space_id = current_space_id(db)
    if not space_id:
        raise typer.BadParameter("No semantic space. Run `pulsar semantics build` first.")
    space = SemanticSpace.load(config, db, space_id)
    console.print(run_profile(config, db, space, log=console.print))


@sem_app.command("status")
def semantics_status() -> None:
    """Show the active space, run, benchmarks and map fidelity."""
    config, db = ctx()
    from .semantics.corpus import load_corpus
    from .semantics.provenance import check_staleness, current_run_id, current_space_id
    space_id = current_space_id(db)
    corpus = load_corpus(db)
    state = check_staleness(db, corpus.fingerprint())
    console.print(f"space  [bold]{space_id or '(none)'}[/bold]")
    console.print(f"run    [bold]{current_run_id(db) or '(none)'}[/bold]")
    console.print(("[green]" if not state.is_stale else "[red]") + state.describe() + "[/]")
    if not space_id:
        return
    console.rule("benchmarks (MRR)")
    _print_df(db.query_df(
        "SELECT benchmark, channel, ROUND(value,4) AS mrr FROM semantic_benchmarks "
        "WHERE space_id=? AND metric='mrr' ORDER BY benchmark, mrr DESC", [space_id]))
    console.rule("map fidelity")
    _print_df(db.query_df(
        "SELECT metric, ROUND(value,4) AS value FROM map_diagnostics WHERE space_id=? ORDER BY metric",
        [space_id]))
    console.rule("topics")
    _print_df(db.query_df(
        "SELECT facet, depth, topic_id, label FROM semantic_topics WHERE space_id=? "
        "ORDER BY facet, topic_id", [space_id]))


@sem_app.command("benchmark")
def semantics_benchmark(
    metric: str = typer.Option("mrr", "--metric"),
    latent: str = typer.Option("", "--latent", help="Compare an alternative latent model: lsa|ppmi"),
) -> None:
    """Run the regression battery over every channel of the current space."""
    config, db = ctx()
    from .semantics.benchmark import BM25Candidate, RepresentationCandidate, build_retrieval_tasks, run_benchmarks
    from .semantics.engine import SemanticSpace
    from .semantics.provenance import current_space_id
    space_id = current_space_id(db)
    if not space_id:
        raise typer.BadParameter("No semantic space. Run `pulsar semantics build` first.")
    space = SemanticSpace.load(config, db, space_id)
    candidates = {name: RepresentationCandidate(rep, name) for name, rep in space.representations.items()}
    candidates["bm25"] = BM25Candidate()
    if latent:
        from .semantics.representations import LSARepresentation, PPMIRepresentation
        training = space.corpus.training_documents()
        cfg = space.config
        extra = (LSARepresentation(n_components=cfg["lsa_components"], name=f"lsa{cfg['lsa_components']}")
                 if latent == "lsa" else
                 PPMIRepresentation(n_components=cfg["ppmi_components"], name="ppmi_alt"))
        extra.fit(training)
        candidates[extra.name] = RepresentationCandidate(extra, extra.name)
    report = run_benchmarks(build_retrieval_tasks(space.corpus), candidates)
    console.print(report.table(metric))
    console.print("\n[bold]best per task[/bold]: " + json.dumps(report.best_per_task(metric)))


@sem_app.command("search")
def semantics_search(
    query: str,
    entity: str = typer.Option("opportunity", "--entity", help="opportunity | project | professor"),
    facet: str = typer.Option("domain", "--facet", help="domain | methods | skills"),
    channel: str = typer.Option("", "--channel", help="Restrict to one channel"),
    limit: int = typer.Option(15, "--limit"),
) -> None:
    """Ad-hoc semantic retrieval against the fitted space (query never refits it)."""
    import numpy as np
    import pandas as pd

    config, db = ctx()
    from .semantics.engine import SemanticSpace
    from .semantics.provenance import current_space_id
    space = SemanticSpace.load(config, db, current_space_id(db))

    if entity == "professor":
        atoms = space.corpus.atoms
        scores = space.score_texts(query, [a.text for a in atoms])
        use = channel or ("fused" if "fused" in scores else "latent")
        by_prof: dict[str, tuple[float, str]] = {}
        for i, atom in enumerate(atoms):
            value = float(scores[use][i])
            if value > by_prof.get(atom.siape, (0.0, ""))[0]:
                by_prof[atom.siape] = (value, f"[{atom.kind}] {atom.label[:70]}")
        names = {str(p["siape"]): p.get("canonical_name") for p in space.corpus.professors}
        df = pd.DataFrame(
            [{"score": round(v, 4), "siape": s, "professor": names.get(s, ""), "best_evidence": label}
             for s, (v, label) in by_prof.items()]
        ).sort_values("score", ascending=False).head(limit)
        _print_df(df)
        return

    texts = space.facet_texts(entity, facet)
    scores = space.score_texts(query, texts)
    ids = space.entity_ids(entity)
    channels = [channel] if channel else list(scores)
    frame = {"entity_id": ids}
    for name in channels:
        frame[name] = np.round(scores[name], 4)
    if entity == "opportunity":
        frame["plan_title"] = [str(r.get("plan_title") or r.get("project_title") or "")[:70]
                               for r in space.corpus.opportunities]
        frame["professor"] = [str(r.get("professor_name") or "")[:32] for r in space.corpus.opportunities]
    else:
        frame["title"] = [str(p.get("project_title") or "")[:70] for p in space.corpus.projects]
    sort_key = channels[0] if channel else ("fused" if "fused" in scores else channels[0])
    _print_df(pd.DataFrame(frame).sort_values(sort_key, ascending=False).head(limit))


@sem_app.command("skills")
def semantics_skills(
    list_all: bool = typer.Option(False, "--list", help="Print the skill taxonomy"),
    discover: bool = typer.Option(False, "--discover", help="Salient terms the taxonomy misses"),
) -> None:
    """Inspect or grow the deterministic skill taxonomy."""
    import pandas as pd
    from .semantics.skills import SKILL_TAXONOMY, discover_candidate_skills

    if list_all or not discover:
        _print_df(pd.DataFrame([
            {"skill_id": s.skill_id, "label": s.label, "category": s.category, "generic": s.generic}
            for s in SKILL_TAXONOMY
        ]))
    if discover:
        _, db = ctx()
        from .semantics.corpus import load_corpus
        corpus = load_corpus(db)
        texts = [str(r.get("acquired_skills") or "") + " " + str(r.get("methodology") or "")
                 for r in corpus.opportunities]
        console.rule("candidate skill terms not covered by the taxonomy")
        _print_df(pd.DataFrame(discover_candidate_skills(texts), columns=["term", "documents"]))


@sem_app.command("embeddings")
def semantics_embeddings(
    check: bool = typer.Option(False, "--check", help="Health-check the embedding service"),
    stats: bool = typer.Option(False, "--stats", help="Show the local embedding cache"),
    clear: bool = typer.Option(False, "--clear", help="Delete cached vectors for the active provider"),
) -> None:
    """Manage the local embedding provider and its cache."""
    import pandas as pd
    config, db = ctx()
    from .semantics.embedding import EmbeddingCache, provider_from_config
    provider = provider_from_config(config, db)
    if clear:
        removed = EmbeddingCache(db).clear(provider.provider_key)
        console.print({"cleared_vectors": removed})
        return
    if stats or not check:
        _print_df(pd.DataFrame(EmbeddingCache(db).stats()), empty="Embedding cache is empty.")
    if check or not stats:
        console.print(provider.health())


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------


@opp_app.command("list")
def opportunity_list(
    funded: bool = typer.Option(False, "--funded"),
    center: Optional[str] = typer.Option(None, "--center"),
    facet: str = typer.Option("overall", "--facet"),
    limit: int = typer.Option(30, "--limit"),
) -> None:
    """Rank opportunities by the current profile run."""
    _, db = ctx()
    from .semantics.provenance import current_run_id
    clauses, params = ["1=1"], [current_run_id(db), facet]
    if funded:
        clauses.append("o.has_funding")
    if center:
        clauses.append("o.center=?")
        params.append(center)
    params.append(limit)
    _print_df(db.query_df(
        f"""
        SELECT ROUND(s.percentile,1) AS pct, o.id_opportunity, o.plan_title, o.professor_name,
               o.center, o.funded_slots, COALESCE(a.status,'-') AS application
        FROM opportunities o
        LEFT JOIN applications a USING (id_opportunity)
        LEFT JOIN entity_scores s ON s.entity_type='opportunity' AND s.entity_id=o.id_opportunity
                                 AND s.run_id=? AND s.facet=? AND s.channel='fused'
        WHERE {' AND '.join(clauses)}
        ORDER BY s.percentile DESC NULLS LAST, o.funded_slots DESC
        LIMIT ?
        """, params))


@opp_app.command("show")
def opportunity_show(opportunity_id: str) -> None:
    """Full record, semantic channels, topics and extracted skills."""
    _, db = ctx()
    from .semantics.provenance import current_run_id, current_space_id
    _print_df(db.query_df("SELECT * FROM opportunities WHERE id_opportunity=?", [opportunity_id]).T
              .reset_index().rename(columns={"index": "field", 0: "value"}))
    console.rule("semantic channels")
    _print_df(db.query_df(
        "SELECT facet, channel, ROUND(score,4) AS score, ROUND(percentile,1) AS pct "
        "FROM entity_scores WHERE run_id=? AND entity_type='opportunity' AND entity_id=? "
        "ORDER BY facet, channel", [current_run_id(db), opportunity_id]))
    console.rule("skills")
    _print_df(db.query_df(
        "SELECT label, category, generic, mentions FROM entity_skills "
        "WHERE space_id=? AND entity_type='opportunity' AND entity_id=? ORDER BY generic, mentions DESC",
        [current_space_id(db), opportunity_id]))


@prof_app.command("list")
def professor_list(
    scope: str = typer.Option("current", "--scope", help="current | trajectory"),
    limit: int = typer.Option(30, "--limit"),
) -> None:
    """Rank professors by current-opportunity fit or research-trajectory fit."""
    _, db = ctx()
    from .semantics.provenance import current_run_id
    _print_df(db.query_df(
        """
        SELECT ROUND(s.percentile,1) AS pct, p.siape, p.canonical_name, p.department,
               COALESCE(m.funded_opportunity_count,0) AS funded,
               COALESCE(m.funded_slots,0) AS slots,
               COALESCE(m.publication_count,0) AS pubs,
               COALESCE(p.email,'') AS email
        FROM professors p
        LEFT JOIN professor_metrics m USING (siape)
        LEFT JOIN entity_scores s ON s.entity_type='professor' AND s.entity_id=p.siape
                                 AND s.run_id=? AND s.facet=? AND s.channel='fused'
        ORDER BY s.percentile DESC NULLS LAST, p.canonical_name
        LIMIT ?
        """, [current_run_id(db), scope, limit]))


@prof_app.command("show")
def professor_show(siape: str) -> None:
    """Portfolio, current opportunities and the evidence behind the ranking."""
    _, db = ctx()
    from .semantics.provenance import current_run_id
    run_id = current_run_id(db)
    _print_df(db.query_df(
        "SELECT siape, canonical_name, department, center, email, lattes_id FROM professors WHERE siape=?",
        [siape]))
    console.rule("current opportunities")
    _print_df(db.query_df(
        "SELECT id_opportunity, plan_title, funded_slots, edital, status FROM opportunities "
        "WHERE professor_siape=? ORDER BY has_funding DESC, funded_slots DESC", [siape]))
    for scope in ("current", "trajectory"):
        console.rule(f"why this professor — {scope}")
        _print_df(db.query_df(
            "SELECT evidence_rank AS n, kind, year, ROUND(score,4) AS score, ROUND(weight,3) AS weight, "
            "SUBSTR(label,1,90) AS evidence FROM professor_evidence "
            "WHERE run_id=? AND siape=? AND scope=? ORDER BY evidence_rank",
            [run_id, siape, scope]))


# ---------------------------------------------------------------------------
# applications  (the only SIGAA mutation)
# ---------------------------------------------------------------------------


@applications_app.command("list")
def applications_list() -> None:
    """Local view of authoritative SIGAA application state."""
    _, db = ctx()
    _print_df(db.query_df(
        "SELECT a.id_opportunity, a.status, a.applied_at, o.plan_title, o.professor_name "
        "FROM applications a LEFT JOIN opportunities o USING (id_opportunity) "
        "ORDER BY a.applied_at DESC NULLS LAST"))


@applications_app.command("apply")
def applications_apply(
    opportunity_id: str,
    confirm: bool = typer.Option(False, "--confirm", help="Required explicit authorization"),
) -> None:
    """Apply to ONE opportunity on SIGAA. Consequential and irreversible."""
    config, db = ctx()
    row = db.query_df(
        "SELECT plan_title, project_title, professor_name, center, funded_slots FROM opportunities "
        "WHERE id_opportunity=?", [opportunity_id])
    if not len(row):
        raise typer.BadParameter(f"Unknown opportunity {opportunity_id}. Sync first.")
    record = row.iloc[0].to_dict()
    console.print(f"[bold]{record['plan_title'] or record['project_title']}[/bold]")
    console.print(f"{record['professor_name']} · {record['center']} · {record['funded_slots']} funded slot(s)")
    if not confirm:
        raise typer.BadParameter(
            "Applying is consequential and cannot be undone from PULSAR. "
            "Review the opportunity, then re-run with --confirm."
        )
    from .acquisition.sigaa_authenticated import apply_opportunity
    console.print(f"Status: [bold]{apply_opportunity(config, opportunity_id)}[/bold]")


# ---------------------------------------------------------------------------
# campaigns
# ---------------------------------------------------------------------------


def _audience_query(**kwargs) -> "object":
    from .outreach.selectors import AudienceQuery
    return AudienceQuery(**{k: v for k, v in kwargs.items() if v is not None})


@campaign_app.command("audience")
def campaign_audience(
    center: list[str] = typer.Option([], "--center"),
    funded: bool = typer.Option(True, "--funded/--any-funding"),
    min_current: Optional[float] = typer.Option(None, "--min-current-percentile"),
    min_opportunity: Optional[float] = typer.Option(None, "--min-opportunity-percentile"),
    skill: list[str] = typer.Option([], "--skill"),
    topic: list[str] = typer.Option([], "--topic"),
    keyword: list[str] = typer.Option([], "--keyword"),
    limit: Optional[int] = typer.Option(None, "--limit"),
) -> None:
    """Preview an audience with its qualifying evidence. Persists nothing."""
    _, db = ctx()
    from .outreach.selectors import select_audience
    query = _audience_query(funded_only=funded, centers=list(center), keywords=list(keyword),
                            skill_ids=list(skill), topic_ids=list(topic),
                            min_current_percentile=min_current,
                            min_opportunity_percentile=min_opportunity, limit=limit,
                            exclude_already_contacted=False)
    rows = select_audience(db, query)
    for r in rows:
        rationale = r["rationale"]
        console.print(f"[bold]{r['professor_name']}[/bold] <{r['email']}> · score {r['selection_score']}")
        console.print(f"  [cyan]{rationale['primary_title'][:88]}[/cyan] "
                      f"({rationale['funded_slots']} slot(s), opp p{rationale['opportunity_percentile']})")
        if rationale["matched_skills"]:
            console.print(f"  skills: {', '.join(rationale['matched_skills'])}")
        for ev in rationale["top_evidence"]:
            console.print(f"  · [{ev['kind']}{'/' + str(ev['year']) if ev['year'] else ''}] {ev['label'][:80]}")
    console.print(f"\n[bold]{len(rows)}[/bold] eligible recipients")


@campaign_app.command("create")
def campaign_create(
    name: str,
    attach: list[Path] = typer.Option([], "--attach", help="Annex a file to every message"),
    center: list[str] = typer.Option([], "--center"),
    funded: bool = typer.Option(True, "--funded/--any-funding"),
    min_current: Optional[float] = typer.Option(None, "--min-current-percentile"),
    min_opportunity: Optional[float] = typer.Option(None, "--min-opportunity-percentile"),
    skill: list[str] = typer.Option([], "--skill"),
    topic: list[str] = typer.Option([], "--topic"),
    keyword: list[str] = typer.Option([], "--keyword"),
    include_contacted: bool = typer.Option(False, "--include-previously-contacted"),
    subject_template: Optional[Path] = typer.Option(None, "--subject-template"),
    body_template: Optional[Path] = typer.Option(None, "--body-template"),
    limit: Optional[int] = typer.Option(None, "--limit"),
    allow_stale: bool = typer.Option(False, "--allow-stale"),
) -> None:
    """Freeze an audience and generate one editable draft per recipient."""
    config, db = ctx()
    from .outreach.campaigns import create_campaign
    from .semantics.corpus import load_corpus
    query = _audience_query(funded_only=funded, centers=list(center), keywords=list(keyword),
                            skill_ids=list(skill), topic_ids=list(topic),
                            min_current_percentile=min_current,
                            min_opportunity_percentile=min_opportunity,
                            exclude_already_contacted=not include_contacted, limit=limit)
    for path in attach:
        if not path.is_file():
            raise typer.BadParameter(f"attachment not found: {path}")
    campaign_id, n = create_campaign(
        config, db, name, query, attachments=list(attach),
        subject_template=subject_template.read_text(encoding="utf-8") if subject_template else None,
        body_template=body_template.read_text(encoding="utf-8") if body_template else None,
        corpus_fingerprint=load_corpus(db).fingerprint(), allow_stale=allow_stale,
    )
    console.print(f"Created [bold]{campaign_id}[/bold] with {n} personalized drafts")


@campaign_app.command("list")
def campaign_list() -> None:
    """List every campaign with its status and recipient counts."""
    _, db = ctx()
    _print_df(db.query_df(
        "SELECT c.campaign_id, c.name, c.status, "
        "(SELECT COUNT(*) FROM campaign_recipients r WHERE r.campaign_id=c.campaign_id) AS recipients, "
        "(SELECT COUNT(*) FROM campaign_messages m WHERE m.campaign_id=c.campaign_id AND m.status='sent') AS sent, "
        "c.created_at FROM campaigns c ORDER BY c.created_at DESC"), empty="No campaigns yet.")


@campaign_app.command("show")
def campaign_show(campaign_id: str, limit: int = typer.Option(10, "--limit")) -> None:
    """Summary, provenance, and the first drafts with their qualifying evidence."""
    _, db = ctx()
    from .outreach.campaigns import campaign_rows, campaign_summary
    console.print(campaign_summary(db, campaign_id))
    for row in campaign_rows(db, campaign_id)[:limit]:
        console.rule(f"{row['professor_name']} <{row['email']}> · {row['status']} · "
                     f"{'selected' if row['selected'] else 'DESELECTED'}")
        console.print(f"[cyan]why:[/cyan] {json.dumps(row['rationale'], ensure_ascii=False)}")
        console.print(f"[bold]{row['subject']}[/bold]\n{row['body_text']}")


@campaign_app.command("preview")
def campaign_preview(campaign_id: str, limit: int = typer.Option(25, "--limit")) -> None:
    """Write the rendered HTML drafts to a local file for review."""
    config, db = ctx()
    from .outreach.campaigns import write_preview
    path = write_preview(db, campaign_id, config.paths.exports_dir / f"campaign_{campaign_id}.html",
                         limit=limit)
    console.print(f"Preview written to {path}")


@campaign_app.command("select")
def campaign_select(campaign_id: str, siape: str,
                    selected: bool = typer.Option(True, "--select/--deselect")) -> None:
    """Include or exclude one recipient from sending."""
    _, db = ctx()
    from .outreach.campaigns import update_message
    update_message(db, campaign_id, siape, selected=selected)
    console.print({"campaign_id": campaign_id, "siape": siape, "selected": selected})


@campaign_app.command("edit")
def campaign_edit(
    campaign_id: str, siape: str,
    subject_file: Optional[Path] = typer.Option(None, "--subject-file"),
    body_file: Optional[Path] = typer.Option(None, "--body-file"),
) -> None:
    """Replace one recipient's draft from local files. Other drafts are untouched."""
    config, db = ctx()
    from .outreach.campaigns import regenerate_html, update_message
    update_message(
        db, campaign_id, siape,
        subject=subject_file.read_text(encoding="utf-8").strip() if subject_file else None,
        body_text=body_file.read_text(encoding="utf-8") if body_file else None,
    )
    regenerate_html(config, db, campaign_id, only_uncustomized=False)
    console.print("Saved customized draft")


@campaign_app.command("export")
def campaign_export(campaign_id: str, path: Optional[Path] = typer.Option(None, "--path")) -> None:
    """Write the campaign roster, rationale and drafts to CSV."""
    config, db = ctx()
    from .outreach.campaigns import export_campaign
    out = path or config.paths.exports_dir / f"campaign_{campaign_id}.csv"
    console.print(str(export_campaign(db, campaign_id, out)))


@campaign_app.command("send")
def campaign_send(
    campaign_id: str,
    confirm: bool = typer.Option(False, "--confirm", help="Required explicit authorization"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Send at most N messages"),
) -> None:
    """Send the SELECTED drafts. Without --confirm this is a dry run."""
    config, db = ctx()
    from .outreach.mailer import send_campaign
    result = send_campaign(config, db, campaign_id, confirm=confirm, limit=limit)
    if result.get("dry_run"):
        console.print(f"[yellow]Dry run[/yellow] — would send {result['would_send']} message(s).")
        for r in result["recipients"][:50]:
            console.print(f"  {r['name']} <{r['email']}> · {r['subject'][:70]}")
        console.print("Re-run with --confirm to send.")
    else:
        console.print(result)


if __name__ == "__main__":
    app()
