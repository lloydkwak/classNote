import json
import re

import pytest

from app.llm.base import LLMProvider, QuotaExhausted
from app.llm.mock import MockProvider
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
    checked_note_type,
    has_unsupported_number,
    locate_quote,
    with_type_prefix,
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


@pytest.mark.parametrize(
    ("note_type", "language", "text", "expected"),
    [
        ("exam_hint", "ko", "base case 빠뜨리면 감점", "★ 시험: base case 빠뜨리면 감점"),
        ("exam_hint", "en", "base case is tested", "★ exam: base case is tested"),
        ("example", "ko", "fib(50)", "예) fib(50)"),
        ("example", "en", "fib(50)", "e.g. fib(50)"),
        ("question", "ko", "왜 멈춤?", "Q. 왜 멈춤?"),
        ("question", "en", "why stop?", "Q. why stop?"),
        ("explanation", "ko", "base case 필요", "base case 필요"),
        ("emphasis", "en", "key idea", "key idea"),
    ],
)
def test_type_prefix_is_added(note_type, language, text, expected):
    assert with_type_prefix(text, note_type, language) == expected


@pytest.mark.parametrize(
    ("note_type", "text"),
    [("exam_hint", "★ 시험: 중요"), ("example", "예) fib(50)"), ("question", "Q. 왜?"), ("example", "e.g. fib")],
)
def test_type_prefix_is_not_added_twice(note_type, text):
    assert with_type_prefix(text, note_type, "ko") == text


EXAM_SPAN = Span(page=3, start=0, end=30, transcript="이 base case가 시험에 나옵니다. 그리고 멈춥니다.")


def test_notes_get_their_prefix_and_keep_their_quote():
    quote = "이 base case가 시험에 나옵니다"
    reply = json.dumps([{**GOOD, "type": "exam_hint", "note": "base case 출제", "quote": quote}])
    note = generate_span_notes(EXAM_SPAN, PAGE, ScriptedProvider(reply))[0]
    assert note.note == "★ 시험: base case 출제"
    assert note.type == "exam_hint"
    assert note.quote == quote


def test_a_marker_in_different_case_is_not_doubled():
    quote = "이 base case가 시험에 나옵니다"
    reply = json.dumps(
        [{**GOOD, "type": "exam_hint", "note": "★ Exam: base case 출제", "quote": quote, "language": "en"}]
    )
    note = generate_span_notes(EXAM_SPAN, PAGE, ScriptedProvider(reply))[0]
    assert note.note == "★ exam: base case 출제"


def test_an_exam_hint_without_exam_words_in_its_quote_loses_the_claim():
    reply = json.dumps(
        [{**GOOD, "type": "exam_hint", "note": "★ 시험: 멈춤", "quote": "그리고 멈춥니다"}]
    )
    note = generate_span_notes(EXAM_SPAN, PAGE, ScriptedProvider(reply))[0]
    assert note.type == "emphasis"
    assert note.note == "멈춤"


@pytest.mark.parametrize(
    ("quote", "supported"),
    [
        ("이건 시험에 나옵니다", True),
        ("기말고사 범위입니다", True),
        ("This will be on the exam", True),
        ("You will see this on a test", True),
        ("이건 정말 중요합니다", False),
        ("Remember this", False),
        ("the latest result", False),
    ],
)
def test_exam_words(quote, supported):
    assert (checked_note_type("exam_hint", quote) == "exam_hint") is supported


@pytest.mark.parametrize(
    ("note_text", "quote", "unsupported"),
    [
        ("속도 3~4배 빠름", "미니배치가 훨씬 빠릅니다", True),
        ("n_init=1 설정", "초기값을 직접 지정합니다", True),
        ("5개 선택", "랜덤하게 5개를 선택합니다", False),
        ("O(n) 필요", "메모리가 더 필요합니다", False),
        ("x 2.5 증가", "2.5배 증가합니다", False),
    ],
)
def test_numbers_in_a_note_must_be_in_its_quote(note_text, quote, unsupported):
    assert has_unsupported_number(note_text, quote) is unsupported


def test_a_note_with_an_invented_number_is_dropped():
    invented = {**GOOD, "note": "속도 3~4배 빠름"}
    assert generate_span_notes(SPAN, PAGE, ScriptedProvider(json.dumps([invented]))) == []


