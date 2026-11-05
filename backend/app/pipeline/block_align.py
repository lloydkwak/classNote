"""Group aligned windows into spans, and ask the LLM for notes on each span."""

import json
import logging
import re
import unicodedata

from pydantic import BaseModel, ValidationError

from app.cache import cached_json
from app.llm import get_provider
from app.llm.base import LLMProvider
from app.llm.mock import INPUT_MARKER
from app.models import Alignment, Note, NoteType, Page, PdfExtraction, Span

log = logging.getLogger(__name__)

# Long spans are split, so a note's playback range stays near where it was said.
MAX_SPAN_SECONDS = 300.0

EMPHASIS = re.compile(
    r"시험|중요|꼭|외워|\bexams?\b|\bimportant\b|\bremember\b|\bmake sure\b",
    re.IGNORECASE,
)

PROMPT = """You annotate university lecture slides using what the professor said.

Below are one slide's text blocks and the transcript of the professor explaining that slide.
The transcript comes from speech recognition, so it may contain misheard words.

Return a JSON array of notes. Each note has these fields:
- "block_ids": the ids of the blocks it relates to, or [] if it relates to the whole page
- "type": one of "explanation", "example", "emphasis", "exam_hint", "question"
- "note": 1-2 sentences
- "quote": the exact part of the transcript this note is based on, copied word for word
- "language": "ko" or "en", the language of the note
- "importance": a whole number from 1 (minor) to 5 (critical)

Rules:
- Only note information that is NOT already on the slide. Do not repeat the slide.
- Write each note in the language the professor used in that part of the lecture. For mixed
  speech, follow the dominant language.
- Never translate technical terms. Keep domain terms, names, and jargon exactly as the professor
  said them or as they appear on the slide, including inside a note in another language. If the
  transcript spells a term phonetically or mishears it, use the slide's spelling. For example,
  write "recursion" and "base case" inside a Korean note, not a Korean translation of them.
- Do not invent anything. Every note must be supported by its quote.
- "exam_hint_candidates" lists emphasis words found in the transcript. Use type "exam_hint" when
  the professor really is saying that something will be tested, and otherwise ignore them.
- Skip garbled or meaningless parts of the transcript. Return [] if nothing is worth noting.

"""


class LLMNote(BaseModel):
    block_ids: list[str] = []
    type: NoteType
    note: str
    quote: str
    language: str
    importance: int = 1


def build_spans(alignment: Alignment) -> list[Span]:
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
            and window.end - last.start <= MAX_SPAN_SECONDS
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


def build_prompt(span: Span, page: Page) -> str:
    request = {
        "blocks": [{"block_id": block.block_id, "text": block.text} for block in page.blocks],
        "transcript": span.transcript,
        "exam_hint_candidates": find_emphasis(span.transcript),
    }
    return f"{PROMPT}{INPUT_MARKER}\n{json.dumps(request, ensure_ascii=False)}"


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


def generate_span_notes(span: Span, page: Page, provider: LLMProvider) -> list[Note]:
    prompt = build_prompt(span, page)
    try:
        rows = cached_json(
            "llm", [provider.name, provider.model, prompt], lambda: _ask_llm(provider, prompt)
        )
    except ValueError as error:
        # Lose this span's notes rather than the whole job. Nothing was cached, so a
        # rerun tries it again.
        log.warning("Skipping notes for page %d at %.0fs: %s", span.page, span.start, error)
        return []

    block_ids = {block.block_id for block in page.blocks}
    notes = []
    for row in rows:
        note = LLMNote(**row)
        if not quote_in_transcript(note.quote, span.transcript):
            log.info("Dropping a note whose quote isn't in the transcript: %r", note.quote[:60])
            continue
        notes.append(
            Note(
                page=span.page,
                block_ids=[block_id for block_id in note.block_ids if block_id in block_ids],
                type=note.type,
                note=note.note,
                quote=note.quote,
                language=note.language,
                importance=note.importance,
                start=span.start,
                end=span.end,
            )
        )
    return notes


def generate_notes(alignment: Alignment, extraction: PdfExtraction) -> list[Note]:
    """Make one LLM request at a time, which keeps free-tier use within its limits."""
    pages = {page.page: page for page in extraction.pages}
    provider = get_provider()
    notes: list[Note] = []
    for span in build_spans(alignment):
        notes.extend(generate_span_notes(span, pages[span.page], provider))
    return notes
