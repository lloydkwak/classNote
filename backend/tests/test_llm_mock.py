import json

from app.llm import get_provider
from app.llm.mock import INPUT_MARKER


def test_llm_provider_mock_can_be_selected():
    assert get_provider().name == "mock"


def test_connection_check_prompt_gets_ok():
    assert json.loads(get_provider().complete_json('Return {"ok": true}')) == {"ok": True}


def ask(transcript, sample="", candidates=()):
    request = {
        "lecture_sample": sample,
        "blocks": [{"block_id": "p1_b1", "text": "Base case stops recursion"}],
        "transcript": transcript,
        "exam_hint_candidates": list(candidates),
    }
    prompt = f"Instructions\n{INPUT_MARKER}\n{json.dumps(request, ensure_ascii=False)}"
    return json.loads(get_provider().complete_json(prompt))


KOREAN_LECTURE = "오늘은 recursion을 배웁니다. base case가 중요합니다. 다음은 merge sort입니다."


def test_notes_quote_the_transcript_and_keep_technical_terms():
    note = ask("이 recursion이 base case에서 멈춥니다.", KOREAN_LECTURE)[0]
    assert note["quote"] in "이 recursion이 base case에서 멈춥니다."
    assert "base case" in note["note"]
    assert note["block_ids"] == ["p1_b1"]


def test_one_english_sentence_does_not_flip_a_korean_lecture():
    notes = ask("이 recursion이 멈춥니다. Remember this one. 시험에 꼭 나옵니다.", KOREAN_LECTURE, ["시험"])
    assert {note["language"] for note in notes} == {"ko"}
    assert notes[-1]["type"] == "exam_hint"


def test_a_whole_section_in_another_language_may_switch():
    notes = ask("We now look at merge sort. It splits the array in half.", KOREAN_LECTURE)
    assert {note["language"] for note in notes} == {"en"}


def test_an_english_lecture_stays_in_english():
    sample = "Today we cover recursion. The base case matters. Next is merge sort."
    notes = ask("The recursion stops at the base case.", sample)
    assert {note["language"] for note in notes} == {"en"}