def test_quote_reaches_the_viewer():
    annotations = consolidate([note("A point", start=5)], EXTRACTION, "job")
    assert annotations.pages[0].notes[0].quote == "A point"


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
            request = json.loads(prompt.rpartition("INPUT_JSON:")[2])
            seen.append([span["transcript"] for span in request["spans"]])
            return "[]"

    monkeypatch.setattr(block_align, "get_provider", lambda: Recording())
    monkeypatch.setattr(block_align, "MAX_BATCH_SPANS", 1)
    extraction = PdfExtraction(pages=[PAGE.model_copy(update={"page": 1}), PAGE], key_terms=[])
    _, complete = block_align.generate_notes(
        alignment((0, 30, 1, "a"), (30, 60, 3, "b")), extraction
    )
    assert seen == [["a"], ["b"]]
    assert complete is True


class BatchLLM(MockProvider):
    def __init__(self):
        self.requests = []

    def complete_json(self, prompt):
        request = json.loads(prompt.rpartition("INPUT_JSON:")[2])
        self.requests.append([span["span"] for span in request["spans"]])
        return super().complete_json(prompt)


def batch_setup(monkeypatch, llm, count=6):
    monkeypatch.setattr(block_align, "get_provider", lambda: llm)
    pages = [PAGE.model_copy(update={"page": number}) for number in range(1, count + 1)]
    rows = [
        (index * 30, index * 30 + 30, index + 1, f"이 recursion이 base case에서 멈춥니다 {index}.")
        for index in range(count)
    ]
    segments = [Segment(start=s, end=e, text=t) for s, e, _, t in rows]
    return alignment(*rows), PdfExtraction(pages=pages, key_terms=[]), segments


def test_a_lecture_uses_one_request_for_many_spans(monkeypatch):
    llm = BatchLLM()
    aligned, extraction, segments = batch_setup(monkeypatch, llm)
    notes, complete = block_align.generate_notes(aligned, extraction, segments)
    assert llm.requests == [[0, 1, 2, 3, 4, 5]]
    assert complete is True
    assert [note.page for note in notes] == [1, 2, 3, 4, 5, 6]
    # Notes keep their own page and time, and aren't mixed up across spans.
    assert [note.start for note in notes] == [0, 30, 60, 90, 120, 150]
    assert all(note.quote.endswith(f"{note.page - 1}.") for note in notes)


def test_batches_respect_the_size_limits(monkeypatch):
    llm = BatchLLM()
    aligned, extraction, segments = batch_setup(monkeypatch, llm)
    monkeypatch.setattr(block_align, "MAX_BATCH_SPANS", 4)
    block_align.generate_notes(aligned, extraction, segments)
    assert llm.requests == [[0, 1, 2, 3], [0, 1]]

    llm.requests.clear()
    monkeypatch.setenv("DATA_DIR", str(block_align.get_settings().data_dir / "other"))
    monkeypatch.setattr(block_align, "MAX_BATCH_SPANS", 100)
    monkeypatch.setattr(block_align, "MAX_BATCH_CHARS", 1)
    block_align.generate_notes(aligned, extraction, segments)
    assert len(llm.requests) == 6  # A span larger than the limit still goes, on its own.


def test_a_rerun_only_asks_about_spans_without_notes(monkeypatch):
    llm = BatchLLM()
    aligned, extraction, segments = batch_setup(monkeypatch, llm)
    block_align.generate_notes(aligned, extraction, segments)
    assert len(llm.requests) == 1
    block_align.generate_notes(aligned, extraction, segments)
    assert len(llm.requests) == 1


def test_batched_and_single_requests_share_their_cache(monkeypatch):
    llm = BatchLLM()
    aligned, extraction, segments = batch_setup(monkeypatch, llm)
    block_align.generate_notes(aligned, extraction, segments)
    span = block_align.build_spans(aligned)[0]
    calls = len(llm.requests)
    notes = generate_span_notes(span, extraction.pages[0], llm, segments)
    assert notes
    assert len(llm.requests) == calls


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        json.dumps([{**GOOD}]),  # No span number.
        json.dumps([{**GOOD, "span": 99}]),  # A span that wasn't asked about.
    ],
)
def test_invalid_batch_replies_are_retried_then_left_for_a_rerun(monkeypatch, bad):
    llm = ScriptedProvider(bad, bad)
    aligned, extraction, segments = batch_setup(monkeypatch, llm, count=2)
    notes, complete = block_align.generate_notes(aligned, extraction, segments)
    assert notes == []
    assert complete is False
    assert llm.calls == 2


