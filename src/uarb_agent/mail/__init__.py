"""Mailbox access: IMAP for requests, SMTP for replies, in-memory fakes for tests."""

from .base import Inbox, Outbox
from .fake import FakeInbox, FakeOutbox
from .inbox import ImapInbox
from .outbox import SmtpOutbox, write_eml

__all__ = ["FakeInbox", "FakeOutbox", "ImapInbox", "Inbox", "Outbox", "SmtpOutbox", "write_eml"]
