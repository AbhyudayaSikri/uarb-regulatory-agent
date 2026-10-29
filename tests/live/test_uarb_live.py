"""Opt-in tests against the real portal. Run with ``UARB_LIVE=1 pytest tests/live -m live``.

They download at most two files and take about a minute.
"""

import os
from pathlib import Path

import pytest

from uarb_agent.config import Settings
from uarb_agent.models import DocumentType, MatterNotFoundError, MatterRequest
from uarb_agent.scraper.uarb import UarbScraper

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("UARB_LIVE") != "1", reason="set UARB_LIVE=1 to run"),
]


@pytest.fixture(scope="module")
def scraper() -> UarbScraper:
    channel = os.environ.get("BROWSER_CHANNEL", "chromium")
    return UarbScraper(Settings(headless=True, browser_channel=channel))


def test_m12205_other_documents(scraper: UarbScraper, tmp_path: Path):
    result = scraper.fetch(MatterRequest("M12205", DocumentType.OTHER_DOCUMENTS), tmp_path, limit=2)
    meta = result.metadata
    assert meta.title.startswith("Halifax Regional Water Commission")
    assert meta.type == "Water"
    assert meta.category.startswith("Capital Expenditure")
    assert meta.date_received is not None and meta.date_received.isoformat() == "2025-04-07"
    assert meta.date_final_submissions is not None
    assert result.counts.exhibits >= 13
    assert result.counts.other_documents >= 44
    assert result.counts.transcripts == 0
    assert len(result.downloaded) == 2
    for f in result.downloaded:
        assert f.path.exists()
        assert f.size_bytes > 1000
        assert f.path.read_bytes()[:5] == b"%PDF-", f.path
        assert f.row.doc_no


def test_unknown_matter_raises(scraper: UarbScraper, tmp_path: Path):
    with pytest.raises(MatterNotFoundError):
        scraper.fetch(MatterRequest("M99999", DocumentType.EXHIBITS), tmp_path, limit=1)


def test_empty_tab_downloads_nothing(scraper: UarbScraper, tmp_path: Path):
    result = scraper.fetch(MatterRequest("M12205", DocumentType.TRANSCRIPTS), tmp_path, limit=1)
    assert result.counts.transcripts == 0
    assert result.downloaded == []
