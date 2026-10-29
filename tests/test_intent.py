import pytest

from uarb_agent.intent import parse_request
from uarb_agent.models import DocumentType, MatterRequest, normalise_matter


def test_assignment_example():
    out = parse_request("", "Hi Agent, Can you give me Other Documents files from M12205? Thanks!")
    assert out.ok
    assert out.request == MatterRequest("M12205", DocumentType.OTHER_DOCUMENTS)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("exhibits for M12383 please", DocumentType.EXHIBITS),
        ("Send me the Exhibit list from m12383", DocumentType.EXHIBITS),
        ("key docs m12205", DocumentType.KEY_DOCUMENTS),
        ("Key Documents - M12205", DocumentType.KEY_DOCUMENTS),
        ("other docs from M12205", DocumentType.OTHER_DOCUMENTS),
        ("M12205 transcripts", DocumentType.TRANSCRIPTS),
        ("any recordings in M12205?", DocumentType.RECORDINGS),
        ("audio for M12205", DocumentType.RECORDINGS),
    ],
)
def test_variations(text, expected):
    out = parse_request("", text)
    assert out.ok, out.problem
    assert out.request.document_type is expected


def test_subject_is_parsed_too():
    out = parse_request("M12205 other documents", "")
    assert out.ok
    assert out.request.matter == "M12205"


def test_missing_matter():
    out = parse_request("", "please send me the exhibits")
    assert not out.ok
    assert "matter" in out.problem


def test_missing_type():
    out = parse_request("", "please send me M12205")
    assert not out.ok
    assert "document type" in out.problem


def test_two_matters_is_rejected():
    out = parse_request("", "exhibits for M12205 and M12383")
    assert not out.ok
    assert "M12205" in out.problem and "M12383" in out.problem


def test_two_types_is_rejected():
    out = parse_request("", "exhibits and transcripts for M12205")
    assert not out.ok


def test_negated_second_type_is_accepted():
    out = parse_request("", "Other documents (not exhibits) for M12205")
    assert out.ok
    assert out.request.document_type is DocumentType.OTHER_DOCUMENTS


def test_quoted_reply_is_ignored():
    body = (
        "Key documents please\n\n"
        "On Tue, Oct 28, 2026 Agent wrote:\n"
        "> M12205 is about ... I found 13 Exhibits, 6 Key Documents, 44 Other Documents\n"
    )
    out = parse_request("Re: M12205", body)
    assert out.ok
    assert out.request == MatterRequest("M12205", DocumentType.KEY_DOCUMENTS)


def test_normalise_matter():
    assert normalise_matter("m12205") == "M12205"
    with pytest.raises(ValueError):
        normalise_matter("M1")
