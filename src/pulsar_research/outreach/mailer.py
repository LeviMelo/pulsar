from __future__ import annotations

import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from ..config import AppConfig
from ..db import Database, utcnow


class SMTPMailer:
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
        self.delay = float(c.get("rate_limit_seconds", 1.0))
        if not self.host or not self.from_address:
            raise RuntimeError("SMTP is not configured in config/default.toml / environment variables")

    def connect(self):
        if self.use_ssl:
            server = smtplib.SMTP_SSL(self.host, self.port, context=ssl.create_default_context(), timeout=30)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=30)
            server.ehlo()
            if self.starttls:
                server.starttls(context=ssl.create_default_context()); server.ehlo()
        if self.user:
            server.login(self.user, self.password)
        return server

    def send_campaign(self, db: Database, campaign_id: str) -> dict[str, int]:
        with db.connect(read_only=True) as con:
            rows = con.execute(
                """
                SELECT r.siape,r.professor_name,r.email,m.subject,m.body,m.status
                FROM campaign_recipients r JOIN campaign_messages m USING(campaign_id,siape)
                WHERE r.campaign_id=? AND r.selected=TRUE AND m.status<>'sent'
                ORDER BY r.selection_score DESC
                """, [campaign_id]
            ).fetchall()
        sent = failed = 0
        with self.connect() as server:
            for siape, name, email, subject, body, status in rows:
                msg = EmailMessage()
                msg["From"] = formataddr((self.from_name, self.from_address)) if self.from_name else self.from_address
                msg["To"] = formataddr((name or "", email))
                msg["Subject"] = subject
                if self.reply_to: msg["Reply-To"] = self.reply_to
                msg_id = make_msgid(domain=self.from_address.split("@")[-1] if "@" in self.from_address else None)
                msg["Message-ID"] = msg_id
                msg.set_content(body)
                try:
                    server.send_message(msg)
                    with db.connect() as con:
                        con.execute("UPDATE campaign_messages SET status='sent',sent_at=?,provider_message_id=?,error='' WHERE campaign_id=? AND siape=?", [utcnow(), msg_id, campaign_id, siape])
                    sent += 1
                except Exception as exc:
                    with db.connect() as con:
                        con.execute("UPDATE campaign_messages SET status='failed',error=? WHERE campaign_id=? AND siape=?", [str(exc), campaign_id, siape])
                    failed += 1
                time.sleep(self.delay)
        with db.connect() as con:
            con.execute("UPDATE campaigns SET status=?,updated_at=? WHERE campaign_id=?", ["sent" if failed == 0 else "partial", utcnow(), campaign_id])
        return {"sent": sent, "failed": failed}
