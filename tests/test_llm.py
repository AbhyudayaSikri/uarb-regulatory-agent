from uarb_agent.config import Settings
from uarb_agent.llm import build_client, extract_request, facts_preserved, polish_reply
from uarb_agent.models import DocumentType


class StubClient:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls = []

    def complete(self, system, user, *, json_mode=False):
        self.calls.append((system, user, json_mode))
        return self.reply


def test_no_provider_means_no_client():
    assert build_client(Settings(llm_provider="none")) is None
    assert build_client(Settings(llm_provider="openai", openai_api_key="")) is None


def test_extract_request_parses_json():
    client = StubClient('{"matter": "m12205", "document_type": "Key Documents"}')
    req = extract_request(client, "hello", "some free text")
    assert req is not None
    assert req.matter == "M12205"
    assert req.document_type is DocumentType.KEY_DOCUMENTS
    assert client.calls[0][2] is True


def test_extract_request_handles_fenced_and_null():
    assert extract_request(StubClient('```json\n{"matter": null, "document_type": null}\n```'),
                           "", "") is None
    assert extract_request(StubClient("not json"), "", "") is None


def test_polish_keeps_draft_when_facts_change():
    draft = "M12205 has 44 Other Documents. I downloaded 10 and attached M12205-other.zip."
    bad = "M12205 has 43 Other Documents. I downloaded 10 and attached M12205-other.zip."
    assert polish_reply(StubClient(bad), draft) == draft
    good = "Hello! M12205 has 44 Other Documents; I downloaded 10 of them. See M12205-other.zip."
    assert polish_reply(StubClient(good), draft) == good


def test_facts_preserved():
    assert facts_preserved("April 7, 2025 and $69,275,000", "on April 7, 2025 for $69,275,000")
    assert not facts_preserved("13 Exhibits", "12 Exhibits")
