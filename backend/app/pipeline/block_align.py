"""Group aligned windows into spans, and ask the LLM for notes on each span."""

import json
import logging
import re
import unicodedata
from collections.abc import Sequence

from pydantic import BaseModel, ValidationError

from app.cache import cached_json, read_cache, write_cache
from app.config import get_settings
from app.llm import get_provider
from app.llm.base import LLMProvider, QuotaExhausted
from app.llm.mock import INPUT_MARKER
from app.models import Alignment, Note, NoteType, Page, PdfExtraction, Segment, Span

log = logging.getLogger(__name__)

# Long spans are split, so a note's playback range stays near where it was said.
MAX_SPAN_SECONDS = 300.0
LECTURE_SAMPLE_CHARS = 300
# What one batched request carries. A long reply risks being cut off, which loses every span in it.
MAX_BATCH_CHARS = 20_000
MAX_BATCH_SPANS = 12
MAX_CONSECUTIVE_BATCH_FAILURES = 2

# Words that say something will be tested, needed in the quote of an exam hint. Narrower than
# EMPHASIS: "important" alone doesn't promise a test question.
EXAM_WORDS = re.compile(r"시험|기말|중간고사|출제|\bexams?\b|\btests?\b|\bquiz", re.IGNORECASE)
NUMBER = re.compile(r"\d+(?:\.\d+)?")

TYPE_PREFIXES = {
    "exam_hint": {"ko": "★ 시험:", "en": "★ exam:"},
    "example": {"ko": "예)", "en": "e.g."},
    "question": {"ko": "Q.", "en": "Q."},
}

EMPHASIS = re.compile(
    r"시험|중요|꼭|외워|\bexams?\b|\bimportant\b|\bremember\b|\bmake sure\b",
    re.IGNORECASE,
)

NOTE_FIELDS_AND_RULES = """Each note has these fields:
- "block_ids": the ids of the blocks it relates to, or [] if it relates to the whole page
- "type": one of "explanation", "example", "emphasis", "exam_hint", "question"
- "note": a condensed key point, written as described below
- "quote": the exact part of the transcript this note is based on, copied word for word as one
  continuous excerpt, including any recognition errors. Never shorten it with "...".
- "language": "ko" or "en", the language the note is actually written in
- "importance": a whole number from 1 (minor) to 5 (critical)

Rules:
- Only note information that is NOT already on the slide. Do not repeat the slide.
- Write each note in the language that will be clearest to a student of this course. Usually
  that is the main language of the lecture, which "lecture_sample" shows. Keep the notes on a
  slide, and across the lecture, in one language unless there is a good reason to switch, such as
  a whole section taught in another language, or a note that is almost entirely English terms.
  One sentence in another language is not a reason to switch.
- Keep technical terms as they are commonly written in the field, as said or as on the slide,
  rather than translating them. If the transcript spells a term phonetically or mishears it, use
  the slide's spelling. For example, "recursion" and "base case" stay in English inside a Korean
  note.
- Write like a good student's notes: condensed key points, not spoken sentences.
  - Korean: end with a noun or a short phrase ("~함", "~임", "~ 필요"). Never use polite spoken
    endings such as "~입니다", "~거죠", "~해요".
  - English: terse phrases, not full spoken sentences.
  - Use symbols where natural: → ← ↔ < > ≪ ≈ ∴ ×
  - Start exam_hint notes with "★ 시험:" ("★ exam:" in English), example notes with "예)" ("e.g."
    in English), and question notes with "Q.". Explanation and emphasis notes have no prefix.
  - Keep each note to about 40 characters in Korean or 60 in English. If a span has several
    separate points, return them as separate notes rather than one long note.
  For example, "여기서 제일 중요한 건 base case입니다. 없으면 안 멈춰요." becomes
  "base case 없음 → recursion 안 멈춤".
- Only condense what the quote actually says. Do not add conclusions, explanations, or facts that
  are not in the quote, even if they are on the slide. Do not write a number the quote doesn't say.
- "exam_hint_candidates" lists emphasis words found in the transcript. Use type "exam_hint" only
  when the quote itself says that something will be on a test, and otherwise ignore them.
- Blocks with "kind": "figure" are charts or images, and their "text" describes what they show.
  When the professor refers to a figure ("look at this graph", "이 그래프 보면") or explains what it
  shows, attach the note to that figure's block.
- The transcript has speech recognition errors, and technical terms often come out as Hangul
  spellings or wrong words. Work out what the professor meant from the context and the slide, and
  write notes on everything you can follow. Skip only parts that make no sense even then, and
  return [] only if nothing in the transcript can be understood.
"""

