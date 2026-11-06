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
    kind: Literal["text", "figure"] = "text"  # A figure's text is its generated caption.


class Word(BaseModel):
    """A word on the slide. Annotation actions point at these ids."""

    word_id: str  # e.g. "p3_w17"
    text: str
    bbox: BBox
    line_id: str  # e.g. "p3_l4"
    block_id: str | None = None  # The enclosing block, if any.
    source: Literal["pdf", "ocr"] = "pdf"


class Line(BaseModel):
    line_id: str
    word_ids: list[str]
    text: str
    bbox: BBox
    block_id: str | None = None
    source: Literal["pdf", "ocr"] = "pdf"


class Page(BaseModel):
    page: int
    title: str
    blocks: list[Block]
    low_text: bool = False
    width: float = 960  # In PDF points.
    height: float = 540
    # Filled by add_geometry, for ANNOTATION_ENGINE=v2.
    words: list[Word] = []
    lines: list[Line] = []
    free_rects: list[BBox] = []  # Empty rectangles on the slide, largest first. May overlap.


class PdfExtraction(BaseModel):
    pages: list[Page]
    key_terms: list[str]


class SpokenWord(BaseModel):
    start: float
    end: float
    text: str
    probability: float = 1.0


class Segment(BaseModel):
    start: float
    end: float
    text: str
    words: list[SpokenWord] = []  # Empty when the STT provider has no word times.

    def shifted(self, seconds: float) -> "Segment":
        return Segment(
            start=self.start + seconds,
            end=self.end + seconds,
            text=self.text,
            words=[
                word.model_copy(update={"start": word.start + seconds, "end": word.end + seconds})
                for word in self.words
            ],
        )


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


class Mention(BaseModel):
    """The professor said something that matches these words on the slide."""

    page: int
    word_ids: list[str]
    time: float  # When the matching speech starts.
    end: float
    spoken_text: str
    score: float  # 0-100.


class AudioRange(BaseModel):
    """One visit to a page, in the original recording's clock."""

    start: float
    end: float
    confidence: float  # Of the alignment, 0-1, weighted by window length.
    source_window_ids: list[str]  # e.g. ["w4", "w5"], the alignment windows behind this range.
    reason: Literal["first_visit", "revisit"]


class PageAudioEntry(BaseModel):
    page: int
    audio_ranges: list[AudioRange]  # In time order. Empty if nothing was matched to this page.
    primary_start: float | None
    total_duration: float


class PageAudio(BaseModel):
    schema_version: int = 2
    pages: list[PageAudioEntry]
    duration: float  # Of the recording, or of the transcript when that isn't known.
    unmatched_seconds: float = 0.0  # Off-topic speech, which belongs to no page.


NoteType = Literal["explanation", "example", "emphasis", "exam_hint", "question"]


class Span(BaseModel):
    """Consecutive windows aligned to one page."""

    page: int
    start: float
    end: float
    transcript: str


class Note(BaseModel):
    content_id: str | None = None
    page: int
    block_ids: list[str]  # Empty means a page-level note.
    type: NoteType
    note: str
    quote: str
    language: str
    importance: int
    start: float
    end: float


Point = tuple[float, float]


class Sketch(BaseModel):
    """How to draw a note by hand. Both the viewer and annotated.pdf draw exactly this.

    Coordinates are in PDF points. The page spans x from 0 to its width, and the writable
    gutters extend past it on both sides.
    """

    mark: Literal["underline", "circle", "bracket", "none"]
    strokes: list[list[Point]]  # The mark on the slide.
    arrow: list[list[Point]]  # From the margin note to the mark. Empty for page-level notes.
    side: Literal["left", "right"]
    x: float  # Top left of the margin note.
    y: float
    width: float
    height: float
    font_size: float
    line_height: float
    lines: list[str]  # Already wrapped, so both renderers break lines identically.


class Annotation(BaseModel):
    content_id: str | None = None
    id: str
    block_ids: list[str]
    bboxes: list[BBox]
    type: NoteType
    note: str
    quote: str  # The professor's own words, which the note condenses.
    language: str
    start: float
    end: float
    sketch: Sketch


class PageAnnotations(BaseModel):
    page: int
    width: float
    height: float
    gutter: float
    notes: list[Annotation]


class Annotations(BaseModel):
    planning: list[dict] = []
    job_id: str
    pages: list[PageAnnotations]
    note_font: str = "pretendard"  # Both renderers must use the font the layout measured.
    incomplete: bool = False  # The LLM's quota ran out, so some slides have no notes yet.


class JobStatus(BaseModel):
    status: Literal[
        "queued",
        "extracting_pdf",
        "transcribing",
        "aligning",
        "writing_notes",
        "preparing",  # A live job reading its slides and loading its models.
        "ready",  # A live job waiting for the talk to start.
        "recording",  # A live job receiving and processing chunks.
        "done",
        "error",
    ]
    error: str | None = None
    chunks_received: int = 0
    chunks_processed: int = 0
    note_count: int = 0
