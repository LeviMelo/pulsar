"""Delivery. The only module in PULSAR that can send an email.

Safety properties, all structural rather than advisory:

* only recipients explicitly marked ``selected`` are sent;
* a message already marked ``sent`` is never re-sent;
* delivery reads persisted drafts and never re-renders a template, so what you
  reviewed is exactly what leaves;
* an explicit confirmation flag is required by every caller;
* a dry run walks the full path and sends nothing.

SMTP is one provider behind an interface, not an architectural commitment.
"""

from __future__ import annotations

import json
import mimetypes
import os
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Any, Iterable, Protocol

from ..config import AppConfig
from ..db import Database, utcnow


MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024  # Gmail rejects over ~25 MB


@dataclass(slots=True)
class OutgoingMessage:
    siape: str
    to_name: str
    to_email: str
    subject: str
    body_text: str
    body_html: str
    attachments: tuple[Path, ...] = ()


class MailProvider(Protocol):
    name: str

    def is_configured(self) -> bool: ...

    def send(self, message: OutgoingMessage) -> str:
        """Deliver one message and return a provider message id."""


class SMTPProvider:
    name = "smtp"

    def __init__(self, config: AppConfig):
        c = config.smtp
        self.host = c.get("host") or ""
        self.port = int(c.get("port", 587))
        self.starttls = bool(c.get("starttls", True))
        self.use_ssl = bool(c.get("ssl", False))
        self.user = os.getenv(c.get("username_env", "PULSAR_SMTP_USER"), "")
        self.password = os.getenv(c.get("password_env", "PULSAR_SMTP_PASSWORD"), "")
        self.from_address = c.get("from_address") or self.user
        self.from_name = c.get("from_name") or ""
        self.reply_to = c.get("reply_to") or ""
        self.delay = float(c.get("rate_limit_seconds", 1.5))
        self._server: smtplib.SMTP | None = None

    def is_configured(self) -> bool:
        return bool(self.host and self.from_address)

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "host": self.host, "port": self.port,
                "from": self.from_address, "credentials": bool(self.user and self.password)}

    def __enter__(self) -> "SMTPProvider":
        if not self.is_configured():
            raise RuntimeError("SMTP is not configured: set [smtp] host/from_address in config/default.toml")
        if self.use_ssl:
            self._server = smtplib.SMTP_SSL(self.host, self.port, context=ssl.create_default_context(), timeout=30)
        else:
            self._server = smtplib.SMTP(self.host, self.port, timeout=30)
            self._server.ehlo()
            if self.starttls:
                self._server.starttls(context=ssl.create_default_context())
                self._server.ehlo()
        if self.user:
            self._server.login(self.user, self.password)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            try:
                self._server.quit()
            except Exception:
                pass
            self._server = None

    def verify_credentials(self) -> tuple[bool, str]:
        """Open a real session and authenticate, sending nothing.

        Checking that the environment variables are *set* proved worthless: the
        variables were set, `doctor` reported ready, and the relay rejected the
        login on the first message. The only check worth having is the one the
        provider itself performs.
        """
        if not self.is_configured():
            return False, "host or from_address not configured"
        try:
            # Entered even with no credentials: that still proves the host
            # resolves, the port answers and STARTTLS negotiates, which is most
            # of what goes wrong. `__enter__` skips the login when user is empty.
            with self:
                if not self.user:
                    return True, "connected; no credentials set, relay must allow anonymous relay"
                return True, f"login accepted for {self.user}"
        except smtplib.SMTPAuthenticationError as exc:
            detail = exc.smtp_error.decode("utf-8", "replace") if exc.smtp_error else str(exc)
            hint = ""
            # The overwhelmingly common cause, and invisible from the error text:
            # Google rejects account passwords for SMTP and wants a 16-character
            # App Password, which in turn requires 2-Step Verification.
            if "gmail" in self.host or "google" in self.host:
                length = len(self.password.replace(" ", ""))
                if length != 16:
                    hint = (f" — the password is {length} characters; Google requires a "
                            "16-character App Password (myaccount.google.com/apppasswords), "
                            "not the account password")
            return False, f"rejected by {self.host}: {detail.splitlines()[0]}{hint}"
        except Exception as exc:
            return False, f"could not reach {self.host}:{self.port}: {exc}"

    def send(self, message: OutgoingMessage) -> str:
        if self._server is None:
            raise RuntimeError("SMTP session is not open")
        msg = EmailMessage()
        msg["From"] = formataddr((self.from_name, self.from_address)) if self.from_name else self.from_address
        msg["To"] = formataddr((message.to_name or "", message.to_email))
        msg["Subject"] = message.subject
        if self.reply_to:
            msg["Reply-To"] = self.reply_to
        domain = self.from_address.split("@")[-1] if "@" in self.from_address else None
        message_id = make_msgid(domain=domain)
        msg["Message-ID"] = message_id
        # multipart/alternative: plaintext first, HTML second. Clients that
        # cannot render HTML still receive the full message, not a stub.
        msg.set_content(message.body_text)
        if message.body_html:
            msg.add_alternative(message.body_html, subtype="html")
        for path in message.attachments:
            data = Path(path).read_bytes()
            ctype, _ = mimetypes.guess_type(str(path))
            maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
            msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream",
                               filename=Path(path).name)
        self._server.send_message(msg)
        return message_id


