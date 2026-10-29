"""Optional LLM assistance (OpenAI or Anthropic).

Two narrow jobs, both safe to skip:

* ``extract_request``: when the deterministic parser cannot decide, ask the model for a JSON
  object ``{"matter": "M12205", "document_type": "Other Documents"}``.
* ``polish_reply``: rewrite the templated reply in a friendlier tone. The result is discarded
  unless every number and the matter id from the template survive verbatim, so the model can
  never change a count or a date.

The SDKs are optional extras; importing this module never fails.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from .config import Settings
from .models import DocumentType, MatterRequest

log = logging.getLogger(__name__)

EXTRACT_SYSTEM = (
    "You extract structured data from emails sent to a regulatory filings assistant. "
    "Return ONLY a JSON object with keys 'matter' (a string like M12205, or null) and "
    "'document_type' (one of: Exhibits, Key Documents, Other Documents, Transcripts, "
    "Recordings, or null). Use null when the email does not clearly specify a value."
)

POLISH_SYSTEM = (
    "You rewrite short business emails to be warm and clear. Keep every fact, number, date, "
    "matter id and file name exactly as given. Do not add facts. Do not add a subject line. "
    "Return only the rewritten email body."
)


class LLMClient(Protocol):
    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str: ...


class OpenAIClient:
    def __init__(self, api_key: str, model: str) -> None:
        from openai import OpenAI  # type: ignore[import-not-found]

        self._client = OpenAI(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0,
            **kwargs,
        )
        return resp.choices[0].message.content or ""


class AnthropicClient:
    def __init__(self, api_key: str, model: str) -> None:
        from anthropic import Anthropic  # type: ignore[import-not-found]

        self._client = Anthropic(api_key=api_key)
        self._model = model

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(getattr(block, "text", "") for block in resp.content)


def build_client(settings: Settings) -> LLMClient | None:
    try:
        if settings.llm_provider == "openai" and settings.openai_api_key:
            return OpenAIClient(settings.openai_api_key, settings.openai_model)
        if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
            return AnthropicClient(settings.anthropic_api_key, settings.anthropic_model)
    except ImportError as exc:
        log.warning("LLM provider %s requested but SDK missing: %s", settings.llm_provider, exc)
    return None


def extract_request(client: LLMClient, subject: str, body: str) -> MatterRequest | None:
    try:
        raw = client.complete(
            EXTRACT_SYSTEM, f"Subject: {subject}\n\n{body[:4000]}", json_mode=True
        )
        data = json.loads(_strip_fences(raw))
        matter = data.get("matter")
        doc_type = data.get("document_type")
        if not matter or not doc_type:
            return None
        return MatterRequest(str(matter), DocumentType(str(doc_type)))
    except Exception as exc:  # any LLM failure just means "no help"
        log.warning("LLM extraction failed: %s", exc)
        return None


def polish_reply(client: LLMClient, draft: str) -> str:
    try:
        candidate = client.complete(POLISH_SYSTEM, draft).strip()
    except Exception as exc:
        log.warning("LLM polish failed: %s", exc)
        return draft
    if not candidate or not facts_preserved(draft, candidate):
        log.info("LLM polish discarded: facts not preserved")
        return draft
    return candidate


_FACT_RE = re.compile(
    r"\bM\d{5}\b|\$[\d,]+(?:\.\d+)?|\b\d[\d,]*\b|\b[\w-]+\.(?:pdf|zip|docx?|xlsx?)\b", re.I
)


def facts_preserved(original: str, candidate: str) -> bool:
    """Every number, dollar amount, matter id and file name in ``original`` must remain."""
    wanted = {m.group(0).lower() for m in _FACT_RE.finditer(original)}
    have = {m.group(0).lower() for m in _FACT_RE.finditer(candidate)}
    return wanted.issubset(have)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text
