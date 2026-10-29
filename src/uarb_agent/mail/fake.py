from __future__ import annotations

from ..models import InboundMessage, OutboundMessage


class FakeInbox:
    def __init__(self, messages: list[InboundMessage] | None = None) -> None:
        self.messages = list(messages or [])
        self.processed: list[str] = []

    def fetch_unprocessed(self) -> list[InboundMessage]:
        return [m for m in self.messages if m.message_id not in self.processed]

    def mark_processed(self, message: InboundMessage) -> None:
        self.processed.append(message.message_id)


class FakeOutbox:
    def __init__(self, fail_times: int = 0) -> None:
        self.sent: list[OutboundMessage] = []
        self.fail_times = fail_times

    def send(self, message: OutboundMessage) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("smtp down")
        self.sent.append(message)
