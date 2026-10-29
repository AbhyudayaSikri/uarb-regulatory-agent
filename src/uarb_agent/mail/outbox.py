"""SMTP outbox and a helper that writes the same message to an ``.eml`` file (dry runs)."""

from __future__ import annotations

import logging
import mimetypes
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from ..config import Settings
from ..models import OutboundMessage

log = logging.getLogger(__name__)


def to_email_message(message: OutboundMessage, sender: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = message.to
    msg["Subject"] = message.subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1].strip("> ") or None)
    msg["Auto-Submitted"] = "auto-replied"
    msg["X-Auto-Response-Suppress"] = "All"
    if message.in_reply_to:
        msg["In-Reply-To"] = message.in_reply_to
    if message.references:
        msg["References"] = " ".join(message.references)

    msg.set_content(message.text_body)
    if message.html_body:
        msg.add_alternative(message.html_body, subtype="html")

    for path in message.attachments:
        path = Path(path)
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(
            path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name
        )
    return msg


class SmtpOutbox:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, message: OutboundMessage) -> None:
        s = self.settings
        sender = s.smtp_from or s.smtp_user
        msg = to_email_message(message, sender)
        if s.smtp_use_ssl or s.smtp_port == 465:
            server: smtplib.SMTP = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=60)
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=60)
        with server:
            server.ehlo()
            if not isinstance(server, smtplib.SMTP_SSL) and server.has_extn("starttls"):
                server.starttls()
                server.ehlo()
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password)
            server.send_message(msg)
        log.info("sent reply to %s (%d attachment(s))", message.to, len(message.attachments))


def write_eml(message: OutboundMessage, sender: str, path: Path) -> Path:
    """Serialise the reply exactly as SMTP would send it. Used by ``--dry-run``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(to_email_message(message, sender).as_bytes())
    return path
