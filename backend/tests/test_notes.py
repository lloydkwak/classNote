import json
import re

import pytest

from app.llm.base import LLMProvider, QuotaExhausted
from app.models import (
    AlignedWindow,
    Alignment,
    Annotations,
    Block,
    Note,
    Page,
    PdfExtraction,
    Segment,
)
from app.pipeline import block_align
from app.pipeline import run as pipeline
from app.pipeline.block_align import (
    build_spans,
    find_emphasis,
    generate_span_notes,
    locate_quote,
    quote_in_transcript,
)
from app.pipeline.consolidate import consolidate, merge_duplicates
from app.models import Span
from app.samples import SLIDES, make_test_pdf


def window(index, start, end, page, text="text"):
    return AlignedWindow(
        window_id=index,
        start=start,
        end=end,
        text=text,
        page=page,
        raw_page=page or 1,
        confidence=1,
        similarities=[],
    )


def alignment(*rows):
    return Alignment(windows=[window(i, *row) for i, row in enumerate(rows)], pages=[])


def test_spans_join_consecutive_windows_on_the_same_page():
    spans = build_spans(alignment((0, 30, 1, "a"), (30, 60, 1, "b"), (60, 90, 2, "c")))
    assert [(span.page, span.start, span.end, span.transcript) for span in spans] == [
        (1, 0, 60, "a b"),
        (2, 60, 90, "c"),
    ]


def test_off_topic_window_splits_a_span_and_is_excluded():
    spans = build_spans(alignment((0, 30, 1, "a"), (30, 60, None, "chat"), (60, 90, 1, "b")))
    assert [span.transcript for span in spans] == ["a", "b"]


def test_long_spans_are_split_to_keep_playback_near_the_note():
    rows = [(start, start + 100, 1, "x") for start in range(0, 700, 100)]
    spans = build_spans(alignment(*rows))
    assert len(spans) == 3
    assert all(span.end - span.start <= block_align.MAX_SPAN_SECONDS for span in spans)


def test_emphasis_in_both_languages_without_matching_inside_other_words():
    assert find_emphasis("이건 시험에 꼭 나옵니다") == ["시험", "꼭"]
    assert find_emphasis("Remember this for the exam") == ["Remember", "exam"]
    assert find_emphasis("Here is an example") == []


def test_quote_check_allows_whitespace_and_punctuation_differences():
    transcript = "이 recursion이 base case에서 멈추고, 다음으로 넘어갑니다."
    assert quote_in_transcript("recursion이 base  case에서 멈추고", transcript)
    assert quote_in_transcript("BASE CASE에서 멈추고 다음으로", transcript)
    assert not quote_in_transcript("deadlock이 발생합니다", transcript)
    assert not quote_in_transcript("...", transcript)


PAGE = Page(
    page=3,
    title="Title",
    blocks=[Block(block_id="p3_b0", page=3, text="Recursion", bbox=(0.1, 0.1, 0.5, 0.2))],
)
SPAN = Span(page=3, start=10, end=40, transcript="이 recursion이 base case에서 멈춥니다.")
GOOD = {
    "block_ids": ["p3_b0"],
    "type": "explanation",
    "note": "base case에서 멈춘다.",
    "quote": "base case에서 멈춥니다",
    "language": "ko",
    "importance": 3,
}


class ScriptedProvider(LLMProvider):
    name = "scripted"
    model = "test"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def complete_json(self, prompt):
        self.calls += 1
        return self.replies.pop(0)


def test_note_gets_the_span_times_and_is_cached():
    provider = ScriptedProvider(json.dumps([GOOD]))
    first = generate_span_notes(SPAN, PAGE, provider)
    second = generate_span_notes(SPAN, PAGE, provider)
    assert first == second
    assert (first[0].start, first[0].end, first[0].page) == (10, 40, 3)
    assert provider.calls == 1


def segs(*rows):
    return [Segment(start=start, end=end, text=text) for start, end, text in rows]


TIMED = segs(
    (10, 20, "이 recursion이 base case에서"),
    (20, 30, "멈춥니다. 그 다음에는"),
    (30, 40, "merge sort로 넘어갑니다."),
)


