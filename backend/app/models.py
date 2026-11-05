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
