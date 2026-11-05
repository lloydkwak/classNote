"""Schemas for the pipeline's JSON files. Pages are 1-based; times are in seconds."""

from typing import Literal

from pydantic import BaseModel

BBox = tuple[float, float, float, float]


class Block(BaseModel):
    block_id: str  # e.g. "p3_b2"
    page: int
    text: str
    bbox: BBox  # normalized to 0-1 relative to page size
    is_title: bool = False


class Page(BaseModel):
    page: int
    title: str
    blocks: list[Block]
    low_text: bool = False


class PdfExtraction(BaseModel):
    pages: list[Page]
    key_terms: list[str]


class Segment(BaseModel):
    start: float
    end: float
    text: str


class Window(BaseModel):
    window_id: int
    start: float
    end: float
    text: str


class AlignedWindow(Window):
    page: int | None  # None means off-topic
    confidence: float
    raw_page: int  # argmax of similarity, before the order-constrained DP
    similarities: list[float]  # one per page, for the debug view


class Alignment(BaseModel):
    windows: list[AlignedWindow]
    pages: list[int]  # page number for each index of `similarities`


NoteType = Literal["explanation", "example", "emphasis", "exam_hint", "question"]


class Span(BaseModel):
    """Consecutive windows aligned to one page."""

    page: int
    start: float
    end: float
    transcript: str


class Note(BaseModel):
    page: int
    block_ids: list[str]  # Empty means a page-level note.
    type: NoteType
    note: str
    quote: str
    language: str
    importance: int
    start: float
    end: float


class Annotation(BaseModel):
    id: str
    block_ids: list[str]
    bboxes: list[BBox]
    type: NoteType
    note: str
    language: str
    start: float
    end: float


class PageAnnotations(BaseModel):
    page: int
    notes: list[Annotation]


class Annotations(BaseModel):
    job_id: str
    pages: list[PageAnnotations]


class JobStatus(BaseModel):
    status: Literal[
        "queued", "extracting_pdf", "transcribing", "aligning", "writing_notes", "done", "error"
    ]
    error: str | None = None