def test_quote_in_one_segment_gets_that_segments_times():
    assert locate_quote("merge sort로 넘어갑니다", TIMED) == (30, 40)


def test_quote_crossing_segments_runs_from_the_first_to_the_last():
    assert locate_quote("base case에서 멈춥니다", TIMED) == (10, 30)
    assert locate_quote("recursion이 base case에서 멈춥니다. 그 다음에는 merge", TIMED) == (10, 40)


def test_locating_ignores_spacing_punctuation_and_case():
    assert locate_quote("BASE CASE 에서, 멈춥니다!", TIMED) == (10, 30)


def test_quote_that_is_not_in_the_segments_is_not_located():
    assert locate_quote("deadlock", TIMED) is None
    assert locate_quote("...", TIMED) is None
    assert locate_quote("anything", []) is None


def test_quote_ending_exactly_at_a_segment_boundary_stops_there():
    assert locate_quote("이 recursion이 base case에서", TIMED) == (10, 20)
    assert locate_quote("멈춥니다", TIMED) == (20, 30)


def test_notes_on_one_page_get_different_times_from_their_quotes():
    span = Span(page=3, start=10, end=40, transcript=" ".join(s.text for s in TIMED))
    first = {**GOOD, "quote": "base case에서"}
    second = {**GOOD, "note": "다른 설명", "quote": "merge sort로 넘어갑니다"}
    notes = generate_span_notes(span, PAGE, ScriptedProvider(json.dumps([first, second])), TIMED)
    assert [(note.start, note.end) for note in notes] == [(10, 20), (30, 40)]


def test_note_falls_back_to_the_span_when_its_quote_cannot_be_located():
    span = Span(page=3, start=10, end=40, transcript="base case에서 멈춥니다")
    other = segs((10, 40, "전혀 다른 문장입니다"))
    notes = generate_span_notes(span, PAGE, ScriptedProvider(json.dumps([GOOD])), other)
    assert (notes[0].start, notes[0].end) == (10, 40)


def test_segments_outside_the_span_are_not_used():
    span = Span(page=3, start=100, end=130, transcript="base case에서 멈춥니다")
    earlier_and_inside = segs((10, 20, "base case에서 멈춥니다"), (100, 130, "base case에서 멈춥니다"))
    notes = generate_span_notes(
        span, PAGE, ScriptedProvider(json.dumps([GOOD])), earlier_and_inside
    )
    assert (notes[0].start, notes[0].end) == (100, 130)


def test_invented_quote_is_dropped():
    invented = {**GOOD, "quote": "교수님이 말하지 않은 내용"}
    notes = generate_span_notes(SPAN, PAGE, ScriptedProvider(json.dumps([invented, GOOD])))
    assert [note.quote for note in notes] == [GOOD["quote"]]


def test_unknown_block_id_becomes_a_page_level_note():
    reply = json.dumps([{**GOOD, "block_ids": ["p9_b9"]}])
    assert generate_span_notes(SPAN, PAGE, ScriptedProvider(reply))[0].block_ids == []


@pytest.mark.parametrize("bad", ["not json", json.dumps([{"note": "missing fields"}])])
def test_invalid_reply_is_retried_once(bad):
    provider = ScriptedProvider(bad, json.dumps([GOOD]))
    assert len(generate_span_notes(SPAN, PAGE, provider)) == 1
    assert provider.calls == 2


def test_two_invalid_replies_lose_the_span_but_are_not_cached():
    provider = ScriptedProvider("bad", "bad", json.dumps([GOOD]))
    assert generate_span_notes(SPAN, PAGE, provider) == []
    assert provider.calls == 2
    assert len(generate_span_notes(SPAN, PAGE, provider)) == 1


def test_wrapped_array_is_accepted():
    reply = json.dumps({"items": [GOOD]})
    assert len(generate_span_notes(SPAN, PAGE, ScriptedProvider(reply))) == 1


