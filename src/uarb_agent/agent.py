"""Glue: inbox -> intent -> scraper -> zip -> summary -> outbox."""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from . import llm as llm_mod
from .config import Settings
from .intent import parse_request
from .mail.base import Inbox, Outbox
from .models import (
    InboundMessage,
    JobResult,
    MatterNotFoundError,
    MatterRequest,
    OutboundMessage,
    PortalError,
)
from .scraper.base import Scraper
from .summary import build_reply, error_reply
from .zipper import build_zip

log = logging.getLogger(__name__)


class Agent:
    def __init__(
        self,
        settings: Settings,
        scraper: Scraper,
        inbox: Inbox | None = None,
        outbox: Outbox | None = None,
        llm_client: llm_mod.LLMClient | None = None,
        *,
        keep_files: bool = False,
    ) -> None:
        self.settings = settings
        self.scraper = scraper
        self.inbox = inbox
        self.outbox = outbox
        self.llm = llm_client if llm_client is not None else llm_mod.build_client(settings)
        self.keep_files = keep_files
        self._recent: dict[str, deque[float]] = defaultdict(deque)

    # ------------------------------------------------------------------------------ polling
    def poll_once(self) -> list[JobResult]:
        if self.inbox is None:
            raise RuntimeError("no inbox configured")
        results: list[JobResult] = []
        for inbound in self.inbox.fetch_unprocessed():
            try:
                results.append(self.handle(inbound))
            except Exception:
                log.exception("unhandled error for message %s", inbound.message_id)
                results.append(JobResult(inbound, None, ok=False, error="unhandled error"))
            finally:
                self.inbox.mark_processed(inbound)
        return results

    def serve(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        interval = self.settings.poll_interval_seconds
        log.info("polling %s every %ss", self.settings.imap_user, interval)
        while not stop.is_set():
            try:
                self.poll_once()
            except Exception:
                log.exception("poll failed; will retry")
            stop.wait(interval)

    # ------------------------------------------------------------------------ one message
    def handle(self, inbound: InboundMessage) -> JobResult:
        if inbound.is_auto_reply:
            log.info("ignoring automatic message %s from %s", inbound.message_id, inbound.sender)
            return JobResult(inbound, None, ok=False, error="auto-reply ignored")

        address = _address(inbound.sender)
        if not address or not self.settings.sender_allowed(address):
            log.info("ignoring message from non-allowed sender %s", inbound.sender)
            return JobResult(inbound, None, ok=False, error="sender not allowed")

        if self._rate_limited(address):
            reply = error_reply(inbound, "rate_limited")
            self._send(reply)
            return JobResult(inbound, None, ok=False, reply=reply, error="rate limited")

        request = self._parse(inbound)
        if isinstance(request, OutboundMessage):
            self._send(request)
            return JobResult(inbound, None, ok=False, reply=request, error="could not parse")

        return self.process_request(request, inbound=inbound)

    def process_request(
        self,
        request: MatterRequest,
        *,
        inbound: InboundMessage | None = None,
        to: str | None = None,
        send: bool = True,
    ) -> JobResult:
        """Fetch, zip, compose and (optionally) send. Used by both mail polling and the CLI."""
        work_dir = self._work_dir(request)
        limit = self.settings.max_documents
        try:
            scrape = self.scraper.fetch(request, work_dir / "downloads", limit)
        except MatterNotFoundError:
            log.info("matter %s not found", request.matter)
            reply = (
                error_reply(inbound, "not_found", request.matter)
                if inbound
                else _plain_reply(to, f"Matter {request.matter} was not found on the UARB portal.")
            )
            if send:
                self._send(reply)
            return JobResult(inbound, request, ok=False, reply=reply, error="matter not found")
        except PortalError as exc:
            log.error("portal error for %s: %s", request.matter, exc)
            reply = (
                error_reply(inbound, "portal", str(exc))
                if inbound
                else _plain_reply(to, f"The UARB portal failed: {exc}")
            )
            if send:
                self._send(reply)
            return JobResult(inbound, request, ok=False, reply=reply, error=str(exc))

        zipped = (
            build_zip(scrape, work_dir, self.settings.max_attachment_bytes)
            if scrape.downloaded
            else None
        )
        reply = build_reply(scrape, zipped, inbound, max_mb=self.settings.max_attachment_mb, to=to)
        if self.llm is not None:
            reply.text_body = llm_mod.polish_reply(self.llm, reply.text_body)
        if send:
            self._send(reply)
            if not self.keep_files:
                shutil.rmtree(work_dir, ignore_errors=True)
        return JobResult(inbound, request, ok=True, reply=reply, scrape=scrape, zip=zipped)

    # --------------------------------------------------------------------------- helpers
    def _parse(self, inbound: InboundMessage) -> MatterRequest | OutboundMessage:
        outcome = parse_request(inbound.subject, inbound.body)
        if outcome.ok and outcome.request is not None:
            return outcome.request
        if self.llm is not None:
            extracted = llm_mod.extract_request(self.llm, inbound.subject, inbound.body)
            if extracted is not None:
                return extracted
        log.info("could not parse message %s: %s", inbound.message_id, outcome.problem)
        return error_reply(inbound, "parse", outcome.problem or "")

    def _rate_limited(self, address: str) -> bool:
        limit = self.settings.rate_limit_per_sender_per_hour
        if limit <= 0:
            return False
        now = time.monotonic()
        window = self._recent[address]
        while window and now - window[0] > 3600:
            window.popleft()
        if len(window) >= limit:
            return True
        window.append(now)
        return False

    def _send(self, message: OutboundMessage) -> None:
        if self.outbox is None:
            log.info("no outbox configured; not sending reply to %s", message.to)
            return
        last: Exception | None = None
        for attempt in range(3):
            try:
                self.outbox.send(message)
                return
            except Exception as exc:
                last = exc
                log.warning("send attempt %d failed: %s", attempt + 1, exc)
                time.sleep(2 * (attempt + 1))
        log.error("giving up sending reply to %s: %s", message.to, last)

    def _work_dir(self, request: MatterRequest) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{stamp}-{request.matter}-{request.document_type.slug}"
        path = Path(self.settings.work_dir) / name
        path.mkdir(parents=True, exist_ok=True)
        return path


def _address(sender: str) -> str:
    m = re.search(r"<([^>]+)>", sender)
    return (m.group(1) if m else sender).strip().lower()


def _plain_reply(to: str | None, text: str) -> OutboundMessage:
    return OutboundMessage(to=to or "", subject="Re: your UARB request", text_body=text)
