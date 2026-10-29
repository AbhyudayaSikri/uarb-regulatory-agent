"""IMAP inbox: fetch unseen messages, mark them seen once handled."""

from __future__ import annotations

import imaplib
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from ..config import Settings
from ..models import InboundMessage
from .parsing import parse_rfc822

log = logging.getLogger(__name__)


class ImapInbox:
    """Each call opens a short-lived IMAP session, which keeps long-running polling simple
    and survives server-side idle timeouts."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._uids: dict[str, bytes] = {}

    @contextmanager
    def _connect(self) -> Iterator[imaplib.IMAP4]:
        s = self.settings
        conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
        try:
            conn.login(s.imap_user, s.imap_password)
            status, _ = conn.select(s.imap_folder)
            if status != "OK":
                raise RuntimeError(f"cannot select IMAP folder {s.imap_folder!r}")
            yield conn
        finally:
            try:
                conn.logout()
            except Exception:  # pragma: no cover - best effort
                log.debug("IMAP logout failed", exc_info=True)

    def fetch_unprocessed(self) -> list[InboundMessage]:
        messages: list[InboundMessage] = []
        with self._connect() as conn:
            status, data = conn.uid("search", None, "UNSEEN")
            if status != "OK":
                raise RuntimeError("IMAP search failed")
            uids = data[0].split() if data and data[0] else []
            for uid in uids:
                # BODY.PEEK does not set \Seen, so a crash before mark_processed leaves the
                # message to be retried on the next poll.
                status, parts = conn.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    log.warning("could not fetch IMAP uid %s", uid)
                    continue
                message = parse_rfc822(parts[0][1])
                self._uids[message.message_id] = uid
                messages.append(message)
        messages.sort(key=lambda m: m.received_at)
        log.info("IMAP: %d unseen message(s)", len(messages))
        return messages

    def mark_processed(self, message: InboundMessage) -> None:
        uid = self._uids.pop(message.message_id, None)
        if uid is None:
            return
        with self._connect() as conn:
            conn.uid("store", uid, "+FLAGS", "(\\Seen)")
