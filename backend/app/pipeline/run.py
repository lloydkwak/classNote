"""Run the pipeline for a job. Each step reads and writes JSON in the job folder.

Because each step's inputs are on disk, a run can resume from any step.
"""

import json
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from app.config import get_settings
from app.models import Alignment, Annotations, Note, PdfExtraction, Segment, Window
from app.pipeline.audio import convert_to_mp3
from app.pipeline.block_align import generate_notes
from app.pipeline.consolidate import consolidate
from app.pipeline.figure_caption import caption_figures
from app.pipeline.export_pdf import EXPORT_NAME, export_annotated_pdf
from app.pipeline.page_align import align_windows
from app.pipeline.mentions import find_mentions
from app.pipeline.pdf_extract import add_geometry, extract_pdf
from app.pipeline.stt import transcribe
from app.pipeline.windowing import build_windows

JOB_OPTIONS_NAME = "job.json"
PDF_NAME = "input.pdf"
AUDIO_SOURCE_PREFIX = "audio_source"
AUDIO_NAME = "audio.mp3"
EXTRACTION_NAME = "extraction.json"
SEGMENTS_NAME = "segments.json"
WINDOWS_NAME = "windows.json"
ALIGNMENT_NAME = "alignment.json"
MENTIONS_NAME = "mentions.json"
NOTES_NAME = "notes.json"
NOTES_COMPLETE_NAME = "notes_complete.json"
ANNOTATIONS_NAME = "annotations.json"
REPLACE_ATTEMPTS = 10
REPLACE_RETRY_SECONDS = 0.1


def job_dir(job_id: str) -> Path:
    return get_settings().data_dir / "jobs" / job_id


def create_job(
    pdf: Path, audio: Path | None, job_id: str | None = None, stt_model: str | None = None
) -> str:
    """Copy the inputs into a new job folder and return its id."""
    job_id = job_id or uuid.uuid4().hex[:12]
    folder = job_dir(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf, folder / PDF_NAME)
    if audio is not None:
        shutil.copyfile(audio, folder / f"{AUDIO_SOURCE_PREFIX}{audio.suffix.lower()}")
    (folder / JOB_OPTIONS_NAME).write_text(json.dumps({"stt_model": stt_model}), encoding="utf-8")
    return job_id


def _job_stt_model(folder: Path) -> str | None:
    path = folder / JOB_OPTIONS_NAME
    return json.loads(path.read_text(encoding="utf-8"))["stt_model"] if path.exists() else None


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


def _step_caption(folder: Path) -> None:
    extraction = _read_extraction(folder)
    caption_figures(folder / PDF_NAME, extraction)
    (folder / EXTRACTION_NAME).write_text(
        extraction.model_dump_json(indent=2), encoding="utf-8"
    )


def _step_transcribe(folder: Path) -> None:
    sources = sorted(folder.glob(f"{AUDIO_SOURCE_PREFIX}.*"))
    audio = convert_to_mp3(sources[0], folder / AUDIO_NAME) if sources else None
    segments = transcribe(audio, _read_extraction(folder).key_terms, _job_stt_model(folder))
    _write_models(folder / SEGMENTS_NAME, segments)


def _step_window(folder: Path) -> None:
    windows = build_windows(_read_list(folder / SEGMENTS_NAME, Segment))
    _write_models(folder / WINDOWS_NAME, windows)


def _step_align(folder: Path) -> None:
    alignment = align_windows(_read_list(folder / WINDOWS_NAME, Window), _read_extraction(folder))
    (folder / ALIGNMENT_NAME).write_text(alignment.model_dump_json(indent=2), encoding="utf-8")


def _write_atomically(path: Path, text: str) -> None:
    """Replace a file that may be read at any moment, as live annotations are."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            temporary.replace(path)
            return
        except PermissionError:  # On Windows, while the file is being served.
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_RETRY_SECONDS)


def _read_alignment(folder: Path) -> Alignment:
    return Alignment.model_validate_json((folder / ALIGNMENT_NAME).read_text(encoding="utf-8"))


def _step_evidence(folder: Path) -> None:
    """For ANNOTATION_ENGINE=v2: the slide's words and free space, and what was said about them."""
    if get_settings().annotation_engine != "v2":
        return
    extraction = _read_extraction(folder)
    add_geometry(folder / PDF_NAME, extraction)
    (folder / EXTRACTION_NAME).write_text(extraction.model_dump_json(indent=2), encoding="utf-8")
    mentions = find_mentions(
        _read_alignment(folder), extraction, _read_list(folder / SEGMENTS_NAME, Segment)
    )
    _write_models(folder / MENTIONS_NAME, mentions)


def _step_notes(folder: Path) -> None:
    notes, complete = generate_notes(
        _read_alignment(folder),
        _read_extraction(folder),
        _read_list(folder / SEGMENTS_NAME, Segment),
    )
    _write_models(folder / NOTES_NAME, notes)
    (folder / NOTES_COMPLETE_NAME).write_text(json.dumps(complete), encoding="utf-8")


def _step_consolidate(folder: Path) -> None:
    annotations = consolidate(
        _read_list(folder / NOTES_NAME, Note), _read_extraction(folder), folder.name
    )
    complete = json.loads((folder / NOTES_COMPLETE_NAME).read_text(encoding="utf-8"))
    annotations.incomplete = not complete
    _write_atomically(folder / ANNOTATIONS_NAME, annotations.model_dump_json(indent=2))


def _step_export(folder: Path) -> None:
    annotations = Annotations.model_validate_json(
        (folder / ANNOTATIONS_NAME).read_text(encoding="utf-8")
    )
    export_annotated_pdf(folder / PDF_NAME, annotations, folder / EXPORT_NAME)


# (step name, status shown to the user, function), in run order.
STEPS: list[tuple[str, str, Callable[[Path], None]]] = [
    ("extract", "extracting_pdf", _step_extract),
    ("caption", "extracting_pdf", _step_caption),
    ("transcribe", "transcribing", _step_transcribe),
    ("window", "aligning", _step_window),
    ("align", "aligning", _step_align),
    ("evidence", "aligning", _step_evidence),
    ("notes", "writing_notes", _step_notes),
    ("consolidate", "writing_notes", _step_consolidate),
    ("export", "writing_notes", _step_export),
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


def read_annotations(job_id: str) -> Annotations:
    path = job_dir(job_id) / ANNOTATIONS_NAME
    return Annotations.model_validate_json(path.read_text(encoding="utf-8"))


def export_job(job_id: str) -> None:
    _step_export(job_dir(job_id))


def read_alignment(job_id: str) -> Alignment:
    path = job_dir(job_id) / ALIGNMENT_NAME
    return Alignment.model_validate_json(path.read_text(encoding="utf-8"))
