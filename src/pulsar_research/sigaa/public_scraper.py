#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UFAL SIGAA public-professor scraper
===================================

Reads ``professores_ufal.csv`` from the SAME DIRECTORY as this script,
resolves each professor to the public SIGAA SIAPE identifier, and archives /
extracts all public data exposed through the seven professor tabs:

    portal.jsf       Perfil Pessoal
    producao.jsf     Produção Intelectual
    disciplinas.jsf Disciplinas Ministradas
    relatorios.jsf  Relatórios de Carga Horária
    pesquisa.jsf    Projetos de Pesquisa
    extensao.jsf    Atividades de Extensão
    monitoria.jsf   Projetos de Monitoria

Important design choices
------------------------
* No login, authentication bypass, SIAPE brute force, CAPTCHA bypass, or private
  endpoint is used. Discovery happens through SIGAA's public faculty search.
* Raw HTTP response bytes are preserved exactly. A normalized UTF-8 copy is also
  written for convenient inspection.
* ``portal.jsf`` may embed a very large ``var curriculo = {...};`` object
  containing a Lattes snapshot. It is extracted losslessly and also normalized
  (HTML entities decoded + conservative mojibake repair) without overwriting the
  original.
* The seven root pages are parsed generically (definition lists, tables, links,
  images, forms, visible text). Direct same-origin detail links found in the
  professor page's main content are crawled at depth 1 by default.
* Professor photographs are downloaded when publicly exposed.
* Every parsed professor receives a compressed JSON document. Query-friendly
  JSONL, DuckDB and Parquet datasets are also produced.
* The crawler is deliberately conservative (single-threaded, rate-limited,
  retry/backoff) because this is a university SIGAA server.

Install dependencies
--------------------
    python -m pip install requests beautifulsoup4 charset-normalizer ftfy json5 duckdb

Run
---
    python scrape_sigaa_ufal.py

Useful options
--------------
    python scrape_sigaa_ufal.py --delay 1.0
    python scrape_sigaa_ufal.py --refresh
    python scrape_sigaa_ufal.py --detail-depth 0
    python scrape_sigaa_ufal.py --detail-depth 1 --max-detail-pages 200

Outputs are created under ``sigaa_ufal_dataset/`` next to the script.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import html as html_lib
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import sys
import time
import traceback
import unicodedata
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

try:
    import requests
    from bs4 import BeautifulSoup, UnicodeDammit
    from charset_normalizer import from_bytes as charset_from_bytes
    import duckdb
    import json5
    import ftfy
except ImportError as exc:  # pragma: no cover
    missing = getattr(exc, "name", str(exc))
    raise SystemExit(
        f"Missing dependency: {missing}\n\n"
        "Install required packages with:\n"
        "  python -m pip install requests beautifulsoup4 charset-normalizer ftfy json5 duckdb\n"
    ) from exc


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_ORIGIN = "https://sigaa.sig.ufal.br"
SIGAA_ROOT = f"{BASE_ORIGIN}/sigaa"
DOCENTE_BASE = f"{SIGAA_ROOT}/public/docente"
SEARCH_URL = f"{DOCENTE_BASE}/busca_docentes.jsf?aba=p-academico"

TAB_ENDPOINTS = {
    "perfil": "portal.jsf",
    "producao": "producao.jsf",
    "disciplinas": "disciplinas.jsf",
    "relatorios": "relatorios.jsf",
    "pesquisa": "pesquisa.jsf",
    "extensao": "extensao.jsf",
    "monitoria": "monitoria.jsf",
}

STATIC_PREFIXES = (
    "/shared/",
    "/sigaa/a4j/",
    "/sigaa/css",
    "/sigaa/cssBundles/",
    "/sigaa/javascript/",
    "/shared/javascript/",
    "/shared/css/",
    "/shared/cssBundles/",
    "/shared/jsBundles/",
    "/sigaa/img/",
    "/sigaa/public/images/",
)

USER_AGENT = (
    "UFAL-SIGAA-public-academic-data-archiver/1.0 "
    "(+public pages only; rate-limited; no authentication)"
)

DATASET_DIRNAME = "sigaa_ufal_dataset"
INPUT_FILENAME = "professores_ufal.csv"

LOG = logging.getLogger("sigaa_scraper")


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_dumps(obj: Any, *, pretty: bool = False) -> str:
    return json.dumps(
        obj,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        default=str,
    )


def write_json(path: Path, obj: Any, *, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dumps(obj, pretty=pretty), encoding="utf-8", newline="\n")


