"""Versioned labels and explicit human review, independent of pipeline predictions."""

from typing import Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Review(StrictModel):
    status: Literal["draft", "provisional", "uncertain", "verified"] = "draft"
    source: Literal["human", "synthetic", "legacy_inference", "system"] = "human"
    author: str = ""
    reviewed_at: str | None = None
    note: str = ""

    @model_validator(mode="after")
    def checked(self):
        if self.status == "verified" and (
            self.source != "human" or not self.author.strip() or not self.reviewed_at
        ):
            raise ValueError("Verified labels require a human author and review timestamp")
        if self.reviewed_at is not None:
            timestamp = datetime.fromisoformat(self.reviewed_at.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("Review timestamp must include a timezone")
        return self


class Interval(StrictModel):
    id: str = Field(min_length=1)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    pages: list[int] = []
    review: Review = Field(default_factory=Review)

    @model_validator(mode="after")
    def checked(self):
        if self.end <= self.start or any(page < 1 for page in self.pages):
            raise ValueError("Require positive duration and 1-based pages; [] means off-topic")
        if len(set(self.pages)) != len(self.pages):
            raise ValueError("Duplicate pages")
        return self


class Transition(StrictModel):
    id: str
    time: float = Field(ge=0)
    from_pages: list[int]
    to_pages: list[int]
    revisit: bool = False
    review: Review = Field(default_factory=Review)

    @model_validator(mode="after")
    def checked(self):
        if any(page < 1 for page in self.from_pages + self.to_pages):
            raise ValueError("Transition pages must be positive")
        if set(self.from_pages) == set(self.to_pages):
            raise ValueError("A transition must change pages")
        return self


class MeaningUnit(Interval):
    content: str = Field(min_length=1)
    source_quote: str = Field(min_length=1)
    segment_ids: list[int] = []
    importance: int = Field(default=3, ge=1, le=5)


class MatchReview(StrictModel):
    item_id: str
    unit_ids: list[str] = []
    relevant: bool | None = None
    entailed: bool | None = None
    duplicate_of: str | None = None
    review: Review = Field(default_factory=Review)


class InputIdentity(StrictModel):
    identifier: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class EvaluationDataset(StrictModel):
    schema_version: Literal[1] = 1
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(default="0.1.0", min_length=1)
    revision: int = Field(default=0, ge=0)
    lecture_id: str = Field(min_length=1)
    split: Literal["development", "test", "unassigned"] = "unassigned"
    inputs: dict[str, InputIdentity] = {}
    alignment_labels: list[Interval] = []
    transitions: list[Transition] = []
    meaning_units: list[MeaningUnit] = []
    matches: list[MatchReview] = []
    meaning_review_ranges: list[Interval] = []
    provenance: str = ""

    @model_validator(mode="after")
    def checked(self):
        for rows in (self.alignment_labels, self.transitions, self.meaning_units):
            identifiers = [row.id for row in rows]
            if len(set(identifiers)) != len(identifiers):
                raise ValueError("Label IDs must be unique within each collection")
        for rows in (self.alignment_labels, self.meaning_review_ranges):
            ordered = sorted(rows, key=lambda row: row.start)
            if any(first.end > second.start for first, second in zip(ordered, ordered[1:])):
                raise ValueError("Label ranges must not overlap")
        units = {unit.id for unit in self.meaning_units}
        if any(not set(match.unit_ids) <= units for match in self.matches):
            raise ValueError("Unknown meaning unit in match review")
        if len({match.item_id for match in self.matches}) != len(self.matches):
            raise ValueError("Only one review per extracted item")
        return self


def eligible(review: Review, allow_provisional: bool = False) -> bool:
    return review.status == "verified" or (allow_provisional and review.status == "provisional")
