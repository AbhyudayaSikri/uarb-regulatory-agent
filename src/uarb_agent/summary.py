"""Compose the reply email: matter summary, per-tab counts, what was attached, and why not."""

from __future__ import annotations

import html
import re
from datetime import date

from .intent import HELP_TEXT
from .models import (
    DocumentType,
    InboundMessage,
    OutboundMessage,
    ScrapeResult,
    TabCounts,
    ZipResult,
)

SIGNATURE = "UARB Regulatory Agent"


def long_date(value: date | None) -> str:
    """``date(2025, 4, 7)`` -> ``April 7, 2025``."""
    if value is None:
        return "an unknown date"
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def plural(n: int, doc_type: DocumentType) -> str:
    return doc_type.singular if n == 1 else doc_type.value


def counts_sentence(counts: TabCounts) -> str:
    """``I found 13 Exhibits, 6 Key Documents, 44 Other Documents, and no Transcripts or
    Recordings (63 files in total).``"""
    present = [f"{n} {plural(n, dt)}" for dt, n in counts.as_dict().items() if n > 0]
    absent = [dt.value for dt, n in counts.as_dict().items() if n == 0]
    parts: list[str] = []
    if present:
        parts.extend(present)
    if absent:
        parts.append("no " + _join(absent, "or"))
    if not present:
        return "I found no documents at all in this matter (0 files in total)."
    total = counts.total
    return (
        f"I found {_join(parts, 'and')} "
        f"({total} {'file' if total == 1 else 'files'} in total)."
    )


def _join(items: list[str], word: str) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} {word} {items[1]}"
    return ", ".join(items[:-1]) + f", {word} {items[-1]}"


def greeting_name(sender: str | None) -> str:
    if not sender:
        return "there"
    display = re.sub(r"<.*?>", "", sender).strip().strip('"')
    if display and "@" not in display:
        return display.split()[0]
    local = sender.split("<")[-1].split("@")[0].strip('"> ')
    first = re.split(r"[._\-+]", local)[0]
    return first.capitalize() if first and first.isalpha() else "there"


def about_paragraph(result: ScrapeResult) -> str:
    m = result.metadata
    sentences = [f"{m.matter} is about the {m.title}."]
    if m.category and m.type:
        sentences.append(f"It relates to {m.category} within the {m.type} category.")
    elif m.category or m.type:
        sentences.append(f"It falls under {m.category or m.type}.")
    status_bits = []
    if m.status:
        status_bits.append(f"The matter is {m.status}")
    if m.outcome:
        status_bits.append(
            f'with outcome "{m.outcome}"' if m.status else f'The outcome is "{m.outcome}"'
        )
    if status_bits:
        sentences.append(" ".join(status_bits) + ".")
    if m.date_received or m.date_final_submissions:
        if m.date_received and m.date_final_submissions:
            sentences.append(
                f"It had an initial filing on {long_date(m.date_received)} and a final filing on "
                f"{long_date(m.date_final_submissions)}."
            )
        elif m.date_received:
            sentences.append(f"It had an initial filing on {long_date(m.date_received)}.")
        else:
            sentences.append(f"Its final filing was on {long_date(m.date_final_submissions)}.")
    return " ".join(sentences)


def attachment_paragraph(result: ScrapeResult, zipped: ZipResult | None, max_mb: float) -> str:
    doc_type = result.request.document_type
    available = result.available
    if available == 0:
        return f"This matter has no {doc_type.value}, so there is nothing to attach."

    attached = len(zipped.included) if zipped else 0
    downloaded = len(result.downloaded)
    dropped = zipped.dropped_for_size if zipped else []
    sentences: list[str] = []
    if attached and not dropped:
        sentences.append(
            f"I downloaded {downloaded} out of the {available} {plural(available, doc_type)} "
            f"and am attaching {'them' if downloaded > 1 else 'it'} as a ZIP here"
            + (f" ({zipped.path.name})." if zipped else ".")
        )
    elif dropped:
        names = _join([f"{f.filename} at {_mb(f.size_bytes)}" for f in dropped], "and")
        n = len(dropped)
        tail = (
            f"so the attached ZIP ({zipped.path.name}) contains the remaining {attached} "
            f"{'file' if attached == 1 else 'files'}."
            if attached
            else "so I could not attach any of them."
        )
        sentences.append(
            f"I downloaded {downloaded} out of the {available} {plural(available, doc_type)}. "
            f"{n} of {'them' if downloaded > 1 else 'it'} ({names}) "
            f"{'exceeds' if n == 1 else 'exceed'} the {max_mb:g} MB email attachment limit, {tail}"
        )
    else:
        sentences.append(
            f"I was not able to attach any of the {available} {plural(available, doc_type)}."
        )

    confidential = [s for s in result.skipped if s.reason == "confidential"]
    failed = [s for s in result.skipped if s.reason != "confidential"]
    if confidential:
        n = len(confidential)
        sentences.append(
            f"I skipped {n} confidential {'document' if n == 1 else 'documents'} that "
            f"cannot be downloaded from the public portal."
        )
    if failed:
        n = len(failed)
        sentences.append(
            f"{n} {'download' if n == 1 else 'downloads'} failed on the portal and "
            f"{'was' if n == 1 else 'were'} left out."
        )
    return " ".join(sentences)


