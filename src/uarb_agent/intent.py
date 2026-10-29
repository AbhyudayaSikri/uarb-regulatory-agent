"""Turn a free-form request email into a :class:`MatterRequest`.

The deterministic parser handles everything the assignment describes ("Can you give me Other
Documents files from M12205?") plus common variations ("other docs", "key docs", "exhibit",
lower case, extra whitespace). An optional LLM pass is used only when the deterministic parser
cannot decide, so the agent works with no API key at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import MATTER_RE, DocumentType, MatterRequest

# Longest phrases first so "other documents" wins over "documents".
_TYPE_PATTERNS: list[tuple[DocumentType, re.Pattern[str]]] = [
    (DocumentType.OTHER_DOCUMENTS, re.compile(r"\bother\s+(?:documents?|docs?|files?)\b", re.I)),
    (DocumentType.KEY_DOCUMENTS, re.compile(r"\bkey\s+(?:documents?|docs?|files?)\b", re.I)),
    (DocumentType.TRANSCRIPTS, re.compile(r"\btranscripts?\b", re.I)),
    (DocumentType.RECORDINGS, re.compile(r"\b(?:recordings?|audio|hearing\s+recordings?)\b", re.I)),
    (DocumentType.EXHIBITS, re.compile(r"\bexhibits?\b", re.I)),
]

HELP_TEXT = (
    "I could not work out what you were asking for. Please send one matter number "
    "(for example M12205) and one document type: Exhibits, Key Documents, Other Documents, "
    "Transcripts, or Recordings.\n\n"
    'Example: "Hi Agent, can you give me Other Documents files from M12205? Thanks!"'
)


@dataclass
class ParseOutcome:
    request: MatterRequest | None
    problem: str | None = None
    matters_found: list[str] | None = None
    types_found: list[DocumentType] | None = None

    @property
    def ok(self) -> bool:
        return self.request is not None


def _strip_quoted_reply(body: str) -> str:
    """Drop quoted earlier messages so we only parse what the user actually typed."""
    lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if re.match(r"^On .+ wrote:$", stripped) or stripped.startswith("-----Original Message"):
            break
        if re.match(r"^From:\s.+", stripped) and lines:
            break
        lines.append(line)
    return "\n".join(lines)


def parse_request(subject: str, body: str) -> ParseOutcome:
    """Deterministic parse of subject + body. Never raises."""
    text = f"{subject or ''}\n{_strip_quoted_reply(body or '')}"

    matters: list[str] = []
    for m in MATTER_RE.finditer(text):
        value = m.group(0).upper()
        if value not in matters:
            matters.append(value)

    types: list[DocumentType] = []
    for doc_type, pattern in _TYPE_PATTERNS:
        if pattern.search(text) and doc_type not in types:
            types.append(doc_type)

    if not matters:
        return ParseOutcome(None, "no matter number found", matters, types)
    if len(matters) > 1:
        return ParseOutcome(None, f"more than one matter number found: {', '.join(matters)}",
                            matters, types)
    if not types:
        return ParseOutcome(None, "no document type found", matters, types)
    if len(types) > 1:
        # "exhibits" inside "other documents (not exhibits)" style sentences are ambiguous;
        # prefer the type that appears first in the text.
        first = min(types, key=lambda t: _first_pos(text, t))
        others = [t for t in types if t is not first]
        if _is_clearly_single(text, first, others):
            types = [first]
        else:
            return ParseOutcome(None,
                                "more than one document type found: "
                                + ", ".join(t.value for t in types), matters, types)

    return ParseOutcome(MatterRequest(matters[0], types[0]), None, matters, types)


def _first_pos(text: str, doc_type: DocumentType) -> int:
    pattern = next(p for t, p in _TYPE_PATTERNS if t is doc_type)
    m = pattern.search(text)
    return m.start() if m else len(text)


def _is_clearly_single(text: str, first: DocumentType, others: list[DocumentType]) -> bool:
    """True when the other matches are negated ("not the exhibits") or in a quoted example."""
    lowered = text.lower()
    for other in others:
        pattern = next(p for t, p in _TYPE_PATTERNS if t is other)
        for m in pattern.finditer(lowered):
            window = lowered[max(0, m.start() - 25): m.start()]
            if not re.search(r"\b(not|no|instead of|rather than|except|without)\b", window):
                return False
    return True