def pending_messages(db: Database, campaign_id: str) -> list[OutgoingMessage]:
    """Selected, not-yet-sent drafts, in audience order."""
    rows = db.query_df(
        """
        SELECT r.siape, r.professor_name, r.email, m.subject, m.body_text, m.body_html
        FROM campaign_recipients r
        JOIN campaign_messages m USING (campaign_id, siape)
        WHERE r.campaign_id=? AND r.selected=TRUE AND m.status<>'sent'
          AND COALESCE(r.email,'')<>''
        ORDER BY r.selection_score DESC, r.professor_name
        """,
        [campaign_id],
    )
    attachments = campaign_attachments(db, campaign_id)
    return [
        OutgoingMessage(str(r.siape), str(r.professor_name or ""), str(r.email),
                        str(r.subject or ""), str(r.body_text or ""), str(r.body_html or ""),
                        attachments)
        for r in rows.itertuples()
    ]


def campaign_attachments(db: Database, campaign_id: str) -> tuple[Path, ...]:
    """The campaign's annexes, verified to still exist and to fit in one message.

    Checked here rather than at send time for each recipient: discovering a
    missing file on recipient 40 of 62 leaves half a campaign delivered without
    the evidence it refers to.
    """
    raw = db.scalar("SELECT attachments_json FROM campaigns WHERE campaign_id=?",
                    [campaign_id], "")
    paths = [Path(p) for p in json.loads(raw)] if raw else []
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"campaign attachment(s) no longer on disk: {', '.join(missing)}")
    total = sum(p.stat().st_size for p in paths)
    if total > MAX_TOTAL_ATTACHMENT_BYTES:
        raise ValueError(
            f"attachments total {total / 1e6:.1f} MB, over the "
            f"{MAX_TOTAL_ATTACHMENT_BYTES / 1e6:.0f} MB limit most providers enforce")
    return tuple(paths)


def send_campaign(
    config: AppConfig,
    db: Database,
    campaign_id: str,
    *,
    confirm: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    progress=None,
) -> dict[str, Any]:
    """Send the selected drafts of a campaign. Requires explicit confirmation."""
    messages = pending_messages(db, campaign_id)
    if limit:
        messages = messages[:limit]
    provider = SMTPProvider(config)

    if dry_run or not confirm:
        return {
            "campaign_id": campaign_id,
            "would_send": len(messages),
            "sent": 0,
            "failed": 0,
            "dry_run": True,
            "confirmed": bool(confirm),
            "provider": provider.describe(),
            "attachments": [p.name for p in (messages[0].attachments if messages else ())],
            "recipients": [{"siape": m.siape, "name": m.to_name, "email": m.to_email,
                            "subject": m.subject} for m in messages],
        }

    sent = failed = 0
    with provider as session:
        for message in messages:
            try:
                message_id = session.send(message)
                with db.connect() as con:
                    con.execute(
                        "UPDATE campaign_messages SET status='sent', sent_at=?, "
                        "provider_message_id=?, error='' WHERE campaign_id=? AND siape=?",
                        [utcnow(), message_id, campaign_id, message.siape],
                    )
                sent += 1
            except Exception as exc:
                with db.connect() as con:
                    con.execute(
                        "UPDATE campaign_messages SET status='failed', error=? "
                        "WHERE campaign_id=? AND siape=?",
                        [f"{type(exc).__name__}: {exc}", campaign_id, message.siape],
                    )
                failed += 1
            if progress:
                progress(sent + failed, len(messages))
            time.sleep(provider.delay)

    remaining = int(db.scalar(
        "SELECT COUNT(*) FROM campaign_recipients r JOIN campaign_messages m USING (campaign_id, siape) "
        "WHERE r.campaign_id=? AND r.selected AND m.status<>'sent'", [campaign_id], 0))
    status = "sent" if failed == 0 and remaining == 0 else ("partial" if sent else "failed")
    with db.connect() as con:
        con.execute("UPDATE campaigns SET status=?, updated_at=? WHERE campaign_id=?",
                    [status, utcnow(), campaign_id])
    return {"campaign_id": campaign_id, "sent": sent, "failed": failed,
            "remaining_selected": remaining, "dry_run": False, "status": status}