def write_json_gz(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"), default=str)


def safe_filename(text: str, limit: int = 100) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    return (text or "item")[:limit]


def normalize_key(text: Any) -> str:
    """Aggressive accent/case/punctuation normalization for entity matching."""
    if text is None:
        return ""
    text = str(text)
    text = ftfy.fix_text(text)
    text = html_lib.unescape(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(text: Any) -> Any:
    """Normalize decoded text, while callers still retain raw/source values."""
    if not isinstance(text, str):
        return text
    text = html_lib.unescape(text)
    text = ftfy.fix_text(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def normalize_recursive(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): normalize_recursive(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_recursive(v) for v in value]
    if isinstance(value, str):
        return normalize_text(value)
    return value


def flatten_json(value: Any, path: str = "$", depth: int = 0) -> Iterator[tuple[str, str, str]]:
    """Yield (JSONPath-like path, type, scalar-text) for every leaf."""
    if isinstance(value, dict):
        if not value:
            yield path, "object", "{}"
        for key, child in value.items():
            escaped = str(key).replace("\\", "\\\\").replace("'", "\\'")
            yield from flatten_json(child, f"{path}['{escaped}']", depth + 1)
    elif isinstance(value, list):
        if not value:
            yield path, "array", "[]"
        for idx, child in enumerate(value):
            yield from flatten_json(child, f"{path}[{idx}]", depth + 1)
    elif value is None:
        yield path, "null", ""
    elif isinstance(value, bool):
        yield path, "boolean", "true" if value else "false"
    elif isinstance(value, (int, float)):
        yield path, "number", str(value)
    else:
        yield path, "string", str(value)


def canonicalize_url(url: str) -> str:
    """Drop fragments and sort query keys for stable URL deduplication."""
    p = urlparse(url)
    query = parse_qs(p.query, keep_blank_values=True)
    pairs: list[tuple[str, str]] = []
    for key in sorted(query):
        for val in sorted(query[key]):
            pairs.append((key, val))
    return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path, "", urlencode(pairs), ""))


def extension_for_content_type(content_type: str, url: str) -> str:
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    known = {
        "text/html": ".html",
        "application/xhtml+xml": ".html",
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "text/plain": ".txt",
        "application/json": ".json",
    }
    if content_type in known:
        return known[content_type]
    guessed = mimetypes.guess_extension(content_type) if content_type else None
    if guessed:
        return guessed
    suffix = Path(urlparse(url).path).suffix
    return suffix[:10] if suffix else ".bin"


def decode_html_bytes(raw: bytes, content_type: str = "") -> tuple[str, str]:
    """
    Robustly decode server bytes. BeautifulSoup's UnicodeDammit understands HTML
    meta declarations; charset-normalizer is used as a fallback. The source bytes
    are ALWAYS preserved separately, so normalization can never destroy evidence.
    """
    declared = None
    m = re.search(r"charset\s*=\s*['\"]?([^;\s'\"]+)", content_type or "", re.I)
    if m:
        declared = m.group(1).strip()

    candidates = []
    if declared:
        candidates.append(declared)
    candidates.extend(["utf-8", "windows-1252", "iso-8859-1"])

    # Prefer an explicit HTTP charset when it actually decodes.
    for enc in candidates:
        try:
            text = raw.decode(enc, errors="strict")
            if enc.lower() in {"utf-8", "utf8", "utf-8-sig"} or declared:
                return text, enc
        except (UnicodeDecodeError, LookupError):
            pass

    dammit = UnicodeDammit(raw, is_html=True)
    if dammit.unicode_markup is not None:
        return dammit.unicode_markup, dammit.original_encoding or "unknown"

    best = charset_from_bytes(raw).best()
    if best is not None:
        return str(best), best.encoding or "unknown"

    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def read_csv_robust(path: Path) -> tuple[list[dict[str, str]], list[str], str, str]:
    """Read CSV without assuming UTF-8 or comma delimiter."""
    raw = path.read_bytes()
    enc_candidates = ["utf-8-sig", "utf-8"]
    best = charset_from_bytes(raw).best()
    if best and best.encoding:
        enc_candidates.append(best.encoding)
    enc_candidates.extend(["cp1252", "latin-1"])

    decoded = None
    used_encoding = None
    seen = set()
    for enc in enc_candidates:
        if not enc or enc.lower() in seen:
            continue
        seen.add(enc.lower())
        try:
            decoded = raw.decode(enc, errors="strict")
            used_encoding = enc
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if decoded is None:
        decoded = raw.decode("utf-8", errors="replace")
        used_encoding = "utf-8-replace"

    sample = decoded[:65536]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.DictReader(io.StringIO(decoded), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError(f"CSV has no header: {path}")

    fieldnames = [normalize_text(x or "").strip() for x in reader.fieldnames]
    rows: list[dict[str, str]] = []
    for row in reader:
        cleaned: dict[str, str] = {}
        for original_name, normalized_name in zip(reader.fieldnames, fieldnames):
            value = row.get(original_name, "")
            cleaned[normalized_name] = normalize_text(value or "").strip()
        rows.append(cleaned)
    return rows, fieldnames, used_encoding, delimiter


def pick_column(fieldnames: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    norm_map = {normalize_key(name): name for name in fieldnames}
    for cand in candidates:
        if normalize_key(cand) in norm_map:
            return norm_map[normalize_key(cand)]
    return None


def attrs_to_json(tag: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in getattr(tag, "attrs", {}).items():
        if isinstance(val, list):
            out[str(key)] = [normalize_text(str(x)) for x in val]
        else:
            out[str(key)] = normalize_text(str(val))
    return out


# ---------------------------------------------------------------------------
# Logging / JSONL
# ---------------------------------------------------------------------------

class JsonlWriter:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = path.open("w", encoding="utf-8", newline="\n")
        self.count = 0

    def write(self, obj: dict[str, Any]) -> None:
        self.fh.write(json_dumps(obj, pretty=False) + "\n")
        self.count += 1

    def close(self) -> None:
        self.fh.close()

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

@dataclass
class FetchResult:
    url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    fetched_at_utc: str
    from_cache: bool = False
    error: str = ""

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")


class PoliteSession:
    def __init__(self, delay: float, timeout_connect: float, timeout_read: float):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
        self.delay = max(0.0, delay)
        self.timeout = (timeout_connect, timeout_read)
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        wait = self.delay - elapsed
        if wait > 0:
            time.sleep(wait)

    def request(self, method: str, url: str, *, max_attempts: int = 5, **kwargs) -> requests.Response:
        backoff = 2.0
        last_exc: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            self._throttle()
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
                self._last_request = time.monotonic()
                if response.status_code not in {429, 500, 502, 503, 504}:
                    return response
                if attempt == max_attempts:
                    return response
                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    sleep_for = max(backoff, float(retry_after))
                else:
                    sleep_for = backoff
                LOG.warning("HTTP %s for %s; retrying after %.1fs", response.status_code, url, sleep_for)
                time.sleep(sleep_for)
                backoff = min(backoff * 2, 60.0)
            except requests.RequestException as exc:
                self._last_request = time.monotonic()
                last_exc = exc
                if attempt == max_attempts:
                    raise
                LOG.warning("Request error for %s: %s; retrying after %.1fs", url, exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        if last_exc:
            raise last_exc
        raise RuntimeError("unreachable request failure")

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self.request("POST", url, **kwargs)


# ---------------------------------------------------------------------------
# Discovery / SIAPE resolution
# ---------------------------------------------------------------------------

@dataclass
class SearchCandidate:
    siape: str
    name: str
    department: str
    profile_url: str
    photo_url: str


def extract_search_form(html_text: str, base_url: str) -> tuple[str, dict[str, str], list[tuple[str, str]]]:
    soup = BeautifulSoup(html_text, "html.parser")
    form = soup.find("form", id="form") or soup.find("form")
    if form is None:
        raise RuntimeError("Could not find SIGAA faculty search form")

    action = urljoin(base_url, form.get("action") or base_url)
    hidden: dict[str, str] = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        typ = (inp.get("type") or "text").lower()
        if typ == "hidden":
            hidden[name] = inp.get("value") or ""

    options: list[tuple[str, str]] = []
    select = form.find("select", attrs={"name": "form:departamento"}) or form.find("select", id="form:departamento")
    if select:
        for opt in select.find_all("option"):
            value = opt.get("value") or ""
            text = normalize_text(opt.get_text(" ", strip=True))
            if value and value != "0":
                options.append((value, text))
    return action, hidden, options


def parse_search_results(html_text: str, base_url: str) -> list[SearchCandidate]:
    soup = BeautifulSoup(html_text, "html.parser")
    found: list[SearchCandidate] = []
    seen: set[str] = set()
    for a in soup.select("a[href*='portal.jsf'][href*='siape=']"):
        href = urljoin(base_url, a.get("href") or "")
        q = parse_qs(urlparse(href).query)
        siape = (q.get("siape") or [""])[0].strip()
        if not siape or siape in seen:
            continue
        row = a.find_parent("tr")
        if row:
            name_tag = row.select_one("span.nome")
            dep_tag = row.select_one("span.departamento")
            img = row.find("img", src=True)
            name = normalize_text(name_tag.get_text(" ", strip=True) if name_tag else "")
            dep = normalize_text(dep_tag.get_text(" ", strip=True) if dep_tag else "")
            photo = urljoin(base_url, img.get("src")) if img else ""
        else:
            name = normalize_text(a.get_text(" ", strip=True))
            dep = ""
            photo = ""
        found.append(SearchCandidate(siape, name, dep, href, photo))
        seen.add(siape)
    return found


def match_department_option(csv_department: str, options: list[tuple[str, str]]) -> Optional[tuple[str, str]]:
    target = normalize_key(csv_department)
    if not target:
        return None
    exactish = []
    for value, text in options:
        n = normalize_key(text)
        # SIGAA option frequently appends " - Maceió" etc.
        if n == target or n.startswith(target + " ") or target in n:
            exactish.append((value, text))
    if len(exactish) == 1:
        return exactish[0]
    if exactish:
        exactish.sort(key=lambda x: SequenceMatcher(None, target, normalize_key(x[1])).ratio(), reverse=True)
        return exactish[0]
    scored = [
        (SequenceMatcher(None, target, normalize_key(text)).ratio(), value, text)
        for value, text in options
    ]
    scored.sort(reverse=True)
    if scored and scored[0][0] >= 0.80:
        return scored[0][1], scored[0][2]
    return None


def candidate_score(input_name: str, input_department: str, cand: SearchCandidate) -> float:
    n1 = normalize_key(input_name)
    n2 = normalize_key(cand.name)
    name_score = 1.0 if n1 == n2 and n1 else SequenceMatcher(None, n1, n2).ratio()
    d1 = normalize_key(input_department)
    d2 = normalize_key(cand.department)
    if not d1 or not d2:
        dep_score = 0.5
    elif d1 == d2 or d2.startswith(d1) or d1 in d2:
        dep_score = 1.0
    else:
        dep_score = SequenceMatcher(None, d1, d2).ratio()
    return 0.90 * name_score + 0.10 * dep_score


def select_candidate(input_name: str, input_department: str, candidates: list[SearchCandidate]) -> tuple[Optional[SearchCandidate], str, float, list[dict[str, Any]]]:
    ranked = []
    for cand in candidates:
        score = candidate_score(input_name, input_department, cand)
        ranked.append((score, cand))
    ranked.sort(key=lambda x: x[0], reverse=True)

    audit = [
        {
            "siape": c.siape,
            "name": c.name,
            "department": c.department,
            "profile_url": c.profile_url,
            "score": round(score, 6),
        }
        for score, c in ranked[:20]
    ]
    if not ranked:
        return None, "not_found", 0.0, audit

    exact = [
        c for score, c in ranked
        if normalize_key(c.name) == normalize_key(input_name)
        and (
            not input_department
            or not c.department
            or normalize_key(input_department) in normalize_key(c.department)
            or normalize_key(c.department) in normalize_key(input_department)
        )
    ]
    if len(exact) == 1:
        return exact[0], "exact", 1.0, audit
    if len(exact) > 1:
        return None, "ambiguous_exact", 1.0, audit

    best_score, best = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score >= 0.92 and (best_score - second_score >= 0.03 or best_score >= 0.985):
        return best, "fuzzy", best_score, audit
    return None, "ambiguous_or_low_score", best_score, audit


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------

def extract_curriculo_object(html_text: str) -> tuple[Optional[str], Optional[Any], Optional[str]]:
    """Return raw object text, parsed object, parse error."""
    marker = re.search(r"\bvar\s+curriculo\s*=\s*", html_text)
    if not marker:
        return None, None, None

    start = html_text.find("{", marker.end())
    if start < 0:
        return None, None, "Found var curriculo but no opening brace"

    depth = 0
    quote: Optional[str] = None
    escaped = False
    end = None
    for i in range(start, len(html_text)):
        ch = html_text[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in {'"', "'"}:
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return None, None, "Unterminated curriculo object"

    raw_obj = html_text[start:end]
    try:
        return raw_obj, json.loads(raw_obj), None
    except Exception as e_json:
        try:
            return raw_obj, json5.loads(raw_obj), None
        except Exception as e_json5:
            return raw_obj, None, f"json={e_json}; json5={e_json5}"


def extract_visible_text(node: Any) -> str:
    if node is None:
        return ""
    clone = BeautifulSoup(str(node), "html.parser")
    for bad in clone(["script", "style", "noscript", "template"]):
        bad.decompose()
    text = clone.get_text("\n", strip=True)
    lines = [normalize_text(line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def parse_generic_page(html_text: str, url: str, page_type: str, siape: str, detail_depth: int = 0) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    main = soup.select_one("#center") or soup.select_one("#corpo") or soup.body or soup

    page: dict[str, Any] = {
        "siape": siape,
        "page_type": page_type,
        "detail_depth": detail_depth,
        "url": url,
        "title": normalize_text(soup.title.get_text(" ", strip=True) if soup.title else ""),
        "identity": {},
        "headings": [],
        "definition_fields": [],
        "tables": [],
        "links": [],
        "images": [],
        "forms": [],
        "text_nodes": [],
        "visible_text": extract_visible_text(main),
    }

    id_docente = soup.select_one("#id-docente")
    if id_docente:
        name = id_docente.find("h3")
        dep = id_docente.select_one(".departamento")
        if name:
            page["identity"]["name"] = normalize_text(name.get_text(" ", strip=True))
        if dep:
            page["identity"]["department"] = normalize_text(dep.get_text(" ", strip=True))
    left_name = soup.select_one(".barra_professor > h3:not(.departamento):not(.situacao)")
    left_dep = soup.select_one(".barra_professor .departamento")
    left_status = soup.select_one(".barra_professor .situacao")
    if left_name and not page["identity"].get("name"):
        page["identity"]["name"] = normalize_text(left_name.get_text(" ", strip=True))
    if left_dep and not page["identity"].get("department"):
        page["identity"]["department"] = normalize_text(left_dep.get_text(" ", strip=True))
    if left_status:
        status = normalize_text(left_status.get_text(" ", strip=True))
        if status:
            page["identity"]["status"] = status

    for idx, h in enumerate(main.find_all(re.compile(r"^h[1-6]$"))):
        page["headings"].append({
            "index": idx,
            "level": int(h.name[1]),
            "text": normalize_text(h.get_text(" ", strip=True)),
            "attrs": attrs_to_json(h),
        })

    # Definition lists are used heavily by SIGAA profile pages.
    field_idx = 0
    for dl_idx, dl in enumerate(main.find_all("dl")):
        dts = dl.find_all("dt", recursive=False) or dl.find_all("dt")
        dds = dl.find_all("dd", recursive=False) or dl.find_all("dd")
        maxlen = max(len(dts), len(dds))
        for i in range(maxlen):
            dt_tag = dts[i] if i < len(dts) else None
            dd_tag = dds[i] if i < len(dds) else None
            page["definition_fields"].append({
                "index": field_idx,
                "dl_index": dl_idx,
                "key": normalize_text(dt_tag.get_text(" ", strip=True) if dt_tag else ""),
                "value": normalize_text(dd_tag.get_text("\n", strip=True) if dd_tag else ""),
                "value_links": [
                    {
                        "text": normalize_text(a.get_text(" ", strip=True)),
                        "href": urljoin(url, a.get("href") or ""),
                    }
                    for a in (dd_tag.find_all("a", href=True) if dd_tag else [])
                ],
            })
            field_idx += 1

    for t_idx, table in enumerate(main.find_all("table")):
        caption = table.find("caption")
        table_obj = {
            "index": t_idx,
            "caption": normalize_text(caption.get_text(" ", strip=True) if caption else ""),
            "attrs": attrs_to_json(table),
            "rows": [],
        }
        for r_idx, tr in enumerate(table.find_all("tr")):
            cells = []
            direct_cells = tr.find_all(["th", "td"], recursive=False)
            if not direct_cells:
                direct_cells = tr.find_all(["th", "td"])
            for c_idx, cell in enumerate(direct_cells):
                cells.append({
                    "index": c_idx,
                    "tag": cell.name,
                    "text": normalize_text(cell.get_text(" ", strip=True)),
                    "attrs": attrs_to_json(cell),
                    "links": [
                        {
                            "text": normalize_text(a.get_text(" ", strip=True)),
                            "href": urljoin(url, a.get("href") or ""),
                            "title": normalize_text(a.get("title") or ""),
                        }
                        for a in cell.find_all("a", href=True)
                    ],
                })
            table_obj["rows"].append({
                "index": r_idx,
                "attrs": attrs_to_json(tr),
                "cells": cells,
            })
        page["tables"].append(table_obj)

    for idx, a in enumerate(main.find_all("a", href=True)):
        raw_href = a.get("href") or ""
        page["links"].append({
            "index": idx,
            "text": normalize_text(a.get_text(" ", strip=True)),
            "href_raw": normalize_text(raw_href),
            "href_abs": urljoin(url, raw_href),
            "title": normalize_text(a.get("title") or ""),
            "attrs": attrs_to_json(a),
        })

    for idx, img in enumerate(main.find_all("img", src=True)):
        raw_src = img.get("src") or ""
        page["images"].append({
            "index": idx,
            "src_raw": normalize_text(raw_src),
            "src_abs": urljoin(url, raw_src),
            "alt": normalize_text(img.get("alt") or ""),
            "attrs": attrs_to_json(img),
        })

    for idx, form in enumerate(main.find_all("form")):
        controls = []
        for ctl in form.find_all(["input", "select", "textarea", "button"]):
            obj: dict[str, Any] = {
                "tag": ctl.name,
                "name": normalize_text(ctl.get("name") or ""),
                "id": normalize_text(ctl.get("id") or ""),
                "type": normalize_text(ctl.get("type") or ""),
                "value": normalize_text(ctl.get("value") or ""),
                "text": normalize_text(ctl.get_text(" ", strip=True)),
                "attrs": attrs_to_json(ctl),
            }
            if ctl.name == "select":
                obj["options"] = [
                    {
                        "value": normalize_text(opt.get("value") or ""),
                        "text": normalize_text(opt.get_text(" ", strip=True)),
                        "selected": opt.has_attr("selected"),
                    }
                    for opt in ctl.find_all("option")
                ]
            controls.append(obj)
        page["forms"].append({
            "index": idx,
            "method": (form.get("method") or "get").upper(),
            "action": urljoin(url, form.get("action") or url),
            "attrs": attrs_to_json(form),
            "controls": controls,
        })

    # Preserve every rendered text fragment as a generic DOM-level dataset.
    # This catches uncommon SIGAA layouts (e.g. lists/divs rather than tables)
    # without requiring a page-specific parser. Boilerplate is intentionally
    # retained; raw HTML remains the lossless source of truth.
    body_scope = soup.body or soup
    node_idx = 0
    for text_node in body_scope.find_all(string=True):
        parent = getattr(text_node, "parent", None)
        if parent is None or parent.name in {"script", "style", "noscript", "template"}:
            continue
        text_value = normalize_text(str(text_node)).strip()
        if not text_value:
            continue
        ancestry = []
        cur = parent
        for _ in range(8):
            if cur is None or not getattr(cur, "name", None):
                break
            token = cur.name
            if cur.get("id"):
                token += "#" + str(cur.get("id"))
            classes = cur.get("class") or []
            if classes:
                token += "." + ".".join(str(x) for x in classes[:4])
            ancestry.append(token)
            if cur.name == "body":
                break
            cur = cur.parent
        page["text_nodes"].append({
            "index": node_idx,
            "text": text_value,
            "parent_tag": parent.name or "",
            "parent_id": normalize_text(parent.get("id") or ""),
            "parent_classes": [normalize_text(str(x)) for x in (parent.get("class") or [])],
            "dom_path": " > ".join(reversed(ancestry)),
        })
        node_idx += 1

    return page


def root_tab_urls_for_siape(siape: str) -> set[str]:
    return {
        canonicalize_url(f"{DOCENTE_BASE}/{endpoint}?siape={siape}")
        for endpoint in TAB_ENDPOINTS.values()
    }


def is_detail_candidate(url: str, *, siape: str) -> bool:
    if not url:
        return False
    p = urlparse(url)
    if p.scheme not in {"http", "https"}:
        return False
    if p.netloc.lower() != urlparse(BASE_ORIGIN).netloc.lower():
        return False
    if not p.path.startswith("/sigaa/"):
        return False
    if any(p.path.startswith(prefix) for prefix in STATIC_PREFIXES):
        return False
    if p.path in {"/sigaa/", "/sigaa/public/", "/sigaa/public"}:
        return False
    if "verTelaLogin" in p.path or "logout" in p.path.lower():
        return False
    if "busca_docentes.jsf" in p.path:
        return False
    if canonicalize_url(url) in root_tab_urls_for_siape(siape):
        return False
    # Do not recursively walk to another professor from one professor's content.
    if "/public/docente/" in p.path and "siape=" in p.query:
        return False
    return True


# ---------------------------------------------------------------------------
# Archival fetch helpers
# ---------------------------------------------------------------------------

def save_fetch_metadata(meta_path: Path, result: FetchResult, *, detected_encoding: str = "", local_path: str = "") -> None:
    write_json(meta_path, {
        "url": result.url,
        "status_code": result.status_code,
        "headers": result.headers,
        "fetched_at_utc": result.fetched_at_utc,
        "from_cache": result.from_cache,
        "error": result.error,
        "content_type": result.content_type,
        "detected_encoding": detected_encoding,
        "bytes": len(result.content),
        "sha256": sha256_bytes(result.content),
        "local_path": local_path,
    })


def fetch_and_archive(
    client: PoliteSession,
    url: str,
    raw_path: Path,
    utf8_path: Optional[Path],
    *,
    refresh: bool,
    method: str = "GET",
    data: Optional[dict[str, str]] = None,
) -> tuple[FetchResult, Optional[str], str]:
    meta_path = raw_path.with_suffix(raw_path.suffix + ".meta.json")
    if raw_path.exists() and meta_path.exists() and not refresh and method.upper() == "GET":
        content = raw_path.read_bytes()
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        result = FetchResult(
            url=meta.get("url", url),
            status_code=int(meta.get("status_code", 200)),
            headers={str(k).lower(): str(v) for k, v in (meta.get("headers") or {}).items()},
            content=content,
            fetched_at_utc=meta.get("fetched_at_utc", ""),
            from_cache=True,
            error=meta.get("error", ""),
        )
        text = None
        encoding = meta.get("detected_encoding", "")
        if "html" in result.content_type.lower() or raw_path.suffix.lower() in {".html", ".htm"}:
            text, encoding = decode_html_bytes(content, result.content_type)
            if utf8_path:
                utf8_path.parent.mkdir(parents=True, exist_ok=True)
                utf8_path.write_text(text, encoding="utf-8", newline="\n")
        return result, text, encoding

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if method.upper() == "POST":
        response = client.post(url, data=data or {})
    else:
        response = client.get(url)
    headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
    result = FetchResult(
        url=response.url,
        status_code=response.status_code,
        headers=headers,
        content=response.content,
        fetched_at_utc=now_utc(),
        from_cache=False,
    )
    raw_path.write_bytes(result.content)

    text = None
    encoding = ""
    if "html" in result.content_type.lower() or raw_path.suffix.lower() in {".html", ".htm"}:
        text, encoding = decode_html_bytes(result.content, result.content_type)
        if utf8_path:
            utf8_path.parent.mkdir(parents=True, exist_ok=True)
            utf8_path.write_text(text, encoding="utf-8", newline="\n")
    save_fetch_metadata(meta_path, result, detected_encoding=encoding, local_path=str(raw_path))
    return result, text, encoding


def fetch_search_page(client: PoliteSession) -> tuple[requests.Response, str]:
    response = client.get(SEARCH_URL)
    text, _ = decode_html_bytes(response.content, response.headers.get("content-type", ""))
    return response, text


def submit_public_search(
    client: PoliteSession,
    *,
    name: str = "",
    department_id: str = "0",
) -> tuple[requests.Response, str, list[tuple[str, str]]]:
    # Fresh GET ensures a valid JSF ViewState for this POST.
    get_response, get_text = fetch_search_page(client)
    action, hidden, options = extract_search_form(get_text, get_response.url)
    payload = dict(hidden)
    payload.update({
        "form": payload.get("form", "form"),
        "form:nome": name,
        "form:departamento": department_id or "0",
        "form:buscar": "Buscar",
    })
    response = client.post(action, data=payload)
    text, _ = decode_html_bytes(response.content, response.headers.get("content-type", ""))
    return response, text, options


# ---------------------------------------------------------------------------
# Dataset emitter
# ---------------------------------------------------------------------------

class DatasetEmitter:
    def __init__(self, base: Path):
        self.base = base
        self.jsonl_dir = base / "jsonl"
        self.jsonl_dir.mkdir(parents=True, exist_ok=True)
        names = [
            "input_professors",
            "input_matches",
            "resolution_candidates",
            "professors",
            "fetches",
            "pages",
            "fields",
            "table_rows",
            "links",
            "images",
            "forms",
            "text_nodes",
            "lattes_documents",
            "lattes_flat",
            "errors",
        ]
        self.writers = {name: JsonlWriter(self.jsonl_dir / f"{name}.jsonl") for name in names}

    def write(self, table: str, obj: dict[str, Any]) -> None:
        self.writers[table].write(obj)

    def close(self) -> None:
        for writer in self.writers.values():
            writer.close()

    def emit_page(self, page: dict[str, Any], raw_path: str = "") -> None:
        siape = page.get("siape", "")
        page_type = page.get("page_type", "")
        url = page.get("url", "")
        self.write("pages", {
            "siape": siape,
            "page_type": page_type,
            "detail_depth": page.get("detail_depth", 0),
            "url": url,
            "title": page.get("title", ""),
            "professor_name": page.get("identity", {}).get("name", ""),
            "department": page.get("identity", {}).get("department", ""),
            "status": page.get("identity", {}).get("status", ""),
            "visible_text": page.get("visible_text", ""),
            "raw_path": raw_path,
        })
        ordinal = 0
        for h in page.get("headings", []):
            self.write("fields", {
                "siape": siape, "page_type": page_type, "url": url,
                "kind": "heading", "ordinal": ordinal,
                "key": f"h{h.get('level', '')}", "value": h.get("text", ""),
                "meta_json": json_dumps(h, pretty=False),
            })
            ordinal += 1
        for field in page.get("definition_fields", []):
            self.write("fields", {
                "siape": siape, "page_type": page_type, "url": url,
                "kind": "definition", "ordinal": ordinal,
                "key": field.get("key", ""), "value": field.get("value", ""),
                "meta_json": json_dumps(field, pretty=False),
            })
            ordinal += 1

        for table in page.get("tables", []):
            for row in table.get("rows", []):
                self.write("table_rows", {
                    "siape": siape,
                    "page_type": page_type,
                    "url": url,
                    "table_index": table.get("index", 0),
                    "table_caption": table.get("caption", ""),
                    "table_attrs_json": json_dumps(table.get("attrs", {}), pretty=False),
                    "row_index": row.get("index", 0),
                    "row_attrs_json": json_dumps(row.get("attrs", {}), pretty=False),
                    "cells_json": json_dumps(row.get("cells", []), pretty=False),
                    "cell_texts_json": json_dumps([c.get("text", "") for c in row.get("cells", [])], pretty=False),
                })

        for link in page.get("links", []):
            self.write("links", {
                "siape": siape,
                "page_type": page_type,
                "source_url": url,
                "link_index": link.get("index", 0),
                "text": link.get("text", ""),
                "href_raw": link.get("href_raw", ""),
                "href_abs": link.get("href_abs", ""),
                "title": link.get("title", ""),
                "attrs_json": json_dumps(link.get("attrs", {}), pretty=False),
            })

        for image in page.get("images", []):
            self.write("images", {
                "siape": siape,
                "page_type": page_type,
                "source_url": url,
                "image_index": image.get("index", 0),
                "src_raw": image.get("src_raw", ""),
                "src_abs": image.get("src_abs", ""),
                "alt": image.get("alt", ""),
                "attrs_json": json_dumps(image.get("attrs", {}), pretty=False),
            })

        for form in page.get("forms", []):
            self.write("forms", {
                "siape": siape,
                "page_type": page_type,
                "url": url,
                "form_index": form.get("index", 0),
                "method": form.get("method", ""),
                "action": form.get("action", ""),
                "attrs_json": json_dumps(form.get("attrs", {}), pretty=False),
                "controls_json": json_dumps(form.get("controls", []), pretty=False),
            })

        for node in page.get("text_nodes", []):
            self.write("text_nodes", {
                "siape": siape,
                "page_type": page_type,
                "url": url,
                "node_index": node.get("index", 0),
                "text": node.get("text", ""),
                "parent_tag": node.get("parent_tag", ""),
                "parent_id": node.get("parent_id", ""),
                "parent_classes_json": json_dumps(node.get("parent_classes", []), pretty=False),
                "dom_path": node.get("dom_path", ""),
            })


# ---------------------------------------------------------------------------
# DuckDB / Parquet finalization
# ---------------------------------------------------------------------------

TABLE_SCHEMAS = {
    "input_professors": "input_index BIGINT, row_json VARCHAR",
    "input_matches": "input_index BIGINT, input_name VARCHAR, input_department VARCHAR, resolution_status VARCHAR, siape VARCHAR, sigaa_name VARCHAR, sigaa_department VARCHAR, match_method VARCHAR, match_score DOUBLE, profile_url VARCHAR",
    "resolution_candidates": "input_index BIGINT, input_name VARCHAR, candidate_rank BIGINT, siape VARCHAR, candidate_name VARCHAR, candidate_department VARCHAR, profile_url VARCHAR, score DOUBLE",
    "professors": "siape VARCHAR, name VARCHAR, department VARCHAR, status VARCHAR, profile_url VARCHAR, photo_url VARCHAR, lattes_id VARCHAR, lattes_update_date VARCHAR, lattes_update_time VARCHAR, professor_json_path VARCHAR",
    "fetches": "siape VARCHAR, scope VARCHAR, page_type VARCHAR, url VARCHAR, fetched_at_utc VARCHAR, status_code BIGINT, content_type VARCHAR, detected_encoding VARCHAR, bytes BIGINT, sha256 VARCHAR, local_path VARCHAR, from_cache BOOLEAN, error VARCHAR",
    "pages": "siape VARCHAR, page_type VARCHAR, detail_depth BIGINT, url VARCHAR, title VARCHAR, professor_name VARCHAR, department VARCHAR, status VARCHAR, visible_text VARCHAR, raw_path VARCHAR",
    "fields": "siape VARCHAR, page_type VARCHAR, url VARCHAR, kind VARCHAR, ordinal BIGINT, key VARCHAR, value VARCHAR, meta_json VARCHAR",
    "table_rows": "siape VARCHAR, page_type VARCHAR, url VARCHAR, table_index BIGINT, table_caption VARCHAR, table_attrs_json VARCHAR, row_index BIGINT, row_attrs_json VARCHAR, cells_json VARCHAR, cell_texts_json VARCHAR",
    "links": "siape VARCHAR, page_type VARCHAR, source_url VARCHAR, link_index BIGINT, text VARCHAR, href_raw VARCHAR, href_abs VARCHAR, title VARCHAR, attrs_json VARCHAR",
    "images": "siape VARCHAR, page_type VARCHAR, source_url VARCHAR, image_index BIGINT, src_raw VARCHAR, src_abs VARCHAR, alt VARCHAR, attrs_json VARCHAR",
    "forms": "siape VARCHAR, page_type VARCHAR, url VARCHAR, form_index BIGINT, method VARCHAR, action VARCHAR, attrs_json VARCHAR, controls_json VARCHAR",
    "text_nodes": "siape VARCHAR, page_type VARCHAR, url VARCHAR, node_index BIGINT, text VARCHAR, parent_tag VARCHAR, parent_id VARCHAR, parent_classes_json VARCHAR, dom_path VARCHAR",
    "lattes_documents": "siape VARCHAR, lattes_id VARCHAR, source_system VARCHAR, update_date VARCHAR, update_time VARCHAR, raw_object_path VARCHAR, raw_json_path VARCHAR, normalized_json_path VARCHAR, curriculo_json VARCHAR, parse_error VARCHAR",
    "lattes_flat": "siape VARCHAR, lattes_id VARCHAR, path VARCHAR, value_type VARCHAR, value_text VARCHAR",
    "errors": "stage VARCHAR, siape VARCHAR, input_index BIGINT, input_name VARCHAR, url VARCHAR, error VARCHAR, traceback VARCHAR, occurred_at_utc VARCHAR",
}


def sql_quote_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def finalize_duckdb_and_parquet(dataset_dir: Path) -> None:
    db_path = dataset_dir / "sigaa_ufal.duckdb"
    parquet_dir = dataset_dir / "parquet"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        for table, schema in TABLE_SCHEMAS.items():
            jsonl = dataset_dir / "jsonl" / f"{table}.jsonl"
            con.execute(f'DROP TABLE IF EXISTS "{table}"')
            if jsonl.exists() and jsonl.stat().st_size > 0:
                p = sql_quote_path(jsonl)
                # maximum_object_size protects against huge embedded Lattes JSON strings.
                con.execute(
                    f'CREATE TABLE "{table}" AS '
                    f"SELECT * FROM read_json_auto('{p}', format='newline_delimited', union_by_name=true, maximum_object_size=67108864)"
                )
            else:
                con.execute(f'CREATE TABLE "{table}" ({schema})')
            out = sql_quote_path(parquet_dir / f"{table}.parquet")
            con.execute(f'COPY "{table}" TO \'{out}\' (FORMAT PARQUET, COMPRESSION ZSTD)')

        con.execute("DROP VIEW IF EXISTS lattes_documents_json")
        con.execute(
            "CREATE VIEW lattes_documents_json AS "
            "SELECT *, try_cast(curriculo_json AS JSON) AS curriculo FROM lattes_documents"
        )
        con.execute("CHECKPOINT")
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Main scraping pipeline
# ---------------------------------------------------------------------------

def build_fetch_record(siape: str, scope: str, page_type: str, result: FetchResult, encoding: str, local_path: Path) -> dict[str, Any]:
    return {
        "siape": siape,
        "scope": scope,
        "page_type": page_type,
        "url": result.url,
        "fetched_at_utc": result.fetched_at_utc,
        "status_code": result.status_code,
        "content_type": result.content_type,
        "detected_encoding": encoding,
        "bytes": len(result.content),
        "sha256": sha256_bytes(result.content),
        "local_path": str(local_path),
        "from_cache": result.from_cache,
        "error": result.error,
    }


def download_professor_photo(
    client: PoliteSession,
    dataset_dir: Path,
    siape: str,
    photo_url: str,
    emitter: DatasetEmitter,
    refresh: bool,
) -> str:
    if not photo_url:
        return ""
    # First use a generic .bin cache path; after fetching, rename according to MIME.
    photo_dir = dataset_dir / "photos"
    photo_dir.mkdir(parents=True, exist_ok=True)
    existing = list(photo_dir.glob(f"{siape}.*"))
    if existing and not refresh:
        return str(existing[0])
    try:
        response = client.get(photo_url)
        ext = extension_for_content_type(response.headers.get("content-type", ""), response.url)
        path = photo_dir / f"{siape}{ext}"
        path.write_bytes(response.content)
        result = FetchResult(
            url=response.url,
            status_code=response.status_code,
            headers={str(k).lower(): str(v) for k, v in response.headers.items()},
            content=response.content,
            fetched_at_utc=now_utc(),
            from_cache=False,
        )
        save_fetch_metadata(path.with_suffix(path.suffix + ".meta.json"), result, local_path=str(path))
        emitter.write("fetches", build_fetch_record(siape, "photo", "photo", result, "binary", path))
        return str(path)
    except Exception as exc:
        emitter.write("errors", {
            "stage": "photo", "siape": siape, "input_index": -1, "input_name": "",
            "url": photo_url, "error": str(exc), "traceback": traceback.format_exc(),
            "occurred_at_utc": now_utc(),
        })
        return ""


def crawl_detail_pages(
    *,
    client: PoliteSession,
    dataset_dir: Path,
    emitter: DatasetEmitter,
    siape: str,
    seed_pages: list[dict[str, Any]],
    max_depth: int,
    max_pages: int,
    refresh: bool,
) -> list[dict[str, Any]]:
    if max_depth <= 0 or max_pages <= 0:
        return []

    queue: list[tuple[str, int, str]] = []  # url, depth, source_page_type
    seen: set[str] = set()
    root_urls = root_tab_urls_for_siape(siape)
    for page in seed_pages:
        for link in page.get("links", []):
            u = canonicalize_url(link.get("href_abs", ""))
            if u and u not in root_urls and is_detail_candidate(u, siape=siape):
                queue.append((u, 1, page.get("page_type", "root")))

    out: list[dict[str, Any]] = []
    while queue and len(out) < max_pages:
        url, depth, source_type = queue.pop(0)
        url = canonicalize_url(url)
        if url in seen or depth > max_depth:
            continue
        seen.add(url)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        path_stub = f"d{depth}_{digest}"
        raw_dir = dataset_dir / "raw" / siape / "details"
        utf8_dir = dataset_dir / "raw_utf8" / siape / "details"
        raw_path = raw_dir / f"{path_stub}.bin"
        utf8_path = utf8_dir / f"{path_stub}.html"
        try:
            # We do not know MIME until after fetching, so cache as .bin; text is
            # additionally materialized as UTF-8 HTML where appropriate.
            result, maybe_text, encoding = fetch_and_archive(
                client, url, raw_path, utf8_path, refresh=refresh
            )
            emitter.write("fetches", build_fetch_record(siape, "detail", f"detail:{source_type}", result, encoding, raw_path))
            record = {
                "url": result.url,
                "canonical_url": url,
                "depth": depth,
                "source_page_type": source_type,
                "status_code": result.status_code,
                "content_type": result.content_type,
                "raw_path": str(raw_path),
                "utf8_path": str(utf8_path) if maybe_text is not None else "",
            }
            if maybe_text is not None and "html" in (result.content_type or "text/html").lower():
                page_type = f"detail:{source_type}:{digest}"
                parsed = parse_generic_page(maybe_text, result.url, page_type, siape, detail_depth=depth)
                emitter.emit_page(parsed, raw_path=str(raw_path))
                record["parsed"] = parsed
                if depth < max_depth:
                    for link in parsed.get("links", []):
                        child = canonicalize_url(link.get("href_abs", ""))
                        if child and child not in seen and child not in root_urls and is_detail_candidate(child, siape=siape):
                            queue.append((child, depth + 1, page_type))
            out.append(record)
        except Exception as exc:
            emitter.write("errors", {
                "stage": "detail_fetch", "siape": siape, "input_index": -1, "input_name": "",
                "url": url, "error": str(exc), "traceback": traceback.format_exc(),
                "occurred_at_utc": now_utc(),
            })
            out.append({
                "url": url, "depth": depth, "source_page_type": source_type,
                "error": str(exc),
            })
    return out


def scrape_one_professor(
    *,
    client: PoliteSession,
    dataset_dir: Path,
    emitter: DatasetEmitter,
    candidate: SearchCandidate,
    input_refs: list[int],
    refresh: bool,
    detail_depth: int,
    max_detail_pages: int,
) -> dict[str, Any]:
    siape = candidate.siape
    LOG.info("Scraping %s — %s", siape, candidate.name)
    professor: dict[str, Any] = {
        "siape": siape,
        "input_indices": input_refs,
        "registry": asdict(candidate),
        "tabs": {},
        "lattes": None,
        "details": [],
        "photo_local_path": "",
        "scraped_at_utc": now_utc(),
    }

    parsed_roots: list[dict[str, Any]] = []
    discovered_photo_url = candidate.photo_url

    for page_type, endpoint in TAB_ENDPOINTS.items():
        url = f"{DOCENTE_BASE}/{endpoint}?siape={siape}"
        raw_path = dataset_dir / "raw" / siape / f"{page_type}.html"
        utf8_path = dataset_dir / "raw_utf8" / siape / f"{page_type}.html"
        try:
            result, text, encoding = fetch_and_archive(
                client, url, raw_path, utf8_path, refresh=refresh
            )
            emitter.write("fetches", build_fetch_record(siape, "root_tab", page_type, result, encoding, raw_path))
            tab_obj: dict[str, Any] = {
                "url": result.url,
                "status_code": result.status_code,
                "content_type": result.content_type,
                "raw_path": str(raw_path),
                "utf8_path": str(utf8_path),
            }
            if text is None:
                tab_obj["parse_error"] = "Response was not decoded as HTML"
                professor["tabs"][page_type] = tab_obj
                continue

            parsed = parse_generic_page(text, result.url, page_type, siape, detail_depth=0)
            tab_obj["parsed"] = parsed
            professor["tabs"][page_type] = tab_obj
            parsed_roots.append(parsed)
            emitter.emit_page(parsed, raw_path=str(raw_path))

            # Photo is in the left sidebar, outside #center; inspect full HTML.
            soup_full = BeautifulSoup(text, "html.parser")
            photo = soup_full.select_one(".foto_professor img[src]")
            if photo:
                discovered_photo_url = urljoin(result.url, photo.get("src") or "")

            if page_type == "perfil":
                raw_obj_text, raw_curriculo, parse_error = extract_curriculo_object(text)
                lattes_dir = dataset_dir / "lattes" / siape
                lattes_dir.mkdir(parents=True, exist_ok=True)
                raw_obj_path = lattes_dir / "curriculo_object.js.txt"
                raw_json_path = lattes_dir / "curriculo.raw.json"
                normalized_json_path = lattes_dir / "curriculo.normalized.json"
                lattes_id = ""
                update_date = ""
                update_time = ""
                source_system = ""
                normalized_curriculo = None

                if raw_obj_text is not None:
                    raw_obj_path.write_text(raw_obj_text, encoding="utf-8", newline="\n")
                if raw_curriculo is not None:
                    write_json(raw_json_path, raw_curriculo)
                    normalized_curriculo = normalize_recursive(raw_curriculo)
                    write_json(normalized_json_path, normalized_curriculo)
                    source_system = str(normalized_curriculo.get("sistemaorigemxml", "")) if isinstance(normalized_curriculo, dict) else ""
                    lattes_id = str(normalized_curriculo.get("numeroidentificador", "")) if isinstance(normalized_curriculo, dict) else ""
                    update_date = str(normalized_curriculo.get("dataatualizacao", "")) if isinstance(normalized_curriculo, dict) else ""
                    update_time = str(normalized_curriculo.get("horaatualizacao", "")) if isinstance(normalized_curriculo, dict) else ""
                    for path, value_type, value_text in flatten_json(normalized_curriculo):
                        emitter.write("lattes_flat", {
                            "siape": siape,
                            "lattes_id": lattes_id,
                            "path": path,
                            "value_type": value_type,
                            "value_text": value_text,
                        })
                emitter.write("lattes_documents", {
                    "siape": siape,
                    "lattes_id": lattes_id,
                    "source_system": source_system,
                    "update_date": update_date,
                    "update_time": update_time,
                    "raw_object_path": str(raw_obj_path) if raw_obj_text is not None else "",
                    "raw_json_path": str(raw_json_path) if raw_curriculo is not None else "",
                    "normalized_json_path": str(normalized_json_path) if normalized_curriculo is not None else "",
                    "curriculo_json": json_dumps(normalized_curriculo, pretty=False) if normalized_curriculo is not None else "",
                    "parse_error": parse_error or "",
                })
                professor["lattes"] = {
                    "id": lattes_id,
                    "source_system": source_system,
                    "update_date": update_date,
                    "update_time": update_time,
                    "raw_object_path": str(raw_obj_path) if raw_obj_text is not None else "",
                    "raw_json_path": str(raw_json_path) if raw_curriculo is not None else "",
                    "normalized_json_path": str(normalized_json_path) if normalized_curriculo is not None else "",
                    "parse_error": parse_error,
                    "curriculo": normalized_curriculo,
                }
        except Exception as exc:
            emitter.write("errors", {
                "stage": f"root:{page_type}", "siape": siape, "input_index": -1,
                "input_name": candidate.name, "url": url, "error": str(exc),
                "traceback": traceback.format_exc(), "occurred_at_utc": now_utc(),
            })
            professor["tabs"][page_type] = {"url": url, "error": str(exc)}

    if discovered_photo_url:
        professor["registry"]["photo_url"] = discovered_photo_url
        professor["photo_local_path"] = download_professor_photo(
            client, dataset_dir, siape, discovered_photo_url, emitter, refresh
        )

    professor["details"] = crawl_detail_pages(
        client=client,
        dataset_dir=dataset_dir,
        emitter=emitter,
        siape=siape,
        seed_pages=parsed_roots,
        max_depth=detail_depth,
        max_pages=max_detail_pages,
        refresh=refresh,
    )

    professor_json_path = dataset_dir / "professors" / f"{siape}.json.gz"
    write_json_gz(professor_json_path, professor)

    # Prefer identity extracted from the profile page, then discovery metadata.
    identity = {}
    perfil = professor.get("tabs", {}).get("perfil", {}).get("parsed")
    if perfil:
        identity = perfil.get("identity", {})
    lattes = professor.get("lattes") or {}
    emitter.write("professors", {
        "siape": siape,
        "name": identity.get("name") or candidate.name,
        "department": identity.get("department") or candidate.department,
        "status": identity.get("status", ""),
        "profile_url": candidate.profile_url,
        "photo_url": discovered_photo_url,
        "lattes_id": lattes.get("id", ""),
        "lattes_update_date": lattes.get("update_date", ""),
        "lattes_update_time": lattes.get("update_time", ""),
        "professor_json_path": str(professor_json_path),
    })
    return professor


def write_resolved_csv(path: Path, rows: list[dict[str, Any]], original_fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = [
        "_input_index", "_resolution_status", "_siape", "_sigaa_name",
        "_sigaa_department", "_match_method", "_match_score", "_profile_url",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=original_fields + extra, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive and normalize UFAL SIGAA public professor pages")
    parser.add_argument("--input", type=Path, default=None, help=f"Input CSV (default: ./{INPUT_FILENAME})")
    parser.add_argument("--output", type=Path, default=None, help=f"Output directory (default: ./{DATASET_DIRNAME})")
    parser.add_argument("--delay", type=float, default=0.85, help="Minimum seconds between HTTP requests (default: 0.85)")
    parser.add_argument("--timeout-connect", type=float, default=15.0)
    parser.add_argument("--timeout-read", type=float, default=120.0)
    parser.add_argument("--refresh", action="store_true", help="Refetch root/detail pages even if raw cache exists")
    parser.add_argument("--detail-depth", type=int, default=1, choices=range(0, 4), help="Same-origin detail-link crawl depth, 0-3 (default: 1)")
    parser.add_argument("--max-detail-pages", type=int, default=200, help="Safety cap per professor (default: 200)")
    parser.add_argument("--fuzzy-threshold", type=float, default=0.92, help="Reserved compatibility option; matching threshold is conservative")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    script_dir = Path(__file__).resolve().parent
    input_path = (args.input or (script_dir / INPUT_FILENAME)).resolve()
    dataset_dir = (args.output or (script_dir / DATASET_DIRNAME)).resolve()

    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(dataset_dir / "logs" / "scrape.log", encoding="utf-8"),
        ],
    )

    if not input_path.exists():
        LOG.error("Input CSV not found: %s", input_path)
        return 2

    rows, fieldnames, csv_encoding, delimiter = read_csv_robust(input_path)
    name_col = pick_column(fieldnames, ["orientador", "docente", "professor", "nome"])
    dep_col = pick_column(fieldnames, ["departamento", "lotacao", "lotação", "unidade academica", "unidade acadêmica"])
    if not name_col:
        LOG.error("Could not identify professor-name column. CSV columns: %s", fieldnames)
        return 2

    LOG.info("Input: %s rows; encoding=%s delimiter=%r", len(rows), csv_encoding, delimiter)
    LOG.info("Name column=%r; department column=%r", name_col, dep_col)

    # Provenance: copy exact original input bytes and write metadata.
    input_snapshot = dataset_dir / "input" / input_path.name
    input_snapshot.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(input_path, input_snapshot)
    write_json(dataset_dir / "input" / "input_metadata.json", {
        "source_path": str(input_path),
        "snapshot_path": str(input_snapshot),
        "sha256": sha256_bytes(input_path.read_bytes()),
        "detected_encoding": csv_encoding,
        "delimiter": delimiter,
        "fieldnames": fieldnames,
        "rows": len(rows),
        "read_at_utc": now_utc(),
    })

    client = PoliteSession(args.delay, args.timeout_connect, args.timeout_read)
    emitter = DatasetEmitter(dataset_dir)

    resolved_export_rows: list[dict[str, Any]] = []
    input_matches: list[dict[str, Any]] = []
    candidates_by_siape: dict[str, SearchCandidate] = {}
    input_refs_by_siape: dict[str, list[int]] = {}

    try:
        for i, row in enumerate(rows):
            emitter.write("input_professors", {"input_index": i, **row, "row_json": json_dumps(row, pretty=False)})

        # Initial search page provides the authoritative department option IDs.
        LOG.info("Loading SIGAA public faculty search form")
        search_response, search_text = fetch_search_page(client)
        action, hidden, department_options = extract_search_form(search_text, search_response.url)
        discovery_dir = dataset_dir / "raw" / "_discovery"
        discovery_dir.mkdir(parents=True, exist_ok=True)
        (discovery_dir / "search_form.html").write_bytes(search_response.content)
        (dataset_dir / "raw_utf8" / "_discovery").mkdir(parents=True, exist_ok=True)
        (dataset_dir / "raw_utf8" / "_discovery" / "search_form.html").write_text(search_text, encoding="utf-8", newline="\n")
        search_fetch = FetchResult(
            url=search_response.url,
            status_code=search_response.status_code,
            headers={str(k).lower(): str(v) for k, v in search_response.headers.items()},
            content=search_response.content,
            fetched_at_utc=now_utc(),
            from_cache=False,
        )
        emitter.write("fetches", build_fetch_record("", "discovery", "search_form", search_fetch, "html", discovery_dir / "search_form.html"))
        write_json(dataset_dir / "discovery_departments.json", [
            {"id": value, "label": label} for value, label in department_options
        ])

        # Resolve only unique departments present in the input. For this input it
        # avoids dozens of individual name searches.
        unique_input_departments = []
        if dep_col:
            seen_dep = set()
            for row in rows:
                dep = row.get(dep_col, "").strip()
                key = normalize_key(dep)
                if key and key not in seen_dep:
                    unique_input_departments.append(dep)
                    seen_dep.add(key)

        department_registry: dict[str, list[SearchCandidate]] = {}
        all_registry_candidates: dict[str, SearchCandidate] = {}
        for dep_name in unique_input_departments:
            matched = match_department_option(dep_name, department_options)
            if not matched:
                LOG.warning("Could not map input department to SIGAA option: %s", dep_name)
                continue
            dep_id, dep_label = matched
            LOG.info("Enumerating department %s [%s]", dep_label, dep_id)
            response, text, _ = submit_public_search(client, name="", department_id=dep_id)
            safe = safe_filename(f"{dep_id}_{dep_label}", 80)
            (discovery_dir / f"department_{safe}.html").write_bytes(response.content)
            (dataset_dir / "raw_utf8" / "_discovery" / f"department_{safe}.html").write_text(text, encoding="utf-8", newline="\n")
            dep_fetch = FetchResult(
                url=response.url, status_code=response.status_code,
                headers={str(k).lower(): str(v) for k, v in response.headers.items()},
                content=response.content, fetched_at_utc=now_utc(), from_cache=False,
            )
            emitter.write("fetches", build_fetch_record("", "discovery", f"department:{dep_id}", dep_fetch, "html", discovery_dir / f"department_{safe}.html"))
            cands = parse_search_results(text, response.url)
            department_registry[normalize_key(dep_name)] = cands
            for c in cands:
                all_registry_candidates[c.siape] = c
            LOG.info("  -> %s public professor records", len(cands))

        # Resolve every CSV row.
        for i, row in enumerate(rows):
            input_name = row.get(name_col, "").strip()
            input_dep = row.get(dep_col, "").strip() if dep_col else ""
            pool = department_registry.get(normalize_key(input_dep), [])
            selected, method, score, audit = select_candidate(input_name, input_dep, pool)

            # Fallback to a public name search if department enumeration did not
            # produce a safe resolution.
            if selected is None:
                LOG.info("Fallback name search for: %s", input_name)
                try:
                    response, text, _ = submit_public_search(client, name=input_name, department_id="0")
                    safe = safe_filename(f"{i}_{input_name}", 100)
                    (discovery_dir / f"name_{safe}.html").write_bytes(response.content)
                    (dataset_dir / "raw_utf8" / "_discovery" / f"name_{safe}.html").write_text(text, encoding="utf-8", newline="\n")
                    name_fetch = FetchResult(
                        url=response.url, status_code=response.status_code,
                        headers={str(k).lower(): str(v) for k, v in response.headers.items()},
                        content=response.content, fetched_at_utc=now_utc(), from_cache=False,
                    )
                    emitter.write("fetches", build_fetch_record("", "discovery", f"name:{i}", name_fetch, "html", discovery_dir / f"name_{safe}.html"))
                    name_pool = parse_search_results(text, response.url)
                    selected, method2, score2, audit2 = select_candidate(input_name, input_dep, name_pool)
                    if selected is not None or score2 > score:
                        method, score, audit = method2, score2, audit2
                except Exception as exc:
                    emitter.write("errors", {
                        "stage": "name_resolution", "siape": "", "input_index": i,
                        "input_name": input_name, "url": SEARCH_URL, "error": str(exc),
                        "traceback": traceback.format_exc(), "occurred_at_utc": now_utc(),
                    })

            for rank, cand in enumerate(audit, start=1):
                emitter.write("resolution_candidates", {
                    "input_index": i,
                    "input_name": input_name,
                    "candidate_rank": rank,
                    "siape": cand.get("siape", ""),
                    "candidate_name": cand.get("name", ""),
                    "candidate_department": cand.get("department", ""),
                    "profile_url": cand.get("profile_url", ""),
                    "score": cand.get("score", 0.0),
                })

            match = {
                "input_index": i,
                "input_name": input_name,
                "input_department": input_dep,
                "resolution_status": "resolved" if selected else method,
                "siape": selected.siape if selected else "",
                "sigaa_name": selected.name if selected else "",
                "sigaa_department": selected.department if selected else "",
                "match_method": method,
                "match_score": round(float(score), 6),
                "profile_url": selected.profile_url if selected else "",
            }
            emitter.write("input_matches", match)
            input_matches.append(match)

            export_row = dict(row)
            export_row.update({
                "_input_index": i,
                "_resolution_status": match["resolution_status"],
                "_siape": match["siape"],
                "_sigaa_name": match["sigaa_name"],
                "_sigaa_department": match["sigaa_department"],
                "_match_method": match["match_method"],
                "_match_score": match["match_score"],
                "_profile_url": match["profile_url"],
            })
            resolved_export_rows.append(export_row)

            if selected:
                candidates_by_siape[selected.siape] = selected
                input_refs_by_siape.setdefault(selected.siape, []).append(i)

        write_resolved_csv(dataset_dir / "resolved_professors.csv", resolved_export_rows, fieldnames)
        resolved_count = sum(1 for m in input_matches if m["siape"])
        LOG.info("Resolved %s/%s CSV rows to SIAPE; %s unique professors", resolved_count, len(rows), len(candidates_by_siape))

        # Crawl unique resolved professors.
        for idx, siape in enumerate(sorted(candidates_by_siape), start=1):
            cand = candidates_by_siape[siape]
            LOG.info("[%s/%s] %s", idx, len(candidates_by_siape), cand.name)
            try:
                scrape_one_professor(
                    client=client,
                    dataset_dir=dataset_dir,
                    emitter=emitter,
                    candidate=cand,
                    input_refs=input_refs_by_siape.get(siape, []),
                    refresh=args.refresh,
                    detail_depth=args.detail_depth,
                    max_detail_pages=args.max_detail_pages,
                )
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                emitter.write("errors", {
                    "stage": "professor", "siape": siape, "input_index": -1,
                    "input_name": cand.name, "url": cand.profile_url,
                    "error": str(exc), "traceback": traceback.format_exc(),
                    "occurred_at_utc": now_utc(),
                })
                LOG.exception("Professor failed: %s", cand.name)

    except KeyboardInterrupt:
        LOG.warning("Interrupted. Raw cache already downloaded is preserved; rerun to resume.")
        return_code = 130
    except Exception:
        LOG.exception("Fatal pipeline error")
        return_code = 1
    else:
        return_code = 0
    finally:
        emitter.close()

    # Always try to materialize DuckDB/Parquet from whatever was successfully
    # parsed, including after a partial run.
    try:
        LOG.info("Materializing DuckDB and Parquet datasets")
        finalize_duckdb_and_parquet(dataset_dir)
    except Exception:
        LOG.exception("Could not finalize DuckDB/Parquet")
        if return_code == 0:
            return_code = 1

    summary = {
        "completed_at_utc": now_utc(),
        "input_rows": len(rows),
        "resolved_rows": sum(1 for m in input_matches if m.get("siape")),
        "unique_professors": len(candidates_by_siape),
        "output_directory": str(dataset_dir),
        "duckdb": str(dataset_dir / "sigaa_ufal.duckdb"),
        "parquet_directory": str(dataset_dir / "parquet"),
        "resolved_csv": str(dataset_dir / "resolved_professors.csv"),
        "return_code": return_code,
    }
    write_json(dataset_dir / "SUMMARY.json", summary)
    LOG.info("Done. Dataset: %s", dataset_dir)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
