"""Delivery is the least reversible thing PULSAR does, so it is tested for real.

A minimal in-process SMTP sink accepts the messages: nothing leaves the machine
and no real address is ever used, but the whole path runs — session setup, MIME
construction, per-recipient status transitions and the no-resend rule.
"""
from __future__ import annotations

import email
import socket
import threading

import pytest

from pulsar_research.config import AppConfig
from pulsar_research.db import Database, utcnow


class SMTPSink:
    """Speaks just enough SMTP for smtplib, and keeps what it was given.

    Deliberately neither `aiosmtpd` (a dependency PULSAR does not otherwise
    need) nor `smtpd` (removed in Python 3.12).
    """

    def __init__(self) -> None:
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(8)
        self.port = self._server.getsockname()[1]
        self.messages: list[bytes] = []
        self.envelopes: list[tuple[str, list[str]]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(conn,), daemon=True).start()

    def _session(self, conn: socket.socket) -> None:
        stream = conn.makefile("rwb")
        sender, rcpts = "", []
        stream.write(b"220 sink ready\r\n")
        stream.flush()
        while True:
            line = stream.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            upper = text.upper()
            if upper.startswith(("EHLO", "HELO")):
                stream.write(b"250-sink\r\n250 SMTPUTF8\r\n")
            elif upper.startswith("MAIL FROM"):
                sender = text[10:].strip().strip("<>")
                stream.write(b"250 ok\r\n")
            elif upper.startswith("RCPT TO"):
                rcpts.append(text[8:].strip().strip("<>"))
                stream.write(b"250 ok\r\n")
            elif upper == "DATA":
                stream.write(b"354 go ahead\r\n")
                stream.flush()
                # Captured as bytes: a mail server must not lossily decode, and
                # the charset is part of what these tests are checking.
                chunk: list[bytes] = []
                while True:
                    data_line = stream.readline()
                    if data_line in (b".\r\n", b".\n", b""):
                        break
                    chunk.append(data_line[1:] if data_line.startswith(b"..") else data_line)
                self.messages.append(b"".join(chunk))
                self.envelopes.append((sender, list(rcpts)))
                sender, rcpts = "", []
                stream.write(b"250 queued\r\n")
            elif upper == "QUIT":
                stream.write(b"221 bye\r\n")
                stream.flush()
                break
            else:
                stream.write(b"250 ok\r\n")
            stream.flush()
        try:
            conn.close()
        except OSError:
            pass

    def close(self) -> None:
        self._stop.set()
        self._server.close()


@pytest.fixture()
def sink():
    server = SMTPSink()
    yield server
    server.close()


@pytest.fixture()
def config(sink):
    cfg = AppConfig.load()
    cfg.raw["smtp"] = {
        "host": "127.0.0.1", "port": sink.port, "starttls": False, "ssl": False,
        "username_env": "PULSAR_TEST_NO_SUCH_USER",
        "password_env": "PULSAR_TEST_NO_SUCH_PASSWORD",
        "from_address": "levi@example.invalid", "from_name": "Levi",
        "rate_limit_seconds": 0.0,
    }
    return cfg


@pytest.fixture()
def campaign(tmp_path):
    """A two-recipient campaign: one selected, one deselected."""
    db = Database(tmp_path / "outreach.duckdb")
    db.initialize()
    now = utcnow()
    with db.connect() as con:
        con.execute(
            "INSERT INTO campaigns (campaign_id, name, status, created_at, updated_at) "
            "VALUES ('c1','Teste','draft',?,?)", [now, now])
        for siape, name, mail, score, selected in (
            ("10", "ana souza", "ana@example.invalid", 90.0, True),
            ("20", "bruno lima", "bruno@example.invalid", 80.0, False),
        ):
            con.execute(
                "INSERT INTO campaign_recipients (campaign_id, siape, professor_name, email, "
                "selection_score, selected, created_at) VALUES ('c1',?,?,?,?,?,?)",
                [siape, name, mail, score, selected, now])
            con.execute(
                "INSERT INTO campaign_messages (campaign_id, siape, subject, body_text, body_html, "
                "is_customized, status, rendered_at, sent_at, provider_message_id, error) "
                "VALUES ('c1',?,?,?,?,FALSE,'draft',?,'','','')",
                [siape, f"Assunto para {name}", f"Ola {name}, corpo em texto com acento: publicacao.",
                 f"<p>Ola {name}, corpo em HTML com acento: publicacao.</p>", now])
    return db


def test_without_confirm_nothing_is_sent(config, campaign, sink):
    from pulsar_research.outreach.mailer import send_campaign

    result = send_campaign(config, campaign, "c1")
    assert result["dry_run"] is True and result["sent"] == 0
    assert result["would_send"] == 1, "only the selected recipient is even a candidate"
    assert sink.messages == [], "a dry run must not deliver anything"


def test_only_selected_recipients_are_sent(config, campaign, sink):
    from pulsar_research.outreach.mailer import send_campaign

    result = send_campaign(config, campaign, "c1", confirm=True)
    assert (result["sent"], result["failed"]) == (1, 0)
    assert len(sink.messages) == 1
    assert sink.envelopes[0][1] == ["ana@example.invalid"]
    assert b"bruno@example.invalid" not in b"".join(sink.messages)


def test_message_is_multipart_alternative_with_a_real_text_part(config, campaign, sink):
    from pulsar_research.outreach.mailer import send_campaign

    send_campaign(config, campaign, "c1", confirm=True)
    msg = email.message_from_bytes(sink.messages[0])
    assert msg.get_content_type() == "multipart/alternative"
    parts = msg.get_payload()
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    text = parts[0].get_payload(decode=True).decode(parts[0].get_content_charset() or "utf-8")
    html = parts[1].get_payload(decode=True).decode(parts[1].get_content_charset() or "utf-8")
    assert "corpo em texto" in text, "the plaintext alternative must carry the message, not a stub"
    assert "corpo em HTML" in html
    assert msg["Message-ID"] and msg["To"] and msg["Subject"]


def test_a_sent_message_is_never_sent_twice(config, campaign, sink):
    from pulsar_research.outreach.mailer import pending_messages, send_campaign

    first = send_campaign(config, campaign, "c1", confirm=True)
    second = send_campaign(config, campaign, "c1", confirm=True)
    assert first["sent"] == 1 and second["sent"] == 0
    assert len(sink.messages) == 1
    assert pending_messages(campaign, "c1") == []
    assert campaign.scalar("SELECT status FROM campaigns WHERE campaign_id='c1'") == "sent"


def test_a_message_that_never_left_is_not_recorded_as_sent(config, campaign, sink):
    from pulsar_research.outreach.mailer import send_campaign

    sink.close()  # the provider cannot open a session at all
    with pytest.raises(Exception):
        send_campaign(config, campaign, "c1", confirm=True)
    status = campaign.scalar("SELECT status FROM campaign_messages WHERE siape='10'")
    assert status != "sent"