def test_an_invented_quote_in_a_batch_is_still_dropped(monkeypatch):
    invented = {**GOOD, "span": 0, "quote": "교수님이 말하지 않은 내용"}
    real = {**GOOD, "span": 1, "quote": "base case에서 멈춥니다"}
    llm = ScriptedProvider(json.dumps([invented, real]))
    aligned, extraction, segments = batch_setup(monkeypatch, llm, count=2)
    notes, _ = block_align.generate_notes(aligned, extraction, segments)
    assert [note.page for note in notes] == [2]


def test_overload_loses_those_spans_but_not_the_job(monkeypatch):
    class Overloaded(Exception):
        code = 503

    class FailsThenWorks(MockProvider):
        calls = 0

        def complete_json(self, prompt):
            FailsThenWorks.calls += 1
            if FailsThenWorks.calls == 1:
                raise Overloaded("503")
            return super().complete_json(prompt)

    aligned, extraction, segments = batch_setup(monkeypatch, FailsThenWorks())
    monkeypatch.setattr(block_align, "MAX_BATCH_SPANS", 3)
    notes, complete = block_align.generate_notes(aligned, extraction, segments)
    assert complete is False
    assert [note.page for note in notes] == [4, 5, 6]


def test_demo_mode_makes_no_requests_and_uses_what_is_cached(monkeypatch):
    llm = BatchLLM()
    aligned, extraction, segments = batch_setup(monkeypatch, llm)
    monkeypatch.setenv("DEMO_MODE", "1")
    notes, complete = block_align.generate_notes(aligned, extraction, segments)
    assert (notes, complete, llm.requests) == ([], False, [])


def test_quota_running_out_keeps_earlier_notes_and_says_so(monkeypatch):
    class RunsOut(ScriptedProvider):
        def complete_json(self, prompt):
            self.calls += 1
            if self.calls > 1:
                raise QuotaExhausted("limit")
            return json.dumps([{**GOOD, "span": 0}])

    provider = RunsOut()
    monkeypatch.setattr(block_align, "MAX_BATCH_SPANS", 1)
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


def test_prompt_uses_judgment_about_language_and_shows_the_lecture():
    segments = [Segment(start=i, end=i + 1, text=f"강의 내용 {i}") for i in range(200)]
    prompt = block_align.build_prompt(SPAN, PAGE, segments)
    assert "clearest to a student" in prompt
    assert "language the professor used" not in prompt
    sample = json.loads(prompt.rpartition("INPUT_JSON:")[2])["lecture_sample"]
    # It reaches across the lecture, not only its opening.
    assert "강의 내용 0" in sample and "강의 내용 33" in sample
    assert len(sample) <= block_align.LECTURE_SAMPLE_CHARS


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
    detailed = json.loads((pipeline.job_dir(job_id) / "detailed_notes.json").read_text(encoding="utf-8"))
    content_ids = {item["id"] for item in detailed["items"]}
    assert all(item.content_id in content_ids for item in notes)
    assert all(item.end > item.start for item in notes)

    for item in notes:
        hangul = bool(re.search(r"[가-힣]", item.note))
        # A note can be all English terms and still belong to a Korean lecture, but not
        # the other way around.
        assert hangul <= (item.language == "ko")
    # This lecture is mainly Korean, so a slide's notes don't flip between languages.
    for page in annotations.pages:
        assert len({item.language for item in page.notes}) == 1

    korean = " ".join(item.note for item in notes if item.language == "ko")
    # These stay in English inside Korean notes. Their translations must not appear.
    assert "base case" in korean or "memoization" in korean or "recursion" in korean
    for translated in ["재귀", "기저 사례", "메모이제이션"]:
        assert translated not in korean

    # Written like notes: short, and without polite spoken endings.
    for item in notes:
        assert len(item.note) <= (47 if item.language == "ko" else 67)
        if item.language == "ko":
            assert not re.search(r"(입니다|습니다|합니다|거죠|해요|예요|세요)$", item.note)
        if item.type == "exam_hint":
            assert item.note.startswith(("★ 시험:", "★ exam:"))
