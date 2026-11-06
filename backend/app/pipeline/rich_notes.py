"""Extract lecture meaning before choosing what fits on the slide."""

import hashlib
import json
import time
from typing import Literal

from pydantic import BaseModel, Field

from app.cache import cached_json
from app.llm import get_provider
from app.llm.mock import INPUT_MARKER
from app.models import Alignment, Note, PdfExtraction, Segment
from app.pipeline.block_align import (
    build_spans, checked_note_type, has_unsupported_number, locate_quote,
    quote_in_transcript, sample_lecture,
)

Density = Literal["minimal", "balanced", "detailed"]
ContentType = Literal[
    "definition", "explanation", "reasoning", "derivation", "worked_example",
    "comparison", "exception", "warning", "correction", "emphasis", "exam_hint",
    "question_answer", "slide_reference",
]


class ExtractedItem(BaseModel):
    type: ContentType
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_quote: str = Field(min_length=1)
    short_note: str = Field(min_length=1)
    block_ids: list[str] = []
    importance: int = Field(default=3, ge=1, le=5)
    language: str = "en"


class ContentItem(ExtractedItem):
    id: str
    page: int
    start: float
    end: float
    segment_ids: list[int]
    confidence: float
    source: Literal["speech"] = "speech"


class ValidationRecord(BaseModel):
    item_id: str
    status: Literal["accepted", "rejected", "unavailable"]
    reason: str
    recovery_attempted: bool = False
    candidate: ExtractedItem | None = None


class DetailedNotes(BaseModel):
    schema_version: int = 1
    alignment_hash: str = ""
    extraction_hash: str = ""
    transcript_hash: str = ""
    pdf_hash: str = ""
    provenance: dict = {}
    items: list[ContentItem] = []
    validation: list[ValidationRecord] = []
    complete: bool = True
    coverage: dict = {}
    telemetry: dict = {}
    spans: list[dict] = []


CONTENT_PROMPT = """Extract the meaningful teaching units from this lecture transcript.
Return {"items": [...]} with one item per definition, reason, explanation, example,
derivation, comparison, exception, warning, correction, emphasis, exam_hint,
question_answer or slide_reference. Use type worked_example for examples.
Each item has type, title, content (full detailed explanation without a length limit),
source_quote (a continuous verbatim excerpt including recognition errors), short_note
(concise slide annotation), block_ids (only provided IDs), importance (1..5), language.
Preserve separate concepts, reasons and worked examples, even when the slide mentions
the topic. Avoid simply copying slide text. Do not invent facts, numbers, exam claims,
or conclusions. Every claim in content and short_note must follow from source_quote.
Use slide spelling for technical terms. Write consistently in the lecture's main
language; support Korean, English and mixed speech. If speech is unintelligible, skip it.
Use only these types: definition, explanation, reasoning, derivation, worked_example,
comparison, exception, warning, correction, emphasis, exam_hint, question_answer,
slide_reference. Never invent coordinates or timestamps.
"""


def _ask(provider, prompt, telemetry=None):
    for attempt in range(2):
        try:
            started = time.perf_counter()
            if telemetry is not None:
                telemetry["provider_calls"] += 1
            try:
                reply = provider.complete_json(prompt)
                if telemetry is not None:
                    telemetry["usage_events"].append(getattr(provider, "last_usage", None))
            finally:
                if telemetry is not None:
                    telemetry["provider_seconds"] += time.perf_counter() - started
            data = json.loads(reply)
            rows = data["items"]
            if not isinstance(rows, list):
                raise ValueError("Expected items array")
            return [ExtractedItem.model_validate(row).model_dump() for row in rows]
        except (ValueError, KeyError, TypeError):
            if attempt == 1:
                raise ValueError("Invalid content extraction reply twice")


