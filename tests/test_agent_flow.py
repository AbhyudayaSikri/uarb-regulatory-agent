"""End-to-end through the Agent with a fake inbox, fake outbox and fake scraper."""

import email
import email.policy
import zipfile

from uarb_agent.agent import Agent
from uarb_agent.config import Settings
from uarb_agent.mail import FakeInbox, FakeOutbox, write_eml
from uarb_agent.mail.parsing import parse_rfc822
from uarb_agent.models import DocumentType, InboundMessage, MatterRequest
from uarb_agent.scraper.fake import FakeScraper


def _settings(tmp_path, **overrides) -> Settings:
    base = {"work_dir": tmp_path / "work", "llm_provider": "none", "smtp_from": "agent@example.com"}
    base.update(overrides)
    return Settings(**base)


def _msg(body, sender="User <user@example.com>", subject="Request", mid="<m1@example.com>",
         **kw) -> InboundMessage:
    return InboundMessage(message_id=mid, sender=sender, subject=subject, body=body, **kw)


def test_happy_path_sends_zip(tmp_path):
    example = "Hi Agent, Can you give me Other Documents files from M12205? Thanks!"
    inbox = FakeInbox([_msg(example)])
    outbox = FakeOutbox()
    scraper = FakeScraper()
    agent = Agent(_settings(tmp_path), scraper, inbox, outbox, keep_files=True)

    results = agent.poll_once()

    assert len(results) == 1 and results[0].ok
    assert scraper.calls == [(MatterRequest("M12205", DocumentType.OTHER_DOCUMENTS), 10)]
    assert inbox.processed == ["<m1@example.com>"]
    reply = outbox.sent[0]
    assert reply.to == "User <user@example.com>"
    assert reply.in_reply_to == "<m1@example.com>"
    assert "I downloaded 10 out of the 44 Other Documents" in reply.text_body
    assert len(reply.attachments) == 1
    with zipfile.ZipFile(reply.attachments[0]) as zf:
        assert len(zf.namelist()) == 11


def test_unparseable_request_gets_help(tmp_path):
    inbox = FakeInbox([_msg("hello, can you help me?")])
    outbox = FakeOutbox()
    agent = Agent(_settings(tmp_path), FakeScraper(), inbox, outbox)
    results = agent.poll_once()
    assert not results[0].ok
    assert "Example:" in outbox.sent[0].text_body
    assert inbox.processed == ["<m1@example.com>"]


def test_unknown_matter_gets_not_found(tmp_path):
    inbox = FakeInbox([_msg("exhibits for M00001 please")])
    outbox = FakeOutbox()
    agent = Agent(_settings(tmp_path), FakeScraper(), inbox, outbox)
    agent.poll_once()
    assert "could not find matter M00001" in outbox.sent[0].text_body


def test_auto_replies_and_disallowed_senders_are_ignored(tmp_path):
    inbox = FakeInbox([
        _msg("exhibits for M12205", mid="<auto>", is_auto_reply=True),
        _msg("exhibits for M12205", mid="<stranger>", sender="x@elsewhere.org"),
        _msg("exhibits for M12205", mid="<ok>", sender="me@allowed.com"),
    ])
    outbox = FakeOutbox()
    agent = Agent(_settings(tmp_path, allowed_senders="@allowed.com"), FakeScraper(), inbox, outbox)
    results = agent.poll_once()
    assert [r.error for r in results[:2]] == ["auto-reply ignored", "sender not allowed"]
    assert results[2].ok
    assert len(outbox.sent) == 1
    assert len(inbox.processed) == 3


def test_rate_limit(tmp_path):
    msgs = [_msg("exhibits for M12205", mid=f"<{i}>") for i in range(3)]
    outbox = FakeOutbox()
    agent = Agent(_settings(tmp_path, rate_limit_per_sender_per_hour=2), FakeScraper(),
                  FakeInbox(msgs), outbox)
    results = agent.poll_once()
    assert [r.ok for r in results] == [True, True, False]
    assert "hourly request limit" in outbox.sent[-1].text_body


def test_send_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr("uarb_agent.agent.time.sleep", lambda *_: None)
    outbox = FakeOutbox(fail_times=2)
    agent = Agent(_settings(tmp_path), FakeScraper(), FakeInbox([_msg("key docs M12205")]), outbox)
    agent.poll_once()
    assert len(outbox.sent) == 1


def test_eml_round_trip(tmp_path):
    outbox = FakeOutbox()
    agent = Agent(_settings(tmp_path), FakeScraper(), FakeInbox([_msg("exhibits M12205")]), outbox,
                  keep_files=True)
    agent.poll_once()
    path = write_eml(outbox.sent[0], "agent@example.com", tmp_path / "reply.eml")
    msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
    assert msg["To"] == "User <user@example.com>"
    assert msg["In-Reply-To"] == "<m1@example.com>"
    assert msg["Auto-Submitted"] == "auto-replied"
    attachments = [p for p in msg.iter_attachments()]
    assert [a.get_filename() for a in attachments] == ["M12205-exhibits.zip"]
    assert msg.get_body(preferencelist=("plain",)).get_content().startswith("Hi User,")
    assert "<ul>" in msg.get_body(preferencelist=("html",)).get_content()


def test_parse_rfc822_plain_and_html_and_auto():
    raw = (
        b"From: Jane <jane@example.com>\r\nTo: agent@example.com\r\nSubject: M12205\r\n"
        b"Message-ID: <x@example.com>\r\nDate: Tue, 28 Oct 2026 21:00:00 -0400\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\nOther documents please\r\n"
    )
    m = parse_rfc822(raw)
    assert m.sender == "Jane <jane@example.com>"
    assert m.body == "Other documents please"
    assert m.received_at.hour == 21
    assert not m.is_auto_reply

    html = (
        b"From: Jane <jane@example.com>\r\nSubject: hi\r\nMessage-ID: <y@example.com>\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><body><p>Key <b>docs</b> for M12205</p><p>thanks</p></body></html>\r\n"
    )
    assert parse_rfc822(html).body == "Key docs for M12205\nthanks"

    auto = (
        b"From: MAILER-DAEMON@example.com\r\nSubject: Undeliverable\r\n"
        b"Auto-Submitted: auto-replied\r\nContent-Type: text/plain\r\n\r\nbounce\r\n"
    )
    assert parse_rfc822(auto).is_auto_reply
