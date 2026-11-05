"""Upload a lecture, follow its progress, and fetch the results."""

import logging
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.models import JobStatus
from app.pipeline import run as pipeline
from app.pipeline.export_pdf import EXPORT_NAME

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs")

# One job at a time: CPU transcription is heavy, and LLM calls must stay sequential.
_executor = ThreadPoolExecutor(max_workers=1)

JOB_ID = re.compile(r"^[A-Za-z0-9_-]+$")
STATUS_NAME = "status.json"
_live_statuses: dict[str, JobStatus] = {}


def _folder(job_id: str) -> Path:
    """Return a job's folder. The id pattern keeps paths inside the jobs directory."""
    folder = pipeline.job_dir(job_id) if JOB_ID.match(job_id) else None
    if folder is None or not folder.is_dir():
        raise HTTPException(404, "Job not found.")
    return folder


def _write_status(folder: Path, status: str, error: str | None = None) -> None:
    job_status = JobStatus(status=status, error=error)
    # Polls read memory, so they can't catch the file half-written. The file is for
    # finding out about past jobs after a server restart.
    _live_statuses[folder.name] = job_status
    (folder / STATUS_NAME).write_text(job_status.model_dump_json(), encoding="utf-8")


def _run_job(job_id: str) -> None:
    folder = pipeline.job_dir(job_id)
    try:
        pipeline.run_pipeline(job_id, on_status=lambda status: _write_status(folder, status))
        _write_status(folder, "done")
    except Exception as error:
        log.exception("Job %s failed", job_id)
        _write_status(folder, "error", str(error))


def _save_upload(upload: UploadFile, directory: Path, default_suffix: str) -> Path:
    # Keep only the extension: the client's filename isn't safe to use as a path.
    suffix = Path(upload.filename or "").suffix.lower() or default_suffix
    path = directory / f"upload{default_suffix}{suffix}"
    with path.open("wb") as file:
        shutil.copyfileobj(upload.file, file)
    return path


@router.post("", status_code=202)
def create_job(pdf: UploadFile, audio: UploadFile | None = None) -> dict[str, str]:
    if Path(pdf.filename or "").suffix.lower() != ".pdf":
        raise HTTPException(400, "The slides must be a PDF file.")
    if audio is None and get_settings().stt_provider != "mock":
        raise HTTPException(400, "An audio file is required.")
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        pdf_path = _save_upload(pdf, temporary, ".pdf")
        audio_path = _save_upload(audio, temporary, ".audio") if audio else None
        job_id = pipeline.create_job(pdf_path, audio_path)
    _write_status(pipeline.job_dir(job_id), "queued")
    _executor.submit(_run_job, job_id)
    return {"job_id": job_id}


@router.get("/{job_id}")
def get_status(job_id: str) -> JobStatus:
    folder = _folder(job_id)
    if job_id in _live_statuses:
        return _live_statuses[job_id]
    path = folder / STATUS_NAME
    if path.exists():
        saved = JobStatus.model_validate_json(path.read_text(encoding="utf-8"))
        if saved.status in ("done", "error"):
            return saved
        # The server restarted while this job was running, and nothing will resume it.
        return JobStatus(status="error", error="The server restarted during this job.")
    # Jobs made with the CLI have no status file, so report what they produced.
    done = (folder / pipeline.ANNOTATIONS_NAME).exists()
    return JobStatus(status="done" if done else "queued")


def _serve(job_id: str, name: str, media_type: str, not_ready: str) -> FileResponse:
    path = _folder(job_id) / name
    if not path.exists():
        raise HTTPException(404, not_ready)
    return FileResponse(path, media_type=media_type)


@router.get("/{job_id}/annotations")
def get_annotations(job_id: str) -> FileResponse:
    return _serve(
        job_id, pipeline.ANNOTATIONS_NAME, "application/json", "Annotations aren't ready yet."
    )


@router.get("/{job_id}/pdf")
def get_pdf(job_id: str) -> FileResponse:
    return _serve(job_id, pipeline.PDF_NAME, "application/pdf", "PDF not found.")


@router.get("/{job_id}/audio")
def get_audio(job_id: str) -> FileResponse:
    """FileResponse answers HTTP Range requests, which seeking in the player needs."""
    return _serve(job_id, pipeline.AUDIO_NAME, "audio/mpeg", "This job has no audio.")


@router.get("/{job_id}/annotated.pdf")
def get_annotated_pdf(job_id: str) -> FileResponse:
    return _serve(job_id, EXPORT_NAME, "application/pdf", "The annotated PDF isn't ready yet.")


@router.get("/{job_id}/debug")
def get_debug(job_id: str) -> FileResponse:
    return _serve(
        job_id, pipeline.ALIGNMENT_NAME, "application/json", "The alignment isn't ready yet."
    )