def extract_content(alignment: Alignment, extraction: PdfExtraction, segments: list[Segment],
                    provider=None) -> DetailedNotes:
    provider = provider or get_provider()
    pages = {page.page: page for page in extraction.pages}
    result = DetailedNotes()
    started = time.perf_counter()
    result.telemetry = {"provider": provider.name, "model": provider.model, "provider_calls": 0,
                        "cache_hits": 0, "provider_seconds": 0.0, "usage_events": [],
                        "cost": None, "http_attempts": None}
    for span in build_spans(alignment, max_seconds=120):
        page = pages[span.page]
        local = [(index, segment) for index, segment in enumerate(segments)
                 if span.start <= segment.start and segment.end <= span.end]
        request = {
            "task": "content_extraction", "lecture_sample": sample_lecture(segments),
            "transcript": span.transcript,
            "blocks": [{"block_id": block.block_id, "text": block.text} for block in page.blocks],
        }
        prompt = CONTENT_PROMPT + INPUT_MARKER + json.dumps(request, ensure_ascii=False)
        prefix = hashlib.sha256(f"{span.page}:{span.start}:{prompt}".encode()).hexdigest()[:16]
        diagnostic = {"span_id": prefix, "page": span.page, "start": span.start, "end": span.end,
                      "status": "pending", "accepted": 0, "rejected": 0}
        result.spans.append(diagnostic)
        before_calls = result.telemetry["provider_calls"]
        try:
            rows = cached_json("content", [provider.name, provider.model, prompt],
                               lambda: _ask(provider, prompt, result.telemetry))
            result.telemetry["cache_hits"] += before_calls == result.telemetry["provider_calls"]
        except Exception as error:
            diagnostic["status"] = "unavailable"
            diagnostic["reason"] = type(error).__name__
            result.complete = False
            result.validation.append(ValidationRecord(item_id=prefix, status="unavailable",
                                      reason=type(error).__name__,
                                      recovery_attempted=result.telemetry["provider_calls"] - before_calls > 1))
            continue
        diagnostic["status"] = "empty" if not rows else "processed"
        for index, row in enumerate(rows):
            item = ExtractedItem.model_validate(row)
            item_id = f"concept_{prefix}_{index}"
            reason = None
            timed_segments = [segment for _, segment in local]
            timing = locate_quote(item.source_quote, timed_segments)
            words = [Segment(start=word.start, end=word.end, text=word.text)
                     for segment in timed_segments for word in segment.words
                     if segment.start <= word.start < word.end <= segment.end]
            precise = locate_quote(item.source_quote, words) if words else None
            if precise is not None:
                timing = precise
            if not quote_in_transcript(item.source_quote, span.transcript):
                reason = "source_quote_not_found"
            elif timing is None:
                reason = "source_timing_not_found"
            elif has_unsupported_number(item.title + " " + item.content + " " + item.short_note, item.source_quote):
                reason = "unsupported_number"
            elif not set(item.block_ids) <= {block.block_id for block in page.blocks}:
                reason = "unknown_block_id"
            if reason:
                diagnostic["rejected"] += 1
                result.validation.append(ValidationRecord(item_id=item_id, status="rejected", reason=reason,
                                          candidate=item))
                continue
            item.type = checked_note_type(item.type, item.source_quote)
            start, end = timing
            windows = [window for window in alignment.windows
                       if window.page == span.page and window.start < end and window.end > start]
            result.items.append(ContentItem(
                **item.model_dump(), id=item_id, page=span.page, start=start, end=end,
                segment_ids=[segment_id for segment_id, segment in local
                             if segment.start < end and segment.end > start],
                confidence=min((window.confidence for window in windows), default=0.0),
            ))
            diagnostic["accepted"] += 1
            result.validation.append(ValidationRecord(item_id=item_id, status="accepted",
                                      reason="quote_and_number_checks_passed"))
    accepted = len(result.items)
    rejected = sum(record.status == "rejected" for record in result.validation)
    result.coverage = {
        "extracted_candidates": accepted + rejected, "accepted_items": accepted,
        "rejected_items": rejected, "detailed_note_items": accepted,
        "extracted_item_retention": 1.0 if accepted else None,
        "lecture_semantic_coverage": None, "semantic_entailment_verified": False,
    }
    result.telemetry["runtime_seconds"] = time.perf_counter() - started
    events = result.telemetry["usage_events"]
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        complete_usage = (len(events) == result.telemetry["provider_calls"] and bool(events)
                          and all(event is not None and event.get(name) is not None for event in events))
        result.telemetry[name] = sum(event[name] for event in events) if complete_usage else None
    return result


def plan_annotations(content: DetailedNotes, extraction: PdfExtraction, density: Density, *, allow_overflow: bool = False) -> list[Note]:
    thresholds = {"minimal": 4, "balanced": 3, "detailed": 1}
    fractions = {"minimal": 0.25, "balanced": 0.5, "detailed": 0.8}
    planned = []
    for page in extraction.pages:
        budget = page.height * 2 * fractions[density]
        candidates = sorted((item for item in content.items if item.page == page.page),
                            key=lambda item: (-item.importance, item.start))
        for item in candidates:
            if item.importance < thresholds[density]:
                continue
            text = item.short_note
            cost = 24 * max(1, (len(text) + 19) // 20)
            if cost > budget and not allow_overflow:
                continue
            budget -= cost
            note_type = {"worked_example": "example", "question_answer": "question"}.get(item.type, item.type)
            if note_type not in {"example", "question", "emphasis", "exam_hint"}:
                note_type = "explanation"
            planned.append(Note(content_id=item.id, page=item.page, block_ids=item.block_ids,
                                type=note_type, note=text, quote=item.source_quote,
                                language=item.language, importance=item.importance,
                                start=item.start, end=item.end))
    return planned


def planning_report(content: DetailedNotes, annotations, density: Density) -> list[dict]:
    selected = {note.content_id for page in annotations.pages for note in page.notes}
    return [{"item_id": item.id, "status": "selected" if item.id in selected else "omitted",
             "reason": "rendered" if item.id in selected else "density_space_or_duplicate",
             "density": density} for item in content.items]
