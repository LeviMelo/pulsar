from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .analysis.engine import rebuild_analysis
from .analysis.text import quick_query_scores
from .config import AppConfig, discover_root
from .db import Database
from .ingest.legacy import export_professors_csv_for_scraper, import_ledger, import_professors_csv, resolve_opportunity_professors
from .outreach.campaigns import campaign_rows, create_campaign, export_campaign, update_message
from .outreach.mailer import SMTPMailer
from .outreach.selectors import select_audience
from .sigaa.authenticated import apply_opportunity, sync_applications, sync_opportunities
from .sigaa.public import sync_professors

app = typer.Typer(help="UFAL/SIGAA research opportunity intelligence and campaign system", no_args_is_help=True)
sync_app = typer.Typer(help="Synchronize SIGAA/public sources")
analysis_app = typer.Typer(help="Rebuild and inspect intelligence outputs")
campaign_app = typer.Typer(help="Audience selection, personalized drafts, and sending")
prof_app = typer.Typer(help="Professor intelligence")
opp_app = typer.Typer(help="Opportunity intelligence")
applications_app = typer.Typer(help="SIGAA application state and explicit application actions")
app.add_typer(sync_app, name="sync"); app.add_typer(analysis_app, name="analyze"); app.add_typer(campaign_app, name="campaign")
app.add_typer(prof_app, name="professors"); app.add_typer(opp_app, name="opportunities"); app.add_typer(applications_app, name="applications")
console = Console()


def cfg() -> AppConfig:
    c=AppConfig.load(); c.ensure_dirs(); Database(c.paths.database).initialize(); return c

@app.command()
def init():
    """Initialize directories and DuckDB schema in the current project."""
    c=cfg(); console.print(f"[green]Initialized[/green] {c.paths.database}")

@app.command("import-legacy")
def import_legacy(
    ledger: Optional[Path] = typer.Option(None,"--ledger"),
    professors_csv: Optional[Path] = typer.Option(None,"--professors-csv"),
):
    c=cfg(); db=Database(c.paths.database)
    lp=ledger or c.paths.legacy_ledger_json; pp=professors_csv or c.paths.professors_csv
    n1=import_ledger(db,lp) if lp.exists() else 0; n2=import_professors_csv(db,pp) if pp.exists() else 0
    console.print({"ledger_records":n1,"professor_rows":n2})

@sync_app.command("opportunities")
def sync_opps():
    """Authenticated SIGAA discovery + detail backfill. Does NOT apply to anything."""
    console.print(sync_opportunities(cfg()))

@sync_app.command("applications")
def sync_apps():
    """Synchronize authoritative 'Meus Registros de Interesse' state."""
    console.print(sync_applications(cfg()))

@sync_app.command("professors")
def sync_profs(
    input_csv: Optional[Path]=typer.Option(None,"--input"),
    refresh: bool=typer.Option(False,"--refresh"),
    detail_depth: Optional[int]=typer.Option(None,"--detail-depth"),
):
    c=cfg(); db=Database(c.paths.database)
    seed=input_csv or c.paths.professors_csv
    if input_csv is None:
        opp_count = int(db.query_df("SELECT COUNT(*) n FROM opportunities").iloc[0]["n"])
        if opp_count > 0:
            export_professors_csv_for_scraper(db,seed)
        elif not seed.exists():
            raise typer.BadParameter("No opportunities in DuckDB and no professores_ufal.csv seed exists")
    console.print(sync_professors(c,input_csv=seed,refresh=refresh,detail_depth=detail_depth))

@sync_app.command("all")
def sync_all(refresh_public: bool=typer.Option(False,"--refresh-public")):
    c=cfg(); db=Database(c.paths.database)
    console.print("[bold]1/4 authenticated opportunities[/bold]"); console.print(sync_opportunities(c))
    export_professors_csv_for_scraper(db,c.paths.professors_csv)
    console.print("[bold]2/4 public professor corpus[/bold]"); console.print(sync_professors(c,input_csv=c.paths.professors_csv,refresh=refresh_public))
    resolve_opportunity_professors(db)
    console.print("[bold]3/4 applications[/bold]"); console.print(sync_applications(c))
    console.print("[bold]4/4 analysis[/bold]"); console.print(rebuild_analysis(c))

@applications_app.command("apply")
def apply_cmd(opportunity_id: str, confirm: bool=typer.Option(False,"--confirm",help="Required explicit authorization")):
    if not confirm:
        raise typer.BadParameter("Application is consequential. Re-run with --confirm after reviewing the opportunity.")
    status=apply_opportunity(cfg(),opportunity_id); console.print(f"Status: [bold]{status}[/bold]")

@analysis_app.command("rebuild")
def analysis_rebuild():
    console.print(rebuild_analysis(cfg()))


