"""The console's HTTP surface.

It is a server bound to loopback with no authentication, which is the right
trade for a tool reading a local DuckDB file — but only as long as loopback is
actually enforced and the static handler cannot be talked into serving whatever
path it is asked for. Both are checked here, along with the JSON contract the
front end depends on.
"""
from __future__ import annotations

import json
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from pulsar_research.config import AppConfig
from pulsar_research.webapp.payloads import clean, records
from pulsar_research.webapp.server import BadRequest, Console, NotFound, serve


@pytest.fixture()
def home(tmp_path):
    """A throwaway project root: the real config, none of the real data."""
    shutil.copytree(Path(__file__).resolve().parent.parent / "config", tmp_path / "config")
    return tmp_path


@pytest.fixture()
def console(db, home):
    return Console(AppConfig.load(home), db)


@pytest.fixture()
def live(db, home):
    """A real server on an ephemeral port, torn down with the test."""
    httpd = serve(AppConfig.load(home), db, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.read()


# -------------------------------------------------------------- payloads

def test_absent_numbers_stay_absent_rather_than_becoming_zero():
    """A percentile that was never computed is not a percentile of zero, and the
    interface decides whether to draw a bar on exactly that difference."""
    assert clean(float("nan")) is None
    assert clean(float("inf")) is None
    assert clean(0.0) == 0.0
    assert clean(None) is None


def test_records_keeps_only_requested_columns_that_exist():
    import pandas as pd
    frame = pd.DataFrame([{"a": 1, "b": 2}])
    assert records(frame, ("a", "missing")) == [{"a": 1}]
    assert records(pd.DataFrame()) == []


def test_every_payload_survives_strict_json(console):
    """`json.dumps(..., allow_nan=False)` is what the server actually calls, and
    a stray NaN there is a 500 the browser reports as a blank page."""
    for name in ("state", "opportunities", "professors", "engine", "pipeline"):
        json.dumps(getattr(console.payloads, name)(), allow_nan=False)
    json.dumps(console.payloads.landscape("domain"), allow_nan=False)


def test_the_freshness_payload_reports_every_declared_stage(console):
    """The console never runs the pipeline; it has to be able to *show* it."""
    from pulsar_research.pipeline import STAGES
    stages = console.payloads.pipeline()["stages"]
    from pulsar_research.pipeline import order
    # Dependency order, so the table reads top to bottom the way the build runs.
    assert [row["stage"] for row in stages] == [stage.name for stage in order()]
    assert {row["stage"] for row in stages} == {stage.name for stage in STAGES}
    for row in stages:
        assert row["state"] in ("ok", "stale", "never", "blocked", "unknown")
        assert row["why"], f"{row['stage']} does not say what it is for"


def test_freshness_is_never_served_from_the_cache(console):
    """It is the one payload whose whole point is to have changed since the
    page was opened; caching it would make the console confidently stale about
    staleness."""
    console.get("/api/pipeline", {})
    assert not any("pipeline" in key for key in console.cache._entries)


# --------------------------------------------------------------- routing

def test_unknown_routes_are_404_not_500(console):
    with pytest.raises(NotFound):
        console.get("/api/nope", {})
    with pytest.raises(NotFound):
        console.post("/api/nope", {})


def test_an_unknown_entity_is_a_404(console):
    with pytest.raises(NotFound):
        console.get("/api/opportunity/does-not-exist", {})


def test_the_facet_parameter_cannot_be_used_to_ask_for_anything(console):
    """It reaches SQL, so it is validated against a closed set, not passed on."""
    assert console.get("/api/landscape", {"facet": ["methods"]})["facet"] == "methods"
    assert console.get("/api/landscape", {"facet": ["' OR 1=1"]})["facet"] == "domain"


def test_outcome_writes_require_both_identifiers(console):
    for body in ({}, {"campaign_id": "c1"}, {"siape": "1"}):
        with pytest.raises(BadRequest):
            console.post("/api/outcome", body)


def test_a_write_clears_the_read_cache(console, db):
    """Otherwise the funnel a user just changed keeps reporting its old numbers."""
    with db.connect() as con:
        con.execute("INSERT INTO campaigns (campaign_id, name, status) VALUES ('c1','T','sent')")
        con.execute("INSERT INTO campaign_recipients (campaign_id, siape, professor_name, selected) "
                    "VALUES ('c1','10','Ana',TRUE)")
        con.execute("INSERT INTO campaign_messages (campaign_id, siape, subject, status) "
                    "VALUES ('c1','10','hi','sent')")
    console.cache.get("state", console.payloads.state)
    assert console.cache._entries
    console.post("/api/outcome", {"campaign_id": "c1", "siape": "10", "state": "open"})
    assert not console.cache._entries


def test_the_campaign_payload_never_offers_to_send(console, db):
    """Sending is irreversible and outward-facing; it stays at a terminal."""
    with db.connect() as con:
        con.execute("INSERT INTO campaigns (campaign_id, name, status) VALUES ('c1','T','draft')")
    payload = console.get("/api/campaign/c1", {})
    assert payload["send_command"] == "pulsar campaign send c1 --confirm"


# ---------------------------------------------------------------- server

def test_the_shell_is_served_at_the_root(live):
    status, body = get(f"{live}/")
    assert status == 200 and b"<title>PULSAR</title>" in body


def test_an_unknown_page_falls_back_to_the_shell_so_deep_links_work(live):
    """Hash routes never reach the server, but a stray path must not 404 the app."""
    status, body = get(f"{live}/opportunities")
    assert status == 200 and b"<title>PULSAR</title>" in body


def test_the_static_handler_cannot_be_walked_out_of_its_directory(live):
    """`../` must land on the shell, never on a file outside the static root."""
    for attempt in ("/../../../../etc/passwd", "/..%2f..%2fapp.py", "/lib/../../server.py"):
        status, body = get(f"{live}{attempt}")
        assert status == 200, attempt
        assert b"<title>PULSAR</title>" in body, attempt
        assert b"import" not in body[:200], attempt


def test_a_foreign_host_header_is_refused(live):
    """Without this, any web page can point a DNS name at 127.0.0.1 and read
    this server through the user's own browser."""
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(f"{live}/api/state", {"Host": "pulsar.example.com"})
    assert excinfo.value.code == 403


def test_loopback_hosts_are_accepted(live):
    port = live.rsplit(":", 1)[1]
    for host in (f"localhost:{port}", f"127.0.0.1:{port}"):
        status, _ = get(f"{live}/api/state", {"Host": host})
        assert status == 200, host


def test_api_errors_come_back_as_json_with_a_real_status(live):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        get(f"{live}/api/opportunity/nope")
    assert excinfo.value.code == 404
    assert json.loads(excinfo.value.read())["error"]
