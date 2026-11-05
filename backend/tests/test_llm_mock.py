import json

from app.llm import get_provider
from app.llm.mock import INPUT_MARKER


def test_llm_provider_mock_can_be_selected():
    assert get_provider().name == "mock"


def test_connection_check_prompt_gets_ok():
    assert json.loads(get_provider().complete_json('Return {"ok": true}')) == {"ok": True}


def test_notes_quote_the_transcript_and_keep_its_language():
    request = {
        "blocks": [{"block_id": "p1_b1", "text": "Base case stops recursion"}],
        "transcript": "이 recursion이 base case에서 멈춥니다. Remember this for the exam.",
        "exam_hint_candidates": ["exam"],
    }
    prompt = f"Instructions\n{INPUT_MARKER}\n{json.dumps(request, ensure_ascii=False)}"
    first, second = json.loads(get_provider().complete_json(prompt))
    assert first["quote"] in request["transcript"]
    assert first["language"] == "ko"
    assert "base case" in first["note"]
    assert first["block_ids"] == ["p1_b1"]
    assert second["language"] == "en"
    assert second["type"] == "exam_hint"