PROMPT = f"""You annotate university lecture slides using what the professor said.

Below are one slide's text blocks and the transcript of the professor explaining that slide.

Return a JSON array of notes. {NOTE_FIELDS_AND_RULES}
"""

BATCH_PROMPT = f"""You annotate university lecture slides using what the professor said.

Below are several spans of one lecture. Each has a "span" number, one slide's text blocks, and the
transcript of the professor explaining that slide. Handle each span on its own, using only its own
blocks and transcript.

Return one JSON array of notes covering all the spans. In addition to the fields below, each note
has "span": the number of the span it came from. {NOTE_FIELDS_AND_RULES}
"""


class LLMNote(BaseModel):
    span: int | None = None  # In a batched request, which span the note came from.
    block_ids: list[str] = []
    type: NoteType
    note: str
    quote: str
    language: str
    importance: int = 1


def checked_note_type(note_type: str, quote: str) -> str:
    """A false "this is on the exam" misleads students, so it needs support in the quote."""
    if note_type == "exam_hint" and not EXAM_WORDS.search(quote):
        return "emphasis"
    return note_type


def has_unsupported_number(note: str, quote: str) -> bool:
    """Models add figures from the slide or from nowhere, which is easy to catch and costly."""
    said = set(NUMBER.findall(quote))
    return any(number not in said for number in NUMBER.findall(note))


def with_type_prefix(text: str, note_type: str, language: str) -> str:
    """Make sure a note starts with its type's marker, whatever the LLM wrote."""
    prefixes = TYPE_PREFIXES.get(note_type)
    if not prefixes:
        return text
    text = text.strip()
    if text.startswith(tuple(prefixes.values())):
        return text
    return f"{prefixes.get(language, prefixes['en'])} {text}"


def strip_type_prefix(text: str) -> str:
    """Remove a marker the LLM wrote, since the note's type may have just changed."""
    text = text.strip()
    for prefixes in TYPE_PREFIXES.values():
        for prefix in prefixes.values():
            if text.casefold().startswith(prefix.casefold()):
                return text[len(prefix) :].strip()
    return text


def build_spans(alignment: Alignment, max_seconds: float = MAX_SPAN_SECONDS) -> list[Span]:
    """Join consecutive windows on the same page. Off-topic windows make no span."""
    spans: list[Span] = []
    previous_page = None
    for window in alignment.windows:
        if window.page is None:
            previous_page = None
            continue
        last = spans[-1] if spans else None
        if (
            last is not None
            and window.page == previous_page
            and window.end - last.start <= max_seconds
        ):
            last.end = window.end
            last.transcript = f"{last.transcript} {window.text}"
        else:
            spans.append(
                Span(page=window.page, start=window.start, end=window.end, transcript=window.text)
            )
        previous_page = window.page
    return spans


def find_emphasis(transcript: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in EMPHASIS.finditer(transcript)))


def _normalize_for_match(text: str) -> str:
    """Keep only letters and digits, so spacing and punctuation can differ."""
    return "".join(
        char
        for char in unicodedata.normalize("NFC", text).casefold()
        if unicodedata.category(char)[0] in "LN"
    )


def quote_in_transcript(quote: str, transcript: str) -> bool:
    normalized = _normalize_for_match(quote)
    return bool(normalized) and normalized in _normalize_for_match(transcript)


def locate_quote(quote: str, segments: Sequence[Segment]) -> tuple[float, float] | None:
    """Return when a quote was said: from its first segment's start to its last's end.

    Segments are matched as one continuous text, because a quote can cross segments.
    Returns None if the quote isn't in them.
    """
    target = _normalize_for_match(quote)
    if not target:
        return None
    texts = [_normalize_for_match(segment.text) for segment in segments]
    position = "".join(texts).find(target)
    if position < 0:
        return None
    first = last = None
    offset = 0
    for segment, text in zip(segments, texts):
        following = offset + len(text)
        if text and position < following and position + len(target) > offset:
            first = first or segment
            last = segment
        offset = following
    return first.start, last.end


