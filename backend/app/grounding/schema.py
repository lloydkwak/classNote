"""A model selects IDs; only the registry supplies PDF coordinates."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import Mention, Segment
from app.vlm.schema import Coordinate

Box = tuple[Coordinate, Coordinate, Coordinate, Coordinate]
Matrix = tuple[float, float, float, float, float, float]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Element(StrictModel):
    id: str = Field(min_length=1)
    page: int = Field(ge=1)
    kind: Literal["word", "symbol", "line", "formula", "figure", "block"]
    bbox: Box  # normalized unrotated crop-local PyMuPDF coordinates
    text: str
    source: Literal["pdf", "ocr"]
    parent_ids: list[str] = []
    marker: str | None = None
    recognition: str = "native_pdf"

    @model_validator(mode="after")
    def positive_box(self):
        if self.bbox[0] >= self.bbox[2] or self.bbox[1] >= self.bbox[3]:
            raise ValueError("empty or reversed element bbox")
        return self


class ElementRegistry(StrictModel):
    schema_version: str = "som-registry-v2-native-chars"
    pdf_sha256: str
    page: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: Literal[0, 90, 180, 270]
    cropbox: tuple[float, float, float, float]
    rotation_matrix: Matrix
    coordinate_space: Literal["pymupdf_unrotated_crop_normalized"] = "pymupdf_unrotated_crop_normalized"
    elements: list[Element]
    diagnostics: list[str] = []

    @model_validator(mode="after")
    def unique_elements(self):
        ids = [e.id for e in self.elements]
        markers = [e.marker for e in self.elements if e.marker is not None]
        if len(ids) != len(set(ids)) or len(markers) != len(set(markers)):
            raise ValueError("duplicate element ID or marker")
        if any(e.page != self.page for e in self.elements):
            raise ValueError("element on wrong page")
        for element in self.elements:
            if any(parent not in ids for parent in element.parent_ids):
                raise ValueError("unknown parent ID")
        return self


class Viewport(StrictModel):
    # Pixel transform: [x,y] in normalized registry → image pixels, including rotation/crop/zoom.
    pdf_normalized_to_pixels: Matrix
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    crop: Box = (0, 0, 1, 1)
    visible_element_ids: list[str]

    @model_validator(mode="after")
    def valid_view(self):
        if self.crop[0] >= self.crop[2] or self.crop[1] >= self.crop[3]:
            raise ValueError("empty viewport crop")
        a,b,c,d,_,_ = self.pdf_normalized_to_pixels
        if abs(a*d-b*c) < 1e-12:
            raise ValueError("singular viewport transform")
        if len(self.visible_element_ids) != len(set(self.visible_element_ids)):
            raise ValueError("duplicate visible element ID")
        return self


class GroundingItem(StrictModel):
    id: str = Field(min_length=1)
    page: int = Field(ge=1)
    content: str = Field(min_length=1)
    source_quote: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float
    segment_ids: list[int] = Field(min_length=1)
    block_ids: list[str] = []
    target_kind: Literal["word", "symbol", "formula", "figure", "any"] = "any"

    @model_validator(mode="after")
    def ordered_time(self):
        if self.end <= self.start:
            raise ValueError("invalid speech interval")
        return self

    @classmethod
    def from_content_item(cls, item, *, target_kind="any"):
        """Accept an existing rich_notes.ContentItem without changing that schema."""
        return cls(**{k: getattr(item, k) for k in
                     ("id", "page", "content", "source_quote", "start", "end", "segment_ids", "block_ids")},
                   target_kind=target_kind)


class TargetRequest(StrictModel):
    image_path: Path
    registry: ElementRegistry
    viewport: Viewport
    items: list[GroundingItem] = Field(min_length=1, max_length=4)
    segments: list[Segment]
    mentions: list[Mention] = []
    mentions_registry_hash: str = ""  # Reject stale/unversioned word-ID associations.
    image_mode: Literal["som", "original"] = "som"
    alignment_hash: str = ""  # Existing rich alignment fingerprint, when supplied by caller.
    min_confidence: float = Field(default=0.75, ge=0, le=1)

    @model_validator(mode="after")
    def consistent_request(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("duplicate knowledge ID")
        if any(item.page != self.registry.page for item in self.items):
            raise ValueError("knowledge on wrong registry page")
        available = {e.id for e in self.registry.elements}
        if not set(self.viewport.visible_element_ids) <= available:
            raise ValueError("unknown visible element ID")
        x0,y0,x1,y1 = self.viewport.crop
        if any(e.id in self.viewport.visible_element_ids and not
               (x0 <= e.bbox[0] < e.bbox[2] <= x1 and y0 <= e.bbox[1] < e.bbox[3] <= y1)
               for e in self.registry.elements):
            raise ValueError("visible element outside viewport crop")
        return self


class TargetProposal(StrictModel):
    knowledge_id: str
    page: int = Field(ge=1)
    status: Literal["selected", "ambiguous", "unmatched"]
    target_element_id: str | None
    target_text: str = ""
    relationship: Literal["explains", "derives", "compares", "warns", "refers_to"] = "explains"
    confidence: float = Field(ge=0, le=1)
    alternatives: list[str] = Field(default_factory=list, max_length=5)
    annotation_hint: Literal["underline", "circle_and_note", "box", "arrow", "short_note"] = "short_note"


class TargetSelections(StrictModel):
    selections: list[TargetProposal] = Field(max_length=8)