@analysis_app.command("search")
def analysis_search(query:str, entity_type:str=typer.Option("opportunity","--type"), limit:int=typer.Option(20,"--limit")):
    """Ad-hoc sparse semantic search over the current corpus; no persisted embeddings."""
    db=Database(cfg().paths.database)
    df=db.query_df("SELECT entity_id,document_text FROM analysis_documents WHERE entity_type=?",[entity_type])
    if df.empty:
        console.print("No analysis documents. Run `pulsar analyze rebuild` first."); return
    scores=quick_query_scores(df["document_text"].tolist(),query)
    df=df.assign(query_score=scores).sort_values("query_score",ascending=False).head(limit)
    if entity_type=="opportunity":
        meta=db.query_df("SELECT id_opportunity,project_title,plan_title,professor_name FROM opportunities")
        df=df.merge(meta,left_on="entity_id",right_on="id_opportunity",how="left")
        console.print(df[["query_score","entity_id","project_title","plan_title","professor_name"]].to_string(index=False))
    else:
        meta=db.query_df("SELECT siape,canonical_name,department FROM professors")
        df=df.merge(meta,left_on="entity_id",right_on="siape",how="left")
        console.print(df[["query_score","entity_id","canonical_name","department"]].to_string(index=False))

@prof_app.command("list")
def professor_list(limit:int=50):
    db=Database(cfg().paths.database)
    df=db.query_df("""SELECT p.siape,p.canonical_name,p.department,p.email,COALESCE(m.funded_opportunity_count,0) funded,ROUND(COALESCE(m.semantic_fit,0),4) fit FROM professors p LEFT JOIN professor_metrics m USING(siape) ORDER BY fit DESC,p.canonical_name LIMIT ?""",[limit])
    console.print(df.to_string(index=False))

@prof_app.command("show")
def professor_show(siape:str):
    db=Database(cfg().paths.database)
    p=db.query_df("SELECT * FROM professors WHERE siape=?",[siape]); o=db.query_df("SELECT id_opportunity,project_title,plan_title,funded_slots,status FROM opportunities WHERE professor_siape=?",[siape])
    console.print(p.to_string(index=False)); console.print(o.to_string(index=False))

@opp_app.command("list")
def opportunity_list(funded:bool=False,center:Optional[str]=None,limit:int=100):
    db=Database(cfg().paths.database); clauses=["1=1"]; params=[]
    if funded: clauses.append("o.has_funding")
    if center: clauses.append("o.center=?"); params.append(center)
    params.append(limit)
    df=db.query_df(f"SELECT o.id_opportunity,o.project_title,o.plan_title,o.professor_name,o.center,o.funded_slots,o.status,ROUND(COALESCE(s.combined_score,0),4) fit FROM opportunities o LEFT JOIN analysis_scores s ON s.entity_type='opportunity' AND s.entity_id=o.id_opportunity WHERE {' AND '.join(clauses)} ORDER BY fit DESC LIMIT ?",params)
    console.print(df.to_string(index=False))


@campaign_app.command("audience")
def campaign_audience(
    center:list[str]=typer.Option([],"--center"),
    funded:bool=typer.Option(True,"--funded/--any-funding"),
    min_opportunity_fit:Optional[float]=typer.Option(None,"--min-opportunity-fit"),
    min_professor_fit:Optional[float]=typer.Option(None,"--min-professor-fit"),
    keyword:list[str]=typer.Option([],"--keyword"),
    cluster:list[int]=typer.Option([],"--cluster"),
    topic:list[int]=typer.Option([],"--topic"),
    min_topic_weight:float=typer.Option(0.0,"--min-topic-weight"),
    query:Optional[str]=typer.Option(None,"--query",help="Ad-hoc sparse semantic campaign query"),
    min_query_score:Optional[float]=typer.Option(None,"--min-query-score"),
    min_public_projects:Optional[int]=typer.Option(None,"--min-public-projects"),
    min_publications:Optional[int]=typer.Option(None,"--min-publications"),
    min_funders:Optional[int]=typer.Option(None,"--min-funders"),
):
    """Preview a campaign audience without persisting drafts."""
    c=cfg(); db=Database(c.paths.database)
    rows=select_audience(db,funded_only=funded,centers=center,min_opportunity_fit=min_opportunity_fit,min_professor_fit=min_professor_fit,require_email=True,exclude_already_contacted=False,keywords=keyword,clusters=cluster,topic_ids=topic,min_topic_weight=min_topic_weight,semantic_query=query,min_query_score=min_query_score,min_public_projects=min_public_projects,min_publications=min_publications,min_funders=min_funders)
    for r in rows:
        ev='; '.join((x.get('project_title') or x.get('plan_title') or '') for x in r['qualifying_opportunities'])
        console.print(f"[bold]{r['professor_name']}[/bold] <{r['email']}> · score={r['selection_score']:.3f}\n  {ev}")
    console.print(f"\n{len(rows)} eligible recipients")