def sample_lecture(segments: Sequence[Segment]) -> str:
    """Take text from across the lecture, so one request can tell its main language."""
    if not segments:
        return ""
    # Enough evenly spaced segments to reach the limit, at about 50 characters each.
    step = max(1, len(segments) // max(1, LECTURE_SAMPLE_CHARS // 50))
    return " ".join(segment.text for segment in segments[::step])[:LECTURE_SAMPLE_CHARS]


def build_prompt(span: Span, page: Page, segments: Sequence[Segment] = ()) -> str:
    request = {
        "lecture_sample": sample_lecture(segments) or span.transcript[:LECTURE_SAMPLE_CHARS],
        **_span_request(span, page),
    }
    return f"{PROMPT}{INPUT_MARKER}\n{json.dumps(request, ensure_ascii=False)}"


def _span_request(span: Span, page: Page) -> dict:
    return {
        "blocks": [
            {"block_id": block.block_id, "kind": block.kind, "text": block.text}
            for block in page.blocks
        ],
        "transcript": span.transcript,
        "exam_hint_candidates": find_emphasis(span.transcript),
    }


def build_batch_prompt(spans: Sequence[Span], pages: dict[int, Page], sample: str) -> str:
    request = {
        "lecture_sample": sample,
        "spans": [
            {"span": index, **_span_request(span, pages[span.page])}
            for index, span in enumerate(spans)
        ],
    }
    return f"{BATCH_PROMPT}{INPUT_MARKER}\n{json.dumps(request, ensure_ascii=False)}"


def _parse_notes(reply: str) -> list[LLMNote]:
    """Accept a bare array, or an object wrapping one, which OpenAI's JSON mode requires."""
    data = json.loads(reply)
    if isinstance(data, dict):
        data = data.get("items", data.get("notes", []))
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of notes.")
    return [LLMNote.model_validate(item) for item in data]


def _ask_llm(provider: LLMProvider, prompt: str) -> list[dict]:
    """Call the LLM, validating the reply and retrying once if it's invalid."""
    for attempt in (1, 2):
        reply = provider.complete_json(prompt)
        try:
            return [note.model_dump() for note in _parse_notes(reply)]
        except (ValidationError, ValueError) as error:  # JSONDecodeError is a ValueError.
            log.warning("Invalid LLM reply on attempt %d: %s", attempt, error)
    raise ValueError("The LLM returned invalid JSON twice.")


def _cache_key(provider: LLMProvider, prompt: str) -> list[str]:
    return [provider.name, provider.model, prompt]


def generate_span_notes(
    span: Span, page: Page, provider: LLMProvider, segments: Sequence[Segment] = ()
) -> list[Note]:
    """`segments` is the whole transcript, used to time each note by when it was said."""
    prompt = build_prompt(span, page, segments)
    try:
        rows = cached_json("llm", _cache_key(provider, prompt), lambda: _ask_llm(provider, prompt))
    except ValueError as error:
        # Lose this span's notes rather than the whole job. Nothing was cached, so a
        # rerun tries it again.
        log.warning("Skipping notes for page %d at %.0fs: %s", span.page, span.start, error)
        return []
    return _notes_from_rows(rows, span, page, segments)


def _notes_from_rows(
    rows: list[dict], span: Span, page: Page, segments: Sequence[Segment]
) -> list[Note]:
    block_ids = {block.block_id for block in page.blocks}
    # Windows never cut a segment, so every segment is wholly inside or outside a span.
    span_segments = [s for s in segments if span.start <= s.start and s.end <= span.end]
    notes = []
    for row in rows:
        note = LLMNote(**row)
        if not quote_in_transcript(note.quote, span.transcript):
            log.info("Dropping a note whose quote isn't in the transcript: %r", note.quote[:60])
            continue
        if has_unsupported_number(note.note, note.quote):
            log.info("Dropping a note with a number its quote doesn't say: %r", note.note[:60])
            continue
        note_type = checked_note_type(note.type, note.quote)
        start, end = locate_quote(note.quote, span_segments) or (span.start, span.end)
        notes.append(
            Note(
                page=span.page,
                block_ids=[block_id for block_id in note.block_ids if block_id in block_ids],
                type=note_type,
                note=with_type_prefix(strip_type_prefix(note.note), note_type, note.language),
                quote=note.quote,
                language=note.language,
                importance=note.importance,
                start=start,
                end=end,
            )
        )
    return notes


def _plan_batches(spans: Sequence[Span], pages: dict[int, Page]) -> list[list[Span]]:
    """Group spans in order, up to a size that a request can answer completely."""
    batches: list[list[Span]] = []
    size = 0
    for span in spans:
        cost = len(json.dumps(_span_request(span, pages[span.page]), ensure_ascii=False))
        if batches and size + cost <= MAX_BATCH_CHARS and len(batches[-1]) < MAX_BATCH_SPANS:
            batches[-1].append(span)
            size += cost
        else:
            batches.append([span])
            size = cost
    return batches


def _ask_llm_for_batch(
    provider: LLMProvider, spans: Sequence[Span], pages: dict[int, Page], sample: str
) -> dict[int, list[dict]]:
    """Return the notes for each span's index, retrying once if the reply is invalid."""
    prompt = build_batch_prompt(spans, pages, sample)
    for attempt in (1, 2):
        reply = provider.complete_json(prompt)
        try:
            notes = _parse_notes(reply)
            if any(note.span is None or not 0 <= note.span < len(spans) for note in notes):
                raise ValueError("A note has no valid span number.")
        except (ValidationError, ValueError) as error:
            log.warning("Invalid LLM batch reply on attempt %d: %s", attempt, error)
            continue
        rows: dict[int, list[dict]] = {index: [] for index in range(len(spans))}
        for note in notes:
            rows[note.span].append(note.model_dump(exclude={"span"}))
        return rows
    raise ValueError("The LLM returned invalid JSON twice.")


def generate_notes(
    alignment: Alignment, extraction: PdfExtraction, segments: Sequence[Segment] = ()
) -> tuple[list[Note], bool]:
    """Return the notes, and whether every span was covered.

    Several spans go in each request, because free-tier limits count requests, and requests
    go one at a time. Each span is cached on its own, so running again after the limit runs
    out continues from where this stopped, and the notes so far are still useful.
    """
    pages = {page.page: page for page in extraction.pages}
    provider = get_provider()
    spans = build_spans(alignment)
    rows_by_span: dict[tuple[int, float], list[dict]] = {}
    pending = []
    for span in spans:
        cached = read_cache(
            "llm", _cache_key(provider, build_prompt(span, pages[span.page], segments))
        )
        if cached is None:
            pending.append(span)
        else:
            rows_by_span[(span.page, span.start)] = cached

    complete = True
    if pending and get_settings().demo_mode:
        log.warning("DEMO_MODE=1: %d spans have no cached notes.", len(pending))
        pending, complete = [], False
    sample = sample_lecture(segments)
    failures = 0
    for batch in _plan_batches(pending, pages):
        try:
            answers = _ask_llm_for_batch(provider, batch, pages, sample)
        except QuotaExhausted as error:
            log.warning("Stopping notes at %.0fs: %s", batch[0].start, error)
            complete = False
            break
        except ValueError as error:  # Invalid replies. A rerun tries these spans again.
            log.warning("Skipping %d spans at %.0fs: %s", len(batch), batch[0].start, error)
            complete = False
            continue
        except Exception as error:  # Overload that outlasted the retries, or a network failure.
            log.warning("Notes failed for %d spans at %.0fs: %s", len(batch), batch[0].start, error)
            complete = False
            failures += 1
            if failures >= MAX_CONSECUTIVE_BATCH_FAILURES:
                break
            continue
        failures = 0
        for index, span in enumerate(batch):
            prompt = build_prompt(span, pages[span.page], segments)
            write_cache("llm", _cache_key(provider, prompt), answers[index])
            rows_by_span[(span.page, span.start)] = answers[index]

    notes: list[Note] = []
    for span in spans:
        rows = rows_by_span.get((span.page, span.start))
        if rows is not None:
            notes.extend(_notes_from_rows(rows, span, pages[span.page], segments))
    return notes, complete