def _mb(size_bytes: int) -> str:
    return f"{size_bytes / 1024 / 1024:.1f} MB"


def file_list(zipped: ZipResult | None) -> list[str]:
    if not zipped or not zipped.included:
        return []
    lines = []
    for f in zipped.included:
        when = f" ({f.row.date.isoformat()})" if f.row.date else ""
        lines.append(f"{f.row.doc_no}: {f.row.title}{when} - {f.filename}")
    return lines


def build_reply(
    result: ScrapeResult,
    zipped: ZipResult | None,
    inbound: InboundMessage | None,
    *,
    max_mb: float,
    to: str | None = None,
) -> OutboundMessage:
    name = greeting_name(inbound.sender if inbound else to)
    paragraphs = [
        f"Hi {name},",
        about_paragraph(result),
        counts_sentence(result.counts),
        attachment_paragraph(result, zipped, max_mb),
    ]
    files = file_list(zipped)
    text = "\n\n".join(paragraphs)
    if files:
        text += "\n\nAttached files:\n" + "\n".join(f"  - {line}" for line in files)
    text += f"\n\nRegards,\n{SIGNATURE}"

    html_parts = [f"<p>{html.escape(p)}</p>" for p in paragraphs]
    if files:
        html_parts.append(
            "<p>Attached files:</p><ul>"
            + "".join(f"<li>{html.escape(line)}</li>" for line in files)
            + "</ul>"
        )
    html_parts.append(f"<p>Regards,<br>{SIGNATURE}</p>")

    return OutboundMessage(
        to=inbound.sender if inbound else (to or ""),
        subject=reply_subject(inbound, f"{result.request.matter} {result.request.document_type}"),
        text_body=text,
        html_body="".join(html_parts),
        attachments=[zipped.path] if zipped and zipped.included else [],
        in_reply_to=inbound.message_id if inbound else None,
        references=([*inbound.references, inbound.message_id]) if inbound else [],
    )


def reply_subject(inbound: InboundMessage | None, fallback: str) -> str:
    if inbound and inbound.subject.strip():
        subject = inbound.subject.strip()
        return subject if subject.lower().startswith("re:") else f"Re: {subject}"
    return f"Re: {fallback} from the UARB portal"


def error_reply(inbound: InboundMessage, kind: str, detail: str = "") -> OutboundMessage:
    name = greeting_name(inbound.sender)
    if kind == "parse":
        body = HELP_TEXT
        if detail:
            body = f"({detail.capitalize()}.)\n\n{body}"
    elif kind == "not_found":
        body = (
            f"I could not find matter {detail} on the UARB portal. Please double-check the "
            f"number (they look like M12205) and try again."
        )
    elif kind == "rate_limited":
        body = (
            "You have reached the hourly request limit for this agent. Please try again a "
            "little later."
        )
    else:
        body = (
            "The UARB portal did not respond as expected, so I could not complete your "
            "request this time. Please try again in a few minutes."
            + (f"\n\nDetails: {detail}" if detail else "")
        )
    text = f"Hi {name},\n\n{body}\n\nRegards,\n{SIGNATURE}"
    html_body = "".join(f"<p>{html.escape(p)}</p>" for p in text.split("\n\n"))
    return OutboundMessage(
        to=inbound.sender,
        subject=reply_subject(inbound, "your request"),
        text_body=text,
        html_body=html_body,
        in_reply_to=inbound.message_id,
        references=[*inbound.references, inbound.message_id],
    )
