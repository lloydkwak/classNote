"""Run the pipeline for a job. Each step reads and writes JSON in the job folder.

Because each step's inputs are on disk, a run can resume from any step.
"""

import json
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from app.config import get_settings
from app.models import Alignment, Note, PdfExtraction, Segment, Window
from app.pipeline.audio import convert_to_mp3
from app.pipeline.block_align import generate_notes
from app.pipeline.consolidate import consolidate
from app.pipeline.page_align import align_windows
from app.pipeline.pdf_extract import extract_pdf
from app.pipeline.stt import transcribe
from app.pipeline.windowing import build_windows

PDF_NAME = "input.pdf"
AUDIO_SOURCE_PREFIX = "audio_source"
AUDIO_NAME = "audio.mp3"
EXTRACTION_NAME = "extraction.json"
SEGMENTS_NAME = "segments.json"
WINDOWS_NAME = "windows.json"
ALIGNMENT_NAME = "alignment.json"
NOTES_NAME = "notes.json"
ANNOTATIONS_NAME = "annotations.json"


def job_dir(job_id: str) -> Path:
    return get_settings().data_dir / "jobs" / job_id


def create_job(pdf: Path, audio: Path | None, job_id: str | None = None) -> str:
    """Copy the inputs into a new job folder and return its id."""
    job_id = job_id or uuid.uuid4().hex[:12]
    folder = job_dir(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf, folder / PDF_NAME)
    if audio is not None:
        shutil.copyfile(audio, folder / f"{AUDIO_SOURCE_PREFIX}{audio.suffix.lower()}")
    return job_id


def _write_models(path: Path, models: list) -> None:
    path.write_text(
        json.dumps([model.model_dump() for model in models], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_list(path: Path, model: type) -> list:
    return [model(**row) for row in json.loads(path.read_text(encoding="utf-8"))]


def _read_extraction(folder: Path) -> PdfExtraction:
    return PdfExtraction.model_validate_json((folder / EXTRACTION_NAME).read_text(encoding="utf-8"))


def _step_extract(folder: Path) -> None:
    extraction = extract_pdf(folder / PDF_NAME)
    (folder / EXTRACTION_NAME).write_text(
        extraction.model_dump_json(indent=2), encoding="utf-8"
    )


def _step_transcribe(folder: Path) -> None:
    sources = sorted(folder.glob(f"{AUDIO_SOURCE_PREFIX}.*"))
    audio = convert_to_mp3(sources[0], folder / AUDIO_NAME) if sources else None
    segments = transcribe(audio, _read_extraction(folder).key_terms)
    _write_models(folder / SEGMENTS_NAME, segments)


def _step_window(folder: Path) -> None:
    windows = build_windows(_read_list(folder / SEGMENTS_NAME, Segment))
    _write_models(folder / WINDOWS_NAME, windows)


def _step_align(folder: Path) -> None:
    alignment = align_windows(_read_list(folder / WINDOWS_NAME, Window), _read_extraction(folder))
    (folder / ALIGNMENT_NAME).write_text(alignment.model_dump_json(indent=2), encoding="utf-8")


def _read_alignment(folder: Path) -> Alignment:
    return Alignment.model_validate_json((folder / ALIGNMENT_NAME).read_text(encoding="utf-8"))


def _step_notes(folder: Path) -> None:
    notes = generate_notes(_read_alignment(folder), _read_extraction(folder))
    _write_models(folder / NOTES_NAME, notes)


def _step_consolidate(folder: Path) -> None:
    annotations = consolidate(
        _read_list(folder / NOTES_NAME, Note), _read_extraction(folder), folder.name
    )
    (folder / ANNOTATIONS_NAME).write_text(
        annotations.model_dump_json(indent=2), encoding="utf-8"
    )


# (step name, status shown to the user, function), in run order.
STEPS: list[tuple[str, str, Callable[[Path], None]]] = [
    ("extract", "extracting_pdf", _step_extract),
    ("transcribe", "transcribing", _step_transcribe),
    ("window", "aligning", _step_window),
    ("align", "aligning", _step_align),
    ("notes", "writing_notes", _step_notes),
    ("consolidate", "writing_notes", _step_consolidate),
]
STEP_NAMES = [name for name, _, _ in STEPS]


def run_pipeline(
    job_id: str,
    from_step: str | None = None,
    to_step: str | None = None,
    on_status: Callable[[str], None] = lambda status: None,
) -> None:
    """Run steps `from_step` through `to_step`, inclusive, in order."""
    folder = job_dir(job_id)
    start = STEP_NAMES.index(from_step) if from_step else 0
    stop = STEP_NAMES.index(to_step) if to_step else len(STEPS) - 1
    for name, status, function in STEPS[start : stop + 1]:
        on_status(status)
        function(folder)


def read_alignment(job_id: str) -> Alignment:
    path = job_dir(job_id) / ALIGNMENT_NAME
    return Alignment.model_validate_json(path.read_text(encoding="utf-8"))
