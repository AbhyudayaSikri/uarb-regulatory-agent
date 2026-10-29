from __future__ import annotations

from typing import Protocol

from ..models import InboundMessage, OutboundMessage


class Inbox(Protocol):
    def fetch_unprocessed(self) -> list[InboundMessage]:
        """Return new request emails, oldest first, without marking them processed."""
        ...

    def mark_processed(self, message: InboundMessage) -> None: ...


class Outbox(Protocol):
    def send(self, message: OutboundMessage) -> None: ...