@campaign_app.command("create")
def campaign_create(
    name:str,
    center:list[str]=typer.Option([],"--center"),
    funded:bool=typer.Option(True,"--funded/--any-funding"),
    min_opportunity_fit:Optional[float]=typer.Option(None,"--min-opportunity-fit"),
    min_professor_fit:Optional[float]=typer.Option(None,"--min-professor-fit"),
    include_previously_contacted:bool=typer.Option(False,"--include-previously-contacted"),
    subject_template_file:Optional[Path]=typer.Option(None,"--subject-template"),
    body_template_file:Optional[Path]=typer.Option(None,"--body-template"),
    keyword:list[str]=typer.Option([],"--keyword"),
    cluster:list[int]=typer.Option([],"--cluster"),
    topic:list[int]=typer.Option([],"--topic"),
    min_topic_weight:float=typer.Option(0.0,"--min-topic-weight"),
    query:Optional[str]=typer.Option(None,"--query",help="Ad-hoc sparse semantic campaign query"),
    min_query_score:Optional[float]=typer.Option(None,"--min-query-score"),
    min_public_projects:Optional[int]=typer.Option(None,"--min-public-projects"),
    min_publications:Optional[int]=typer.Option(None,"--min-publications"),
    min_funders:Optional[int]=typer.Option(None,"--min-funders"),
):
    c=cfg(); subject=subject_template_file.read_text(encoding="utf-8") if subject_template_file else None; body=body_template_file.read_text(encoding="utf-8") if body_template_file else None
    cid,n=create_campaign(c,name,funded_only=funded,centers=center,min_opportunity_fit=min_opportunity_fit,min_professor_fit=min_professor_fit,exclude_already_contacted=not include_previously_contacted,subject_template=subject,body_template=body,keywords=keyword,clusters=cluster,topic_ids=topic,min_topic_weight=min_topic_weight,semantic_query=query,min_query_score=min_query_score,min_public_projects=min_public_projects,min_publications=min_publications,min_funders=min_funders)
    console.print(f"Created [bold]{cid}[/bold] with {n} personalized drafts")

@campaign_app.command("list")
def campaign_list():
    db=Database(cfg().paths.database); console.print(db.query_df("SELECT campaign_id,name,status,created_at,updated_at FROM campaigns ORDER BY created_at DESC").to_string(index=False))

@campaign_app.command("preview")
def campaign_preview(campaign_id:str,limit:int=20):
    db=Database(cfg().paths.database); rows=campaign_rows(db,campaign_id)[:limit]
    for r in rows:
        evidence=r.get("qualifying_evidence",[]); projects="; ".join((e.get("project_title") or e.get("plan_title") or "") for e in evidence)
        console.rule(f"{r['professor_name']} <{r['email']}> · score {r['selection_score']:.3f}")
        console.print(f"[cyan]Qualifies via:[/cyan] {projects}")
        console.print(f"[bold]{r['subject']}[/bold]\n{r['body']}")

@campaign_app.command("edit")
def campaign_edit(campaign_id:str,siape:str,subject_file:Path=typer.Option(...,"--subject-file"),body_file:Path=typer.Option(...,"--body-file")):
    db=Database(cfg().paths.database); update_message(db,campaign_id,siape,subject=subject_file.read_text(encoding="utf-8").strip(),body=body_file.read_text(encoding="utf-8")); console.print("Saved customized draft")

@campaign_app.command("export")
def campaign_export(campaign_id:str,path:Optional[Path]=None):
    c=cfg(); db=Database(c.paths.database); out=path or (c.paths.data_dir/"exports"/f"campaign_{campaign_id}.csv"); console.print(export_campaign(db,campaign_id,out))

@campaign_app.command("send")
def campaign_send(campaign_id:str,confirm:bool=typer.Option(False,"--confirm")):
    if not confirm: raise typer.BadParameter("Sending is disabled without --confirm. Preview and customize drafts first.")
    c=cfg(); result=SMTPMailer(c).send_campaign(Database(c.paths.database),campaign_id); console.print(result)

@app.command()
def dashboard():
    """Launch the local DuckDB-backed Streamlit operator console."""
    c=cfg(); env=os.environ.copy(); env["PULSAR_HOME"]=str(c.root)
    app_path=Path(__file__).resolve().parent/"dashboard"/"app.py"
    raise typer.Exit(subprocess.call([sys.executable,"-m","streamlit","run",str(app_path)],cwd=str(c.root),env=env))

@app.command()
def doctor():
    c=cfg(); db=Database(c.paths.database); db.initialize(); counts=db.counts()
    rows=[("Project root",str(c.root)),("Database",str(c.paths.database)),("Database counts",json.dumps(counts)),("SIGAA username env","set" if os.getenv("UFAL_SIGAA_USERNAME") else "missing"),("SIGAA password env","set" if os.getenv("UFAL_SIGAA_PASSWORD") else "missing"),("SMTP host",c.smtp.get("host") or "missing"),("Public seed CSV",str(c.paths.professors_csv) if c.paths.professors_csv.exists() else "missing")]
    table=Table(title="PULSAR doctor"); table.add_column("Check"); table.add_column("Value")
    for a,b in rows: table.add_row(a,b)
    console.print(table)

if __name__ == "__main__":
    app()
