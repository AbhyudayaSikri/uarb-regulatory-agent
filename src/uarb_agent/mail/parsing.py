"""Turn a raw RFC 822 message into an :class:`InboundMessage`."""

from __future__ import annotations

import email
import email.policy
import re
from contextlib import suppress
from datetime import datetime
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from ..models import InboundMessage

AUTO_SUBMITTED_VALUES = {"auto-replied", "auto-generated", "auto-notified"}
BOUNCE_SENDERS = re.compile(r"^(mailer-daemon|postmaster|no-?reply|donotreply)@", re.I)


def parse_rfc822(raw: bytes) -> InboundMessage:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(msg, EmailMessage)
    message_id = (msg.get("Message-ID") or "").strip() or f"<missing-{abs(hash(raw))}@local>"
    sender = (msg.get("Reply-To") or msg.get("From") or "").strip()
    subject = (msg.get("Subject") or "").strip()
    references = [r for r in (msg.get("References") or "").split() if r]
    in_reply_to = (msg.get("In-Reply-To") or "").strip()
    if in_reply_to and in_reply_to not in references:
        references.append(in_reply_to)
    received_at = datetime.now()
    if msg.get("Date"):
        with suppress(TypeError, ValueError):
            received_at = parsedate_to_datetime(msg["Date"])
    return InboundMessage(
        message_id=message_id,
        sender=sender,
        subject=subject,
        body=extract_text(msg),
        received_at=received_at,
        references=references,
        is_auto_reply=looks_automatic(msg),
    )


def extract_text(msg: EmailMessage) -> str:
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    content = body.get_content()
    if body.get_content_type() == "text/html":
        content = html_to_text(content)
    return content.strip()


def html_to_text(markup: str) -> str:
    markup = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", markup)
    text = re.sub(r"<[^>]+>", " ", markup)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return "\n".join(" ".join(line.split()) for line in text.splitlines()).strip()


def looks_automatic(msg: EmailMessage) -> bool:
    """Bounces, vacation responders and other machines must never trigger a reply."""
    auto_submitted = (msg.get("Auto-Submitted") or "").lower().strip()
    if auto_submitted and auto_submitted != "no":
        return True
    if (msg.get("X-Auto-Response-Suppress") or "").strip():
        return True
    if (msg.get("Precedence") or "").lower().strip() in {"bulk", "junk", "list", "auto_reply"}:
        return True
    if msg.get("List-Id") or msg.get("List-Unsubscribe"):
        return True
    sender = (msg.get("From") or "").strip()
    address = sender.split("<")[-1].strip("> ")
    if BOUNCE_SENDERS.match(address):
        return True
    return msg.get_content_type() in {"multipart/report"}
