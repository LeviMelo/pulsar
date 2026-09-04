from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import sync_playwright

from ..config import AppConfig, env_secret
from ..db import Database, utcnow
from .ledger import parse_slots, resolve_opportunity_professors

# IMPORTANT: The selectors, JSF bean calls, navigation order, and Windows-1252
# detail-fetch logic below are deliberately retained from the working legacy
# automation. SIGAA/JSF is fragile; change these only with live regression tests.

DEFAULT_RECORD = {
    "id_oportunidade": "",
    "codigo_projeto": "",
    "titulo_projeto": "",
    "titulo_plano": "",
    "orientador": "",
    "vagas": "",
    "unidade": "",
    "departamento": "",
    "centro_filtro": "",
    "grande_area": "",
    "area": "",
    "edital": "",
    "cota": "",
    "status": "pending",
    "applied_at": "",
    "detalhes_coletados": False,
    "introducao_justificativa": "",
    "objetivos": "",
    "metodologia": "",
    "habilidades_adquiridas": "",
    "referencias": "",
    "has_apply_link": None,
    "has_details_link": None,
}
CSV_COLUMNS = list(DEFAULT_RECORD.keys())


def _ledger_paths(config: AppConfig) -> tuple[Path, Path]:
    return config.paths.opportunity_ledger_json, config.paths.opportunity_ledger_csv


def load_ledger_from_db(db: Database) -> dict[str, dict[str, Any]]:
    ledger: dict[str, dict[str, Any]] = {}
    if not db.path.exists():
        return ledger
    with db.connect(read_only=True) as con:
        try:
            rows = con.execute("SELECT * FROM opportunities").fetchdf().to_dict("records")
        except Exception:
            return ledger
    for r in rows:
        op_id = str(r.get("id_opportunity") or "")
        if not op_id:
            continue
        item = dict(DEFAULT_RECORD)
        item.update({
            "id_oportunidade": op_id,
            "codigo_projeto": r.get("project_code") or "",
            "titulo_projeto": r.get("project_title") or "",
            "titulo_plano": r.get("plan_title") or "",
            "orientador": r.get("professor_name") or "",
            "vagas": r.get("vacancies_text") or "",
            "unidade": r.get("unit") or "",
            "departamento": r.get("department") or "",
            "centro_filtro": r.get("center") or "",
            "grande_area": r.get("large_area") or "",
            "area": r.get("area") or "",
            "edital": r.get("edital") or "",
            "cota": r.get("quota") or "",
            "status": r.get("status") or "pending",
            "applied_at": r.get("applied_at") or "",
            "detalhes_coletados": bool(r.get("details_collected")),
            "introducao_justificativa": r.get("introduction_justification") or "",
            "objetivos": r.get("objectives") or "",
            "metodologia": r.get("methodology") or "",
            "habilidades_adquiridas": r.get("acquired_skills") or "",
            "referencias": r.get("references_text") or "",
            "has_apply_link": r.get("has_apply_link"),
            "has_details_link": r.get("has_details_link"),
        })
        ledger[op_id] = item
    return ledger


def merge_ledger_file(config: AppConfig, ledger: dict[str, dict[str, Any]]) -> None:
    path = config.paths.opportunity_ledger_json
    if not path.exists():
        return
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    for key, value in stored.items():
        op_id = str(value.get("id_oportunidade") or key)
        merged = dict(DEFAULT_RECORD)
        merged.update(value)
        if op_id in ledger:
            # Existing DB state wins unless the legacy record has missing rich text.
            for k, v in merged.items():
                if not ledger[op_id].get(k) and v:
                    ledger[op_id][k] = v
        else:
            ledger[op_id] = merged


def save_ledger_file(config: AppConfig, ledger: dict[str, dict[str, Any]]) -> None:
    json_path, csv_path = _ledger_paths(config)
    json_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for item in ledger.values():
            writer.writerow({k: item.get(k, "") for k in CSV_COLUMNS})


