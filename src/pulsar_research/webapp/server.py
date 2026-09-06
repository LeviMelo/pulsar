"""The console's HTTP server: standard library only.

The previous console was a Streamlit app. Streamlit was also the single heaviest
entry in this project's dependency tree, it was not actually installed in the
environment that ships the tool, and its execution model — re-run the whole
script on every widget change — is the wrong shape for an interface whose entire
job is to let someone filter 187 rows and read one of them. Selecting a work
plan from a dropdown to "inspect" it is a workaround for a framework, not a
design.

So this serves a handful of JSON endpoints out of `http.server`, and the
dependency is gone. The corpus is small enough that the browser holds all of it,
which is what makes the interface feel immediate.

The front end that consumes those endpoints is a React application built by
Vite; `STATIC_DIR` is its output directory. Nothing here knows that: it finds an
`index.html` and a hashed asset bundle, and every unknown path falls back to the
shell, which is all a hash-routed single-page app needs. See
`webapp/frontend/README` and the project README for the build.

Bound to the loopback interface, and the `Host` header is checked on every
request: a page on the open internet can otherwise point a DNS name at
127.0.0.1 and read a local server through the user's browser. There is no
authentication beyond that, which is the correct trade for a tool that reads a
DuckDB file in the operator's own home directory — and the reason no endpoint
here sends mail.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from ..config import AppConfig
from ..db import Database
from .payloads import Payloads

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Hostnames a browser may legitimately use to reach a loopback server.
ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})

#: List payloads are rebuilt from several joins, so they are memoised briefly.
#: Short enough that a `pulsar semantics profile` in another terminal shows up
#: without restarting the console, long enough that paging around is instant.
CACHE_TTL_SECONDS = 20.0


class Cache:
    """A tiny TTL memo, cleared wholesale whenever anything is written."""

    def __init__(self, ttl: float = CACHE_TTL_SECONDS):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, key: str, produce: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        value = produce()
        with self._lock:
            self._entries[key] = (now, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class Console:
    """Routing and handlers, kept apart from the HTTP plumbing so it is testable."""

    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db
        self.payloads = Payloads(db)
        self.cache = Cache()

    # -- GET ---------------------------------------------------------------

    def get(self, path: str, query: dict[str, list[str]]) -> Any:
        parts = [p for p in path.strip("/").split("/") if p]
        if parts == ["api", "state"]:
            return self.cache.get("state", self.payloads.state)
        if parts == ["api", "opportunities"]:
            return self.cache.get("opportunities", self.payloads.opportunities)
        if parts == ["api", "professors"]:
            return self.cache.get("professors", self.payloads.professors)
        if parts == ["api", "landscape"]:
            facet = (query.get("facet") or ["domain"])[0]
            facet = facet if facet in ("domain", "methods") else "domain"
            return self.cache.get(f"landscape:{facet}", lambda: self.payloads.landscape(facet))
        if parts == ["api", "network"]:
            from .network import MODES, build
            mode = (query.get("mode") or ["collaboration"])[0]
            mode = mode if mode in MODES else "collaboration"
            return self.cache.get(f"network:{mode}", lambda: build(
                self.db, mode, display_name=self.payloads.display_name))
        if parts == ["api", "engine"]:
            return self.cache.get("engine", self.payloads.engine)
        if parts == ["api", "pipeline"]:
            # Deliberately uncached. Freshness is the one payload whose whole
            # point is to have changed since the page was opened — a `pulsar
            # pipeline run` in another window must show up on a reload.
            return self.payloads.pipeline()
        # -- the store, opened: records, portfolios, entities, one search ------
        if parts == ["api", "records"]:
            return self.payloads.explorer.search(query)
        if parts == ["api", "records", "facets"]:
            siape = (query.get("siape") or [""])[0]
            return self.cache.get(f"facets:{siape}", lambda: self.payloads.explorer.facets(siape))
        if len(parts) == 3 and parts[:2] == ["api", "record"]:
            return self.payloads.explorer.record(parts[2]) or _missing("record", parts[2])
        if len(parts) == 4 and parts[:2] == ["api", "professor"] and parts[3] == "portfolio":
            return self.cache.get(f"portfolio:{parts[2]}",
                                  lambda: self.payloads.explorer.portfolio(parts[2]))
        if len(parts) == 3 and parts[:2] == ["api", "entity"]:
            return self.payloads.explorer.entity(parts[2]) or _missing("entity", parts[2])
        if parts == ["api", "search"]:
            return self.payloads.explorer.search_all((query.get("q") or [""])[0])
        if parts == ["api", "sources"]:
            from ..sources import describe
            return {"sources": describe(self.config, self.db)}
        if len(parts) == 3 and parts[:2] == ["api", "opportunity"]:
            return self.payloads.opportunity(parts[2]) or _missing("work plan", parts[2])
        if len(parts) == 3 and parts[:2] == ["api", "professor"]:
            return self.payloads.professor(parts[2]) or _missing("professor", parts[2])
        if len(parts) == 3 and parts[:2] == ["api", "campaign"]:
            return self.payloads.campaign(parts[2])
        raise NotFound(path)

    # -- POST --------------------------------------------------------------

    def post(self, path: str, body: dict[str, Any]) -> Any:
        parts = [p for p in path.strip("/").split("/") if p]
        if parts == ["api", "outcome"]:
            return self._set_outcome(body)
        if parts == ["api", "message"]:
            return self._update_message(body)
        raise NotFound(path)

    def _set_outcome(self, body: dict[str, Any]) -> Any:
        from ..apps.outreach import outcomes as oc

        campaign_id = str(body.get("campaign_id") or "")
        siape = str(body.get("siape") or "")
        if not campaign_id or not siape:
            raise BadRequest("campaign_id and siape are required")
        try:
            record = oc.set_state(
                self.db, campaign_id, siape,
                state=body.get("state"), note=body.get("note"),
                force=bool(body.get("force")),
            )
        except oc.IndicationConflict as exc:
            raise Conflict(str(exc)) from exc
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        self.cache.clear()
        return {"ok": True, "outcome": record,
                "funnel": oc.funnel(self.db, campaign_id),
                "indicated": (lambda p: None if p is None else
                              {"campaign_id": p[0], "siape": p[1]})(oc.indicated(self.db))}

    def _update_message(self, body: dict[str, Any]) -> Any:
        from ..apps.outreach.campaigns import regenerate_html, update_message

        campaign_id = str(body.get("campaign_id") or "")
        siape = str(body.get("siape") or "")
        if not campaign_id or not siape:
            raise BadRequest("campaign_id and siape are required")
        update_message(self.db, campaign_id, siape,
                       subject=body.get("subject"), body_text=body.get("body_text"),
                       selected=body.get("selected"))
        # The HTML alternative is derived from the text one; leaving it stale
        # would mean the message that goes out is not the message just edited.
        regenerate_html(self.config, self.db, campaign_id, only_uncustomized=False)
        self.cache.clear()
        return {"ok": True}


class HttpError(Exception):
    status = 500

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(HttpError):
    status = 404


class BadRequest(HttpError):
    status = 400


class Conflict(HttpError):
    status = 409


def _missing(kind: str, key: str) -> dict[str, Any]:
    raise NotFound(f"no {kind} with id {key}")


def _handler(console: Console):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PULSAR"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        # -- plumbing ---------------------------------------------------

        def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib signature
            """Silence the per-request access log; failures are reported instead."""

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in {h.strip("[]") for h in ALLOWED_HOSTS}

        def _send(self, status: int, payload: bytes, content_type: str,
                  extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            # The console reads a local database and holds no session; nothing
            # here should ever be embedded in another page.
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _json(self, status: int, value: Any) -> None:
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8",
                       {"Cache-Control": "no-store"})

        def _error(self, exc: BaseException) -> None:
            status = getattr(exc, "status", 500)
            message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
            if status >= 500:
                traceback.print_exc()
            self._json(status, {"error": message, "status": status})

        # -- verbs ------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 - stdlib signature
            if not self._host_ok():
                self._json(403, {"error": "this console only answers on localhost"})
                return
            parsed = urlparse(self.path)
            try:
                if parsed.path.startswith("/api/"):
                    self._json(200, console.get(parsed.path, parse_qs(parsed.query)))
                else:
                    self._static(parsed.path)
            except HttpError as exc:
                self._error(exc)
            except Exception as exc:  # pragma: no cover - defensive
                self._error(exc)

        do_HEAD = do_GET

        def do_POST(self) -> None:  # noqa: N802 - stdlib signature
            if not self._host_ok():
                self._json(403, {"error": "this console only answers on localhost"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError as exc:
                    raise BadRequest(f"body is not JSON: {exc}") from exc
                if not isinstance(body, dict):
                    raise BadRequest("body must be a JSON object")
                self._json(200, console.post(urlparse(self.path).path, body))
            except HttpError as exc:
                self._error(exc)
            except Exception as exc:  # pragma: no cover - defensive
                self._error(exc)

        # -- static -----------------------------------------------------

        def _static(self, path: str) -> None:
            relative = path.lstrip("/") or "index.html"
            target = (STATIC_DIR / relative).resolve()
            # A request for ../../secrets must not escape the static directory.
            if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
                target = STATIC_DIR / "index.html"
            if not target.is_file():
                raise NotFound(path)
            content_type, _ = mimetypes.guess_type(target.name)
            body = target.read_bytes()
            # The front end is edited constantly during development, and a
            # cached shell that never reloads is a worse bug than a slow one.
            self._send(200, body, content_type or "application/octet-stream",
                       {"Cache-Control": "no-cache"})

    return Handler


def serve(config: AppConfig, db: Database, *, host: str = "127.0.0.1", port: int = 8787):
    """Build the server. The caller decides whether to block on it."""
    console = Console(config, db)
    return ThreadingHTTPServer((host, port), _handler(console))