def test_requests_run_one_at_a_time_in_order(monkeypatch):
    seen = []

    class Recording(ScriptedProvider):
        def complete_json(self, prompt):
            seen.append(json.loads(prompt.rpartition("INPUT_JSON:")[2])["transcript"])
            return "[]"

    monkeypatch.setattr(block_align, "get_provider", lambda: Recording())
    extraction = PdfExtraction(pages=[PAGE.model_copy(update={"page": 1}), PAGE], key_terms=[])
    _, complete = block_align.generate_notes(
        alignment((0, 30, 1, "a"), (30, 60, 3, "b")), extraction
    )
    assert seen == ["a", "b"]
    assert complete is True


def test_quota_running_out_keeps_earlier_notes_and_says_so(monkeypatch):
    class RunsOut(ScriptedProvider):
        def complete_json(self, prompt):
            self.calls += 1
            if self.calls > 1:
                raise QuotaExhausted("limit")
            return json.dumps([GOOD])

    provider = RunsOut()
    monkeypatch.setattr(block_align, "get_provider", lambda: provider)
    first = SPAN.transcript
    extraction = PdfExtraction(pages=[PAGE], key_terms=[])
    notes, complete = block_align.generate_notes(
        alignment((10, 40, 3, first), (40, 400, None), (400, 430, 3, "다른 내용")), extraction
    )
    assert len(notes) == 1
    assert complete is False


def note(text="note", block="p3_b0", importance=3, start=0.0, kind="explanation"):
    return Note(
        page=3,
        block_ids=[block] if block else [],
        type=kind,
        note=text,
        quote=text,
        language="en",
        importance=importance,
        start=start,
        end=start + 10,
    )


EXTRACTION = PdfExtraction(pages=[PAGE], key_terms=[])


def test_merge_keeps_the_more_important_duplicate():
    merged = merge_duplicates(
        [note("The base case stops it", importance=2), note("The base case stops it!", importance=5)]
    )
    assert [item.importance for item in merged] == [5]


def test_different_notes_or_blocks_are_not_merged():
    assert len(merge_duplicates([note("Merge sort is stable"), note("Heaps use arrays")])) == 2
    assert len(merge_duplicates([note("Same", block="p3_b0"), note("Same", block=None)])) == 2


def test_consolidate_caps_by_importance_then_orders_by_time():
    notes = [
        note(f"{chr(97 + index)} " * 8, importance=index, start=100 - index) for index in range(7)
    ]
    result = consolidate(notes, EXTRACTION, "job", max_notes_per_page=3)
    kept = result.pages[0].notes
    assert [item.start for item in kept] == [94, 95, 96]
    assert [item.id for item in kept] == ["p3_n0", "p3_n1", "p3_n2"]


def test_consolidate_attaches_bboxes_and_handles_page_level_notes():
    result = consolidate(
        [note("On a block"), note("About the whole page", block=None)], EXTRACTION, "job"
    )
    by_text = {item.note: item for item in result.pages[0].notes}
    assert by_text["On a block"].bboxes == [(0.1, 0.1, 0.5, 0.2)]
    assert by_text["About the whole page"].bboxes == []
    assert result.job_id == "job"


def test_full_pipeline_with_mocks_keeps_language_and_technical_terms(tmp_path):
    """No API keys or downloads: mock STT, LLM, and embeddings on a mixed transcript."""
    pdf = tmp_path / "slides.pdf"
    make_test_pdf(pdf)
    job_id = pipeline.create_job(pdf, None)
    statuses = []
    pipeline.run_pipeline(job_id, on_status=statuses.append)
    assert "writing_notes" in statuses

    text = (pipeline.job_dir(job_id) / "annotations.json").read_text(encoding="utf-8")
    annotations = Annotations.model_validate_json(text)
    notes = [item for page in annotations.pages for item in page.notes]
    assert notes
    assert len(annotations.pages) <= len(SLIDES)
    assert all(len(page.notes) <= 5 for page in annotations.pages)
    assert all(item.end > item.start for item in notes)

    for item in notes:
        hangul = bool(re.search(r"[가-힣]", item.note))
        assert item.language == ("ko" if hangul else "en")
    assert {item.language for item in notes} == {"ko", "en"}

    korean = " ".join(item.note for item in notes if item.language == "ko")
    # These stay in English inside Korean notes. Their translations must not appear.
    assert "base case" in korean or "memoization" in korean or "recursion" in korean
    for translated in ["재귀", "기저 사례", "메모이제이션"]:
        assert translated not in korean
