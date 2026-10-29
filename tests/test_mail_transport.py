"""The real transports: SMTP against a local aiosmtpd server, IMAP against a protocol stub."""

import email
import email.policy
import socket
from pathlib import Path

import pytest
from aiosmtpd.controller import Controller

from uarb_agent.config import Settings
from uarb_agent.mail.inbox import ImapInbox
from uarb_agent.mail.outbox import SmtpOutbox
from uarb_agent.models import OutboundMessage


class _Collector:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 Message accepted for delivery"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def smtp_server():
    handler = _Collector()
    controller = Controller(handler, hostname="127.0.0.1", port=_free_port())
    controller.start()
    try:
        yield controller, handler
    finally:
        controller.stop()


def test_smtp_outbox_sends_multipart_with_zip(smtp_server, tmp_path: Path):
    controller, handler = smtp_server
    attachment = tmp_path / "M12205-exhibits.zip"
    attachment.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    settings = Settings(
        smtp_host=controller.hostname,
        smtp_port=controller.port,
        smtp_user="",
        smtp_from="agent@example.com",
    )
    SmtpOutbox(settings).send(
        OutboundMessage(
            to="User <user@example.com>",
            subject="Re: M12205",
            text_body="hello",
            html_body="<p>hello</p>",
            attachments=[attachment],
            in_reply_to="<req@example.com>",
            references=["<req@example.com>"],
        )
    )
    assert len(handler.messages) == 1
    env = handler.messages[0]
    assert env.mail_from == "agent@example.com"
    assert env.rcpt_tos == ["user@example.com"]
    msg = email.message_from_bytes(env.content, policy=email.policy.default)
    assert msg["In-Reply-To"] == "<req@example.com>"
    assert msg["Auto-Submitted"] == "auto-replied"
    parts = [(p.get_content_type(), p.get_filename()) for p in msg.walk() if not p.is_multipart()]
    assert parts == [("text/plain", None), ("text/html", None), ("application/zip",
                                                                  "M12205-exhibits.zip")]


RAW_1 = (
    b"From: A <a@example.com>\r\nSubject: one\r\nMessage-ID: <1@example.com>\r\n"
    b"Date: Tue, 28 Oct 2026 10:00:00 -0400\r\nContent-Type: text/plain\r\n\r\nexhibits M12205\r\n"
)
RAW_2 = (
    b"From: B <b@example.com>\r\nSubject: two\r\nMessage-ID: <2@example.com>\r\n"
    b"Date: Tue, 28 Oct 2026 09:00:00 -0400\r\nContent-Type: text/plain\r\n\r\nkey docs M12205\r\n"
)


class StubIMAP:
    """Just enough of imaplib.IMAP4 for ImapInbox: UID SEARCH / FETCH (BODY.PEEK) / STORE.

    Mailbox state is class-level because ImapInbox opens a new connection per call, exactly
    like talking to a real server.
    """

    instances: list["StubIMAP"] = []
    flags: dict[bytes, set[str]] = {}
    store: dict[bytes, bytes] = {b"7": RAW_1, b"9": RAW_2}

    @classmethod
    def reset(cls):
        cls.instances.clear()
        cls.flags = {b"7": set(), b"9": set()}

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.log: list[str] = []
        StubIMAP.instances.append(self)

    def login(self, user, password):
        self.log.append(f"login {user}")
        return "OK", [b"ok"]

    def select(self, folder):
        self.log.append(f"select {folder}")
        return "OK", [b"2"]

    def uid(self, command, *args):
        command = command.lower()
        if command == "search":
            unseen = [u for u, f in self.flags.items() if "\\Seen" not in f]
            return "OK", [b" ".join(unseen)]
        if command == "fetch":
            uid, spec = args
            assert spec == "(BODY.PEEK[])", spec  # must not set \Seen implicitly
            return "OK", [(uid + b" (BODY[] {n})", self.store[uid]), b")"]
        if command == "store":
            uid, op, _flags = args
            assert op == "+FLAGS"
            self.flags[uid].add("\\Seen")
            return "OK", [uid]
        raise AssertionError(command)

    def logout(self):
        self.log.append("logout")
        return "BYE", [b"bye"]


def test_imap_inbox_fetches_unseen_and_marks_seen(monkeypatch):
    StubIMAP.reset()
    monkeypatch.setattr("uarb_agent.mail.inbox.imaplib.IMAP4_SSL", StubIMAP)
    inbox = ImapInbox(Settings(imap_host="imap.example.com", imap_user="u", imap_password="p"))

    messages = inbox.fetch_unprocessed()
    # oldest first, regardless of UID order
    assert [m.message_id for m in messages] == ["<2@example.com>", "<1@example.com>"]
    assert messages[1].body == "exhibits M12205"
    assert StubIMAP.instances[0].log == ["login u", "select INBOX", "logout"]

    inbox.mark_processed(messages[0])
    assert StubIMAP.flags[b"9"] == {"\\Seen"}
    assert StubIMAP.flags[b"7"] == set()

    # Second poll only sees the message that was not marked.
    remaining = inbox.fetch_unprocessed()
    assert [m.message_id for m in remaining] == ["<1@example.com>"]
