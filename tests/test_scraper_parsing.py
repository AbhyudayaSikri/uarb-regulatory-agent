"""Tests for the pure parsing helpers in the scraper, using geometry/text recorded from the
live portal on 2026-10-28 for matter M12205."""

from datetime import date

import pytest

from uarb_agent.models import DocumentType, PortalError
from uarb_agent.scraper.uarb import (
    parse_date,
    parse_header_boxes,
    parse_row_text,
    parse_tab_counts,
)

# Exact output of HEADER_GEOMETRY_JS on the live matter page (1400px wide viewport).
HEADER_BOXES = [
    {"text": "Public Documents Database", "x": 7, "y": 96, "w": 406, "h": 38},
    {"text": "Matter No\nStatus", "x": 24, "y": 137, "w": 64, "h": 35},
    {"text": "Title - Description", "x": 163, "y": 137, "w": 114, "h": 19},
    {"text": "Type\nCategory", "x": 663, "y": 137, "w": 61, "h": 35},
    {"text": "Date Received", "x": 837, "y": 137, "w": 96, "h": 19},
    {"text": "Decision Date", "x": 949, "y": 137, "w": 91, "h": 19},
    {"text": "Outcome", "x": 1072, "y": 137, "w": 60, "h": 19},
    {"text": "M12205", "x": 13, "y": 182, "w": 133, "h": 18},
    {
        "text": "Halifax Regional Water Commission - Windsor Street Exchange Redevelopment "
        "Project - $69,275,000",
        "x": 159, "y": 182, "w": 480, "h": 147,
    },
    {"text": "Water", "x": 651, "y": 182, "w": 179, "h": 18},
    {"text": "04/07/2025", "x": 838, "y": 182, "w": 103, "h": 18},
    {"text": "10/23/2025", "x": 948, "y": 182, "w": 103, "h": 18},
    {"text": "Directions given", "x": 1055, "y": 182, "w": 179, "h": 18},
    {"text": "Closed", "x": 13, "y": 214, "w": 133, "h": 18},
    {"text": "Capital Expenditure Approvals", "x": 651, "y": 214, "w": 179, "h": 21.28125},
]


def test_header_geometry_matching():
    meta = parse_header_boxes(HEADER_BOXES, "M12205")
    assert meta.title.startswith("Halifax Regional Water Commission")
    assert meta.title.endswith("$69,275,000")
    assert meta.status == "Closed"
    assert meta.type == "Water"
    assert meta.category == "Capital Expenditure Approvals"
    assert meta.date_received == date(2025, 4, 7)
    assert meta.date_final_submissions == date(2025, 10, 23)
    assert meta.outcome == "Directions given"
    assert meta.raw_fields["Matter No"] == "M12205"


def test_header_accepts_date_final_submissions_label():
    boxes = [dict(b) for b in HEADER_BOXES]
    for b in boxes:
        if b["text"] == "Decision Date":
            b["text"] = "Date Final Submissions"
    meta = parse_header_boxes(boxes, "M12205")
    assert meta.date_final_submissions == date(2025, 10, 23)


def test_tab_counts():
    counts = parse_tab_counts(
        ["Exhibits - 13", "Key Documents - 6", "Other Documents - 44", "Transcripts - 0",
         "Recordings - 0", "Hearings", "Related Matters"]
    )
    assert counts.exhibits == 13
    assert counts.key_documents == 6
    assert counts.other_documents == 44
    assert counts.transcripts == 0
    assert counts.recordings == 0
    assert counts.total == 63
    assert counts.for_type(DocumentType.OTHER_DOCUMENTS) == 44


def test_tab_counts_missing_raises():
    with pytest.raises(PortalError):
        parse_tab_counts(["Hearings", "Related Matters"])


def test_parse_other_document_row():
    row = parse_row_text(
        1,
        "104160\n\nUtility (Board) - Update\n\n10/28/2026\n\nPublic\n\nPreview\nGO GET IT\n.pdf\n",
    )
    assert row.doc_no == "104160"
    assert row.title == "Utility (Board) - Update"
    assert row.date == date(2026, 10, 28)
    assert row.security == "Public"
    assert row.file_type == "pdf"
    assert not row.is_confidential


def test_parse_exhibit_row():
    row = parse_row_text(
        3, "HRWC (Board) RIR-1 to RIR-25 CONFIDENTIAL\n\n05/22/2025\n\nConfidential\n\nH-4(C)\n\n"
           "Preview\nGO GET IT\n.pdf\n"
    )
    assert row.doc_no == "H-4(C)"
    assert row.title == "HRWC (Board) RIR-1 to RIR-25 CONFIDENTIAL"
    assert row.is_confidential


def test_parse_key_document_row():
    row = parse_row_text(
        1, "Notice of Paper Hearing\n\n04/11/2025\n\nPublic\n\n97289\n\nPreview\nGO GET IT\n.pdf\n"
    )
    assert row.doc_no == "97289"
    assert row.title == "Notice of Paper Hearing"


def test_parse_empty_row():
    assert parse_row_text(1, "\n\n") is None


def test_parse_date_variants():
    assert parse_date("04/07/2025") == date(2025, 4, 7)
    assert parse_date("2025-04-07") == date(2025, 4, 7)
    assert parse_date("garbage") is None
