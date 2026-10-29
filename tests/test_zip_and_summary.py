import os
import zipfile
from datetime import date
from pathlib import Path

from uarb_agent.models import (
    DocumentRow,
    DocumentType,
    DownloadedFile,
    InboundMessage,
    MatterRequest,
    ScrapeResult,
    SkippedDocument,
    TabCounts,
)
from uarb_agent.scraper.fake import M12205, M12205_COUNTS, FakeScraper
from uarb_agent.summary import (
    build_reply,
    counts_sentence,
    error_reply,
    greeting_name,
    long_date,
)
from uarb_agent.zipper import build_zip


def _result(tmp_path: Path, doc_type=DocumentType.OTHER_DOCUMENTS, limit=10) -> ScrapeResult:
    return FakeScraper().fetch(MatterRequest("M12205", doc_type), tmp_path / "dl", limit)


def test_zip_contains_files_and_manifest(tmp_path):
    result = _result(tmp_path)
    zipped = build_zip(result, tmp_path, max_bytes=50 * 1024 * 1024)
    assert zipped.path.name == "M12205-other-documents.zip"
    with zipfile.ZipFile(zipped.path) as zf:
        names = zf.namelist()
        assert "MANIFEST.csv" in names
        assert len(names) == 11
        manifest = zf.read("MANIFEST.csv").decode()
    assert manifest.splitlines()[0].startswith("matter,document_type,order,doc_no,title")
    assert "100001,Other Document 1,2025-04-01,Public,100001.pdf" in manifest
    assert zipped.dropped_for_size == []


def test_zip_drops_largest_until_it_fits(tmp_path):
    result = _result(tmp_path)
    big = tmp_path / "dl" / "big.pdf"
    big.write_bytes(b"%PDF-" + os.urandom(1_000_000))  # random bytes do not compress
    result.downloaded.append(
        DownloadedFile(DocumentRow(99, "199999", "Huge attachment", date=date(2025, 5, 1)), big,
                       big.stat().st_size)
    )
    zipped = build_zip(result, tmp_path, max_bytes=200 * 1024)
    assert [f.filename for f in zipped.dropped_for_size] == ["big.pdf"]
    assert zipped.size_bytes <= 200 * 1024
    assert len(zipped.included) == 10


def test_counts_sentence_matches_assignment_style():
    s = counts_sentence(M12205_COUNTS)
    assert s == (
        "I found 13 Exhibits, 6 Key Documents, 44 Other Documents, and no Transcripts or "
        "Recordings (63 files in total)."
    )
    assert counts_sentence(TabCounts(exhibits=1)) == (
        "I found 1 Exhibit and no Key Documents, Other Documents, Transcripts, or Recordings "
        "(1 file in total)."
    )
    assert counts_sentence(TabCounts()).startswith("I found no documents")


def test_long_date_and_greeting():
    assert long_date(date(2025, 4, 7)) == "April 7, 2025"
    assert greeting_name("Jane Doe <jane@example.com>") == "Jane"
    assert greeting_name("jane.doe@example.com") == "Jane"
    assert greeting_name("12345@example.com") == "there"
    assert greeting_name(None) == "there"


def test_full_reply_text(tmp_path):
    result = _result(tmp_path)
    zipped = build_zip(result, tmp_path, max_bytes=50 * 1024 * 1024)
    inbound = InboundMessage(
        message_id="<abc@example.com>",
        sender="User <user@example.com>",
        subject="Other documents from M12205",
        body="Hi Agent, Can you give me Other Documents files from M12205? Thanks!",
    )
    reply = build_reply(result, zipped, inbound, max_mb=20)
    text = reply.text_body
    assert text.startswith("Hi User,")
    assert (
        "M12205 is about the Halifax Regional Water Commission - Windsor Street Exchange "
        "Redevelopment Project - $69,275,000." in text
    )
    assert "It relates to Capital Expenditure Approvals within the Water category." in text
    assert 'The matter is Closed with outcome "Directions given".' in text
    assert "initial filing on April 7, 2025 and a final filing on October 23, 2025." in text
    assert "I found 13 Exhibits, 6 Key Documents, 44 Other Documents, and no Transcripts" in text
    assert "I downloaded 10 out of the 44 Other Documents and am attaching them as a ZIP" in text
    assert "Attached files:" in text
    assert "100001: Other Document 1 (2025-04-01) - 100001.pdf" in text
    assert reply.subject == "Re: Other documents from M12205"
    assert reply.to == "User <user@example.com>"
    assert reply.in_reply_to == "<abc@example.com>"
    assert reply.attachments == [zipped.path]
    assert "<ul>" in reply.html_body


def test_reply_for_empty_tab(tmp_path):
    result = _result(tmp_path, DocumentType.TRANSCRIPTS)
    reply = build_reply(result, None, None, max_mb=20, to="someone@example.com")
    assert "This matter has no Transcripts, so there is nothing to attach." in reply.text_body
    assert reply.attachments == []
    assert reply.subject == "Re: M12205 Transcripts from the UARB portal"


def test_reply_mentions_confidential_and_dropped(tmp_path):
    result = FakeScraper(confidential_every=3).fetch(
        MatterRequest("M12205", DocumentType.EXHIBITS), tmp_path / "dl", 5
    )
    result.skipped.append(SkippedDocument(DocumentRow(50, "H-50", "x"), "download failed: boom"))
    zipped = build_zip(result, tmp_path, max_bytes=50 * 1024 * 1024)
    zipped.dropped_for_size.append(zipped.included.pop())
    dropped_name = zipped.dropped_for_size[0].filename
    reply = build_reply(result, zipped, None, max_mb=20, to="a@b.c")
    assert "I skipped 2 confidential documents" in reply.text_body
    assert "1 download failed on the portal and was left out." in reply.text_body
    assert f"To stay under the 20 MB attachment limit I left out {dropped_name}" in reply.text_body
    assert "the ZIP contains 4 files." in reply.text_body


def test_error_replies():
    inbound = InboundMessage("<id>", "a@b.c", "hello", "body")
    assert "M99999" in error_reply(inbound, "not_found", "M99999").text_body
    assert "Example:" in error_reply(inbound, "parse", "no matter number found").text_body
    assert "hourly request limit" in error_reply(inbound, "rate_limited").text_body
    assert "did not respond" in error_reply(inbound, "portal", "timeout").text_body
    assert error_reply(inbound, "portal").subject == "Re: hello"


def test_fake_metadata_matches_live_shape():
    assert M12205.date_received == date(2025, 4, 7)
    assert M12205_COUNTS.total == 63