def persist_ledger(db: Database, ledger: dict[str, dict[str, Any]]) -> None:
    now = utcnow()
    with db.connect() as con:
        for op_id, item in ledger.items():
            if not op_id:
                continue
            slots = parse_slots(item.get("vagas"))
            project_code = item.get("codigo_projeto") or ""
            project_title = item.get("titulo_projeto") or ""
            if project_code:
                con.execute(
                    "INSERT OR REPLACE INTO projects VALUES (?, ?, ?, COALESCE((SELECT first_seen_at FROM projects WHERE project_code=?), ?), ?)",
                    [project_code, project_title, item.get("area") or item.get("grande_area") or "", project_code, now, now],
                )
            prev = con.execute("SELECT discovered_at, professor_siape FROM opportunities WHERE id_opportunity=?", [op_id]).fetchone()
            discovered_at = prev[0] if prev and prev[0] else now
            professor_siape = prev[1] if prev else None
            con.execute(
                """INSERT OR REPLACE INTO opportunities VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    op_id, project_code, project_title, item.get("titulo_plano") or "", professor_siape,
                    item.get("orientador") or "", item.get("vagas") or "", slots, slots > 0,
                    "sigaa_vagas_text", item.get("unidade") or "", item.get("departamento") or "",
                    item.get("centro_filtro") or "", item.get("grande_area") or "", item.get("area") or "",
                    item.get("edital") or "", item.get("cota") or "", item.get("status") or "pending",
                    item.get("has_apply_link"), item.get("has_details_link"), bool(item.get("detalhes_coletados")),
                    item.get("introducao_justificativa") or "", item.get("objetivos") or "",
                    item.get("metodologia") or "", item.get("habilidades_adquiridas") or "",
                    item.get("referencias") or "", discovered_at, now, item.get("applied_at") or "",
                ],
            )
            if item.get("status") in {"applied", "already_subscribed"} or item.get("applied_at"):
                con.execute(
                    "INSERT OR REPLACE INTO applications VALUES (?, ?, ?, ?, ?)",
                    [op_id, item.get("status") or "applied", item.get("status") or "", item.get("applied_at") or "", now],
                )


def bypass_notices_and_enter_portal(page, max_attempts=8):
    for _ in range(max_attempts):
        if page.locator("form#menu\\:form_menu_discente, #portal-docente, #menu-dropdown").count() > 0:
            return True
        continuar_btn = page.locator("input[type='submit'][value*='Continuar'], input[value*='Continuar']")
        if continuar_btn.count() > 0 and continuar_btn.first.is_visible():
            continuar_btn.first.click()
            page.wait_for_load_state("networkidle")
            continue
        page.wait_for_timeout(400)
    return False


def login_sigaa(page, username: str, password: str):
    print("[*] Autenticando no SIGAA...")
    page.goto("https://sigaa.sig.ufal.br/sigaa/verTelaLogin.do")
    page.wait_for_load_state("networkidle")
    page.locator("input[name='user.login']").fill(username)
    page.locator("input[name='user.senha']").fill(password)
    page.locator("input[type='submit'][value='Entrar']").click()
    page.wait_for_load_state("networkidle")
    bypass_notices_and_enter_portal(page)
    print("[+] Autenticado com sucesso.")


def go_to_portal_discente(page):
    page.goto("https://sigaa.sig.ufal.br/sigaa/verPortalDiscente.do")
    page.wait_for_load_state("networkidle")
    bypass_notices_and_enter_portal(page)
    page.wait_for_selector("form#menu\\:form_menu_discente", timeout=25000)
    page.wait_for_function('''() => {
        const form = document.getElementById('menu:form_menu_discente');
        if (!form) return false;
        for (let key in window) {
            if (key.startsWith('menu_form_menu_discente_') && key.endsWith('_menu')) {
                return true;
            }
        }
        return false;
    }''', timeout=20000)


def trigger_menu_action(page, action_bean_call, expected_selector):
    if page.locator("form#menu\\:form_menu_discente").count() == 0:
        go_to_portal_discente(page)
    page.evaluate('''(action) => {
        const form = document.getElementById('menu:form_menu_discente');
        for (let key in window) {
            if (key.startsWith('menu_form_menu_discente_') && key.endsWith('_menu')) {
                form.elements['jscook_action'].value = key + ':A]#{' + action + '}';
                form.submit();
                break;
            }
        }
    }''', action_bean_call)
    page.wait_for_selector(expected_selector, timeout=25000)
    page.wait_for_load_state("networkidle")


def sync_confirmed_subscriptions(page, ledger, save_fn: Callable[[], None]):
    print("\n" + "#"*65)
    print("[*] SINCRONIZANDO: 'Acompanhar Meus Registros de Interesse'...")
    print("#"*65)
    trigger_menu_action(page, 'interessadoBolsa.acompanharInscricoes', "table.listagem, h2:has-text('Minhas inscrições')")
    confirmed_items = page.evaluate('''() => {
        const table = document.querySelector("table.listagem");
        if (!table) return [];
        const rows = table.querySelectorAll("tbody tr");
        const list = [];
        rows.forEach(tr => {
            const tds = tr.querySelectorAll("td");
            if (tds.length < 3) return;
            const titulo = tds[0].innerText.trim().replace(/\\s+/g, ' ');
            const tipo = tds[1].innerText.trim().toUpperCase();
            const situacao = tds[2].innerText.trim().toUpperCase();
            let opId = null;
            const viewLink = tr.querySelector("a[href*='obj.id=']");
            if (viewLink) {
                const m = viewLink.getAttribute("href").match(/obj\\.id=(\\d+)/);
                if (m) opId = m[1];
            }
            if (opId || titulo) list.push({id_oportunidade: opId, titulo_plano: titulo, tipo: tipo, situacao: situacao});
        });
        return list;
    }''')
    if not confirmed_items:
        print("[-] Nenhum registro retornado.")
        return 0
    new_confirmed = 0
    total_pesquisa = 0
    for item in confirmed_items:
        if item["tipo"] != "PESQUISA":
            continue
        total_pesquisa += 1
        op_id = item["id_oportunidade"]
        titulo = item["titulo_plano"]
        matched_key = None
        if op_id and op_id in ledger:
            matched_key = op_id
        else:
            for k, v in ledger.items():
                if v.get("titulo_plano", "").strip().lower() == titulo.lower():
                    matched_key = k
                    break
        if matched_key:
            if ledger[matched_key].get("status") != "applied":
                ledger[matched_key]["status"] = "applied"
                if not ledger[matched_key].get("applied_at"):
                    ledger[matched_key]["applied_at"] = "confirmed_on_sigaa"
                new_confirmed += 1
        elif op_id:
            rec = dict(DEFAULT_RECORD)
            rec.update({"id_oportunidade": op_id, "titulo_plano": titulo, "status": "applied", "applied_at": "confirmed_on_sigaa"})
            ledger[op_id] = rec
            new_confirmed += 1
    save_fn()
    print(f"[✔] Sincronização oficial: {total_pesquisa} registros de PESQUISA; {new_confirmed} atualizados.")
    return total_pesquisa


def backfill_all_missing_details(page, ledger, save_fn: Callable[[], None]):
    to_fetch = [op_id for op_id, item in ledger.items() if op_id and (not item.get("detalhes_coletados") or not item.get("introducao_justificativa"))]
    if not to_fetch:
        print("[+] Todas as fichas técnicas estão completas.")
        return
    print(f"\n[*] BACKFILL DE FICHAS TÉCNICAS (Windows-1252): {len(to_fetch)} pendentes...")
    for count, op_id in enumerate(to_fetch, start=1):
        titulo = ledger[op_id].get("titulo_plano", "")[:50]
        print(f"    [{count}/{len(to_fetch)}] [{op_id}] {titulo}...")
        raw = page.evaluate('''(id) => {
            return fetch('/sigaa/pesquisa/planoTrabalho/wizard.do?dispatch=view&obj.id=' + id, {credentials: 'include'})
            .then(async r => {
                const buf = await r.arrayBuffer();
                const decoder = new TextDecoder('windows-1252');
                const html = decoder.decode(buf);
                const parser = new DOMParser();
                const doc = parser.parseFromString(html, 'text/html');
                const table = doc.querySelector("#formPlanoTrabalho table.formulario, table.formulario");
                if (!table) return null;
                const res = {};
                const rows = table.querySelectorAll("tr");
                let pendingHeader = "";
                rows.forEach(tr => {
                    const th = tr.querySelector("th");
                    const td = tr.querySelector("td");
                    if (th && th.getAttribute("colspan") === "2") { pendingHeader = th.innerText.trim(); return; }
                    if (td && td.getAttribute("colspan") === "2" && pendingHeader) {
                        const p = td.querySelector("p");
                        res[pendingHeader] = p ? p.innerText.trim() : td.innerText.trim();
                        pendingHeader = ""; return;
                    }
                    if (th && td) {
                        const key = th.innerText.replace(/:/g, "").trim();
                        if (key) res[key] = td.innerText.trim();
                    }
                });
                return res;
            }).catch(() => null);
        }''', op_id)
        if raw and (raw.get("Introdução e Justificativa") or raw.get("Objetivos") or raw.get("Projeto de Pesquisa")):
            raw_proj = raw.get("Projeto de Pesquisa", "")
            cod_p, tit_p = raw_proj.split(" - ", 1) if " - " in raw_proj else ("", raw_proj)
            ledger[op_id].update({
                "codigo_projeto": cod_p.strip(), "titulo_projeto": tit_p.strip(),
                "titulo_plano": raw.get("Título", ledger[op_id].get("titulo_plano", "")),
                "orientador": raw.get("Orientador", ledger[op_id].get("orientador", "")),
                "departamento": raw.get("Departamento", ""), "edital": raw.get("Edital", ""),
                "cota": raw.get("Cota", ""), "grande_area": raw.get("Grande Área", ""),
                "area": raw.get("Área", ""), "introducao_justificativa": raw.get("Introdução e Justificativa", ""),
                "objetivos": raw.get("Objetivos", ""), "metodologia": raw.get("Metodologia", ""),
                "habilidades_adquiridas": raw.get("Habilidades Adquiridas", ""),
                "referencias": raw.get("Referências", ""), "detalhes_coletados": True,
            })
            save_fn()
        time.sleep(0.3)


def search_oportunidades(page, target_centro):
    trigger_menu_action(page, 'agregadorBolsas.iniciarBuscar', "input[id='busca:btaoBuscar']")
    nome_exato = target_centro["nome_exato"]
    codigo_valor = target_centro["codigo_valor"]
    page.locator("select[id='busca:tipo']").select_option(value="3")
    page.evaluate('''(args) => {
        const select = document.getElementById('busca:centro');
        const check = document.getElementById('busca:centroCheck');
        if (!select) return;
        let targetIdx = -1;
        for (let i = 0; i < select.options.length; i++) {
            if (select.options[i].text.trim().toUpperCase() === args.nome.toUpperCase() || select.options[i].value === args.codigo) {
                targetIdx = i; break;
            }
        }
        if (targetIdx !== -1) {
            select.selectedIndex = targetIdx;
            if (check) check.checked = true;
            const evt = document.createEvent("HTMLEvents");
            evt.initEvent("change", true, true);
            select.dispatchEvent(evt);
        }
    }''', {"nome": nome_exato, "codigo": codigo_valor})
    page.wait_for_timeout(500)
    page.locator("input[id='busca:btaoBuscar']").click()
    page.wait_for_selector("form#listagemResultado table.listagem", timeout=20000)
    page.wait_for_load_state("networkidle")


def extract_table_projects(page):
    return page.evaluate('''() => {
        const table = document.querySelector("form#listagemResultado table.listagem");
        if (!table) return [];
        const rows = table.querySelectorAll("tbody tr");
        const results = [];
        let currentProf = "";
        let currentVagas = "";
        rows.forEach(tr => {
            const respTd = tr.querySelector("td.responsavelBolsa");
            if (respTd) {
                const parts = (respTd.innerText || "").split(":");
                currentProf = parts[0].trim();
                currentVagas = parts.length > 1 ? parts.slice(1).join(":").trim() : "";
                return;
            }
            if (tr.classList.contains("linhaPar") || tr.classList.contains("linhaImpar")) {
                const tds = tr.querySelectorAll("td");
                if (tds.length < 3) return;
                const titulo = tds[0].innerText.trim();
                const unidade = tds[1].innerText.trim();
                const linkInteresse = tr.querySelector("a[title*='Cadastrar Interesse']");
                const linkDetalhes = tr.querySelector("a[title*='Detalhes']");
                let idOp = null; let idUser = null;
                if (linkInteresse) {
                    const oc = linkInteresse.getAttribute("onclick") || "";
                    const mOp = oc.match(/'idOportunidade':\\s*'(\\d+)'/); if (mOp) idOp = mOp[1];
                    const mUs = oc.match(/'idUsuario':\\s*'(\\d+)'/); if (mUs) idUser = mUs[1];
                }
                if (!idOp && linkDetalhes) {
                    const oc = linkDetalhes.getAttribute("onclick") || "";
                    const mId = oc.match(/'id':\\s*'(\\d+)'/); if (mId) idOp = mId[1];
                }
                if (idOp) results.push({id_oportunidade:idOp,titulo:titulo,unidade:unidade,responsavel:currentProf,vagas:currentVagas,id_usuario:idUser,has_apply_link:Boolean(linkInteresse),has_details_link:Boolean(linkDetalhes)});
            }
        });
        return results;
    }''')


def discover_centro(page, target_centro, ledger, save_fn: Callable[[], None]) -> list[dict[str, Any]]:
    search_oportunidades(page, target_centro)
    projects = extract_table_projects(page)
    for proj in projects:
        op_id = proj["id_oportunidade"]
        rec = ledger.setdefault(op_id, dict(DEFAULT_RECORD))
        rec.update({
            "id_oportunidade": op_id,
            "titulo_plano": proj["titulo"],
            "orientador": proj["responsavel"],
            "vagas": proj["vagas"],
            "unidade": proj["unidade"],
            "centro_filtro": target_centro["sigla"],
            "has_apply_link": proj["has_apply_link"],
            "has_details_link": proj["has_details_link"],
        })
        if rec.get("status") not in {"applied", "already_subscribed"}:
            rec["status"] = "pending" if proj["has_apply_link"] else "closed_or_unavailable"
    save_fn()
    return projects


def apply_one(page, target_centro, op_id: str, ledger, qualifications_text: str, save_fn: Callable[[], None]) -> str:
    search_oportunidades(page, target_centro)
    link_interesse = page.locator(f"form#listagemResultado a[title*='Cadastrar Interesse'][onclick*=\"'idOportunidade':'{op_id}'\"]")
    if link_interesse.count() == 0:
        link_interesse = page.locator(f"form#listagemResultado a[title*='Cadastrar Interesse'][onclick*='{op_id}']")
    if link_interesse.count() == 0 or not link_interesse.first.is_visible():
        ledger.setdefault(op_id, dict(DEFAULT_RECORD))["status"] = "closed_or_unavailable"
        save_fn(); return "closed_or_unavailable"
    link_interesse.first.click()
    page.wait_for_load_state("networkidle")
    if page.locator("text='Você não pode realizar a inscrição mais de uma vez na mesma bolsa'").count() > 0:
        ledger[op_id]["status"] = "already_subscribed"; save_fn(); return "already_subscribed"
    if page.locator("textarea[id='form:qualificacoes'], textarea[name='form:qualificacoes']").count() > 0:
        qualif = page.locator("textarea[id='form:qualificacoes'], textarea[name='form:qualificacoes']").first
        qualif.fill(qualifications_text)
        page.locator("input[id='form:inscreverse']").click()
        page.wait_for_load_state("networkidle")
        if page.locator("text='Sua inscrição foi realizada com sucesso'").count() > 0 or "discente.jsf" in page.url:
            ledger[op_id]["status"] = "applied"
            ledger[op_id]["applied_at"] = datetime.now().isoformat()
            save_fn(); return "applied"
        ledger[op_id]["status"] = "already_subscribed"; save_fn(); return "already_subscribed"
    ledger[op_id]["status"] = "already_subscribed"; save_fn(); return "already_subscribed"


def _centers(config: AppConfig) -> list[dict[str, str]]:
    return [
        {"nome_exato": c["name"], "codigo_valor": str(c["code"]), "sigla": c["slug"]}
        for c in config.sigaa.get("centers", [])
    ]


def _credentials(config: AppConfig) -> tuple[str, str]:
    """Read credentials from the environment variables named in config."""
    sigaa = config.sigaa
    return (env_secret(sigaa.get("username_env", "UFAL_SIGAA_USERNAME")),
            env_secret(sigaa.get("password_env", "UFAL_SIGAA_PASSWORD")))


def _open_browser(config: AppConfig):
    headless = bool(config.sigaa.get("headless", False))
    return sync_playwright(), headless


def sync_opportunities(config: AppConfig) -> dict[str, Any]:
    db = Database(config.paths.database); db.initialize()
    ledger = load_ledger_from_db(db); merge_ledger_file(config, ledger)
    username, password = _credentials(config)
    def save():
        # Preserve the proven ledger-style checkpoint cheaply inside fragile browser loops.
        # DuckDB is materialized at operation boundaries instead of rewriting the full DB per row.
        save_ledger_file(config, ledger)
    pctx, headless = _open_browser(config)
    with pctx as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(); page = context.new_page(); page.on("dialog", lambda dialog: dialog.accept())
        try:
            login_sigaa(page, username, password)
            sync_confirmed_subscriptions(page, ledger, save)
            discovered = 0
            for center in _centers(config):
                projects = discover_centro(page, center, ledger, save)
                discovered += len(projects)
            backfill_all_missing_details(page, ledger, save)
            sync_confirmed_subscriptions(page, ledger, save)
        finally:
            browser.close()
    save(); persist_ledger(db, ledger)
    resolved = resolve_opportunity_professors(db)
    return {"opportunities_seen": discovered, "ledger_size": len(ledger), "resolved_professor_links": resolved}


def sync_applications(config: AppConfig) -> dict[str, Any]:
    db = Database(config.paths.database); db.initialize()
    ledger = load_ledger_from_db(db); merge_ledger_file(config, ledger)
    username, password = _credentials(config)
    def save():
        # Preserve the proven ledger-style checkpoint cheaply inside fragile browser loops.
        # DuckDB is materialized at operation boundaries instead of rewriting the full DB per row.
        save_ledger_file(config, ledger)
    pctx, headless = _open_browser(config)
    with pctx as p:
        browser = p.chromium.launch(headless=headless); context = browser.new_context(); page = context.new_page(); page.on("dialog", lambda dialog: dialog.accept())
        try:
            login_sigaa(page, username, password)
            total = sync_confirmed_subscriptions(page, ledger, save)
        finally:
            browser.close()
    save(); persist_ledger(db, ledger); return {"research_registrations": total}


def apply_opportunity(config: AppConfig, op_id: str) -> str:
    db = Database(config.paths.database); db.initialize()
    ledger = load_ledger_from_db(db); merge_ledger_file(config, ledger)
    if op_id not in ledger:
        raise KeyError(f"Opportunity not found locally: {op_id}. Run opportunity sync first.")
    center_slug = ledger[op_id].get("centro_filtro") or ""
    center = next((c for c in _centers(config) if c["sigla"] == center_slug), None)
    if not center:
        raise RuntimeError(f"No configured SIGAA center for opportunity {op_id} (center={center_slug!r})")
    username, password = _credentials(config)
    profile = config.load_profile(); qualifications = profile.get("qualifications_text") or ""
    if not qualifications.strip():
        raise RuntimeError("config/profile.yaml has no qualifications_text")
    def save():
        # Preserve the proven ledger-style checkpoint cheaply inside fragile browser loops.
        # DuckDB is materialized at operation boundaries instead of rewriting the full DB per row.
        save_ledger_file(config, ledger)
    pctx, headless = _open_browser(config)
    with pctx as p:
        browser = p.chromium.launch(headless=headless); context = browser.new_context(); page = context.new_page(); page.on("dialog", lambda dialog: dialog.accept())
        try:
            login_sigaa(page, username, password)
            discover_centro(page, center, ledger, save)
            status = apply_one(page, center, op_id, ledger, qualifications, save)
            sync_confirmed_subscriptions(page, ledger, save)
        finally:
            browser.close()
    save(); persist_ledger(db, ledger); return status
