"""Explicitly opt-in developer endpoints. Run the development server on localhost."""

import json
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.jobs import _folder
from app.evaluation.experiments import replay
from app.evaluation.schema import EvaluationDataset
from app.evaluation.storage import load_dataset, save_dataset
from app.models import Alignment, PdfExtraction, Segment


def enabled():
    if os.environ.get("EVALUATION_MODE") != "1":
        raise HTTPException(404, "Evaluation tools are disabled")


router = APIRouter(prefix="/api/jobs", dependencies=[Depends(enabled)])


class SaveRequest(BaseModel):
    dataset: EvaluationDataset
    confirm_human_verification: bool = False


@router.get("/{job_id}/evaluation")
def context(job_id: str):
    folder = _folder(job_id)
    required = [folder / name for name in ("alignment.json", "extraction.json", "segments.json")]
    if not all(path.exists() for path in required):
        raise HTTPException(409, "Run through alignment before inspecting this job")
    alignment = Alignment.model_validate_json(required[0].read_text(encoding="utf-8"))
    extraction = PdfExtraction.model_validate_json(required[1].read_text(encoding="utf-8"))
    segments = [Segment.model_validate(row) for row in json.loads(required[2].read_text(encoding="utf-8"))]
    predictions = {variant: replay(alignment, extraction, segments, variant).model_dump()
                   for variant in ("saved", "current", "hybrid", "hybrid_refined")}
    content_path = folder / "detailed_notes.json"
    content = json.loads(content_path.read_text(encoding="utf-8")) if content_path.exists() else None
    return {"dataset": load_dataset(folder), "predictions": predictions, "segments": segments,
            "detailed_notes": content}


@router.put("/{job_id}/evaluation")
def save(job_id: str, request: SaveRequest):
    dataset = request.dataset
    reviews = [row.review for rows in (dataset.alignment_labels, dataset.transitions,
               dataset.meaning_units, dataset.matches, dataset.meaning_review_ranges) for row in rows]
    if any(review.status == "verified" for review in reviews) and not request.confirm_human_verification:
        raise HTTPException(422, "Explicit human verification confirmation is required")
    try:
        return save_dataset(_folder(job_id), dataset)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
