"""Typed data passed between the mail, intent, scraper, zip and summary layers."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

MATTER_RE = re.compile(r"\bM\d{5}\b", re.IGNORECASE)


class DocumentType(enum.StrEnum):
    """The five document tabs on a UARB matter page, in the order the site shows them."""

    EXHIBITS = "Exhibits"
    KEY_DOCUMENTS = "Key Documents"
    OTHER_DOCUMENTS = "Other Documents"
    TRANSCRIPTS = "Transcripts"
    RECORDINGS = "Recordings"

    @property
    def singular(self) -> str:
        return {
            DocumentType.EXHIBITS: "Exhibit",
            DocumentType.KEY_DOCUMENTS: "Key Document",
            DocumentType.OTHER_DOCUMENTS: "Other Document",
            DocumentType.TRANSCRIPTS: "Transcript",
            DocumentType.RECORDINGS: "Recording",
        }[self]

    @property
    def slug(self) -> str:
        return self.value.lower().replace(" ", "-")


def normalise_matter(raw: str) -> str:
    """Return the canonical ``M12345`` form or raise ``ValueError``."""
    match = MATTER_RE.search(raw or "")
    if not match:
        raise ValueError(f"not a matter number: {raw!r}")
    return match.group(0).upper()


@dataclass(frozen=True)
class MatterRequest:
    """What a user asked for, after parsing their email."""

    matter: str
    document_type: DocumentType

    def __post_init__(self) -> None:
        object.__setattr__(self, "matter", normalise_matter(self.matter))


@dataclass
class InboundMessage:
    """A request email pulled from the agent's mailbox."""

    message_id: str
    sender: str
    subject: str
    body: str
    received_at: datetime = field(default_factory=datetime.now)
    references: list[str] = field(default_factory=list)
    is_auto_reply: bool = False


@dataclass
class MatterMetadata:
    """The header block shown at the top of a matter page."""

    matter: str
    title: str = ""
    status: str = ""
    type: str = ""
    category: str = ""
    date_received: date | None = None
    date_final_submissions: date | None = None
    outcome: str = ""
    raw_fields: dict[str, str] = field(default_factory=dict)


@dataclass
class TabCounts:
    """Number of items behind each document tab, read from the tab labels."""

    exhibits: int = 0
    key_documents: int = 0
    other_documents: int = 0
    transcripts: int = 0
    recordings: int = 0

    def for_type(self, doc_type: DocumentType) -> int:
        return {
            DocumentType.EXHIBITS: self.exhibits,
            DocumentType.KEY_DOCUMENTS: self.key_documents,
            DocumentType.OTHER_DOCUMENTS: self.other_documents,
            DocumentType.TRANSCRIPTS: self.transcripts,
            DocumentType.RECORDINGS: self.recordings,
        }[doc_type]

    def set_for_type(self, doc_type: DocumentType, value: int) -> None:
        attr = {
            DocumentType.EXHIBITS: "exhibits",
            DocumentType.KEY_DOCUMENTS: "key_documents",
            DocumentType.OTHER_DOCUMENTS: "other_documents",
            DocumentType.TRANSCRIPTS: "transcripts",
            DocumentType.RECORDINGS: "recordings",
        }[doc_type]
        setattr(self, attr, value)

    @property
    def total(self) -> int:
        return (
            self.exhibits
            + self.key_documents
            + self.other_documents
            + self.transcripts
            + self.recordings
        )

    def as_dict(self) -> dict[DocumentType, int]:
        return {dt: self.for_type(dt) for dt in DocumentType}


@dataclass
class DocumentRow:
    """One row in a document list (before download)."""

    index: int
    doc_no: str
    title: str
    security: str = "Public"
    file_type: str = ""
    date: date | None = None

    @property
    def is_confidential(self) -> bool:
        return "confidential" in self.security.lower()


@dataclass
class DownloadedFile:
    """A document that was saved to disk."""

    row: DocumentRow
    path: Path
    size_bytes: int

    @property
    def filename(self) -> str:
        return self.path.name


@dataclass
class SkippedDocument:
    row: DocumentRow
    reason: str


@dataclass
class ScrapeResult:
    """Everything the scraper learned about a matter plus the files it saved."""

    request: MatterRequest
    metadata: MatterMetadata
    counts: TabCounts
    downloaded: list[DownloadedFile] = field(default_factory=list)
    skipped: list[SkippedDocument] = field(default_factory=list)
    listed_rows: list[DocumentRow] = field(default_factory=list)

    @property
    def available(self) -> int:
        return self.counts.for_type(self.request.document_type)


@dataclass
class ZipResult:
    path: Path
    size_bytes: int
    included: list[DownloadedFile]
    dropped_for_size: list[DownloadedFile] = field(default_factory=list)


@dataclass
class OutboundMessage:
    """A reply ready for SMTP (or for writing to disk in dry-run mode)."""

    to: str
    subject: str
    text_body: str
    html_body: str | None = None
    attachments: list[Path] = field(default_factory=list)
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)


@dataclass
class JobResult:
    """Outcome of handling one inbound request."""

    inbound: InboundMessage | None
    request: MatterRequest | None
    ok: bool
    reply: OutboundMessage | None = None
    scrape: ScrapeResult | None = None
    zip: ZipResult | None = None
    error: str | None = None


class MatterNotFoundError(Exception):
    """The portal reported no matter for the requested number."""


class PortalError(Exception):
    """The portal misbehaved (session expired, communication problem, layout changed...)."""
