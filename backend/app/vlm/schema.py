"""Bounded, single-page VLM inputs; independent of the production annotation schema."""

import json
from pathlib import Path
from typing import Annotated, Literal, Protocol
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from app.grounding.schema import TargetRequest

MODEL_ID = "Qwen/Qwen3-VL-8B-Instruct"
MODEL_REVISION = "0c351dd01ed87e9c1b53cbc748cba10e6187ff3b"
PROMPT_VERSION = "page-analysis-v2-normalized-regions"
REPO_ROOT = Path(__file__).resolve().parents[3]
Coordinate = Annotated[float, Field(ge=0, le=1)]


class VLMConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    model_id: Literal["Qwen/Qwen3-VL-8B-Instruct"] = MODEL_ID
    revision: str = MODEL_REVISION
    max_pixels: int = Field(default=262144, ge=16384, le=1048576)
    max_input_tokens: int = Field(default=2048, ge=256, le=8192)
    max_new_tokens: int = Field(default=512, ge=64, le=1024)
    timeout_seconds: float = Field(default=300, gt=0)
    # Also used across local VLM processes. Other GPU stages are NOT integrated yet.
    lease_path: Path = REPO_ROOT / "data/gpu.lock"
    cache_dir: Path = REPO_ROOT / "data/cache/vlm"
    model_cache_dir: Path = REPO_ROOT / "models/huggingface"
    local_files_only: bool = True


class PageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_path: Path
    page: int = Field(ge=1)


class VisualRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["formula", "figure"]
    description: str = Field(min_length=1, max_length=500)
    # Diagnostic image-space region ONLY. Never an accepted annotation target.
    bbox: tuple[Coordinate, Coordinate, Coordinate, Coordinate]

    @model_validator(mode="after")
    def ordered_box(self):
        x0, y0, x1, y1 = self.bbox
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("bbox must be a nonempty normalized image rectangle")
        return self


class PageAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=500)
    texts: list[str] = Field(max_length=80)
    regions: list[VisualRegion] = Field(max_length=30)


class VisualGroundingProvider(Protocol):
    def analyze_page(self, request: PageRequest, *, use_cache: bool = True) -> dict: ...
    def select_targets(self, request: "TargetRequest", *, use_cache: bool = True) -> dict: ...
    def close(self) -> None: ...


def analysis_prompt() -> str:
    return (
        f"{PROMPT_VERSION}. Inspect this single lecture slide. Return ONLY a JSON object "
        "matching this schema: " + json.dumps(PageAnalysis.model_json_schema()) + ". "
        "Copy the visible title and main texts literally. Identify visible formula/figure "
        "regions using normalized image coordinates: bbox=[left,top,right,bottom], "
        "every coordinate is a real number between 0.0 and 1.0 inclusive. "
        "Include visible mathematical expressions as formula regions, even when written "
        "with Latin words. Describe only visible objects; do not infer axis meanings. "
        "Do not invent obscured symbols. "
        "Use empty lists/string when uncertain. No markdown."
    )
