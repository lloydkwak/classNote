"""Upload a lecture, follow its progress, and fetch the results."""

import logging
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.models import JobStatus
from app.pipeline import run as pipeline
from app.pipeline.export_pdf import EXPORT_NAME
from app.pipeline.live import LiveSession
from app.pipeline.stt import FASTER_WHISPER_MODELS

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs")

# One job at a time: CPU transcription is heavy, and LLM calls must stay sequential.
_executor = ThreadPoolExecutor(max_workers=1)

JOB_ID = re.compile(r"^[A-Za-z0-9_-]+$")
STATUS_NAME = "status.json"
_live_statuses: dict[str, JobStatus] = {}
_sessions: dict[str, LiveSession] = {}


def _folder(job_id: str) -> Path:
    """Return a job's folder. The id pattern keeps paths inside the jobs directory."""
    folder = pipeline.job_dir(job_id) if JOB_ID.match(job_id) else None
    if folder is None or not folder.is_dir():
        raise HTTPException(404, "Job not found.")
    return folder


def _write_status(folder: Path, status: str, error: str | None = None, **counts: int) -> None:
    job_status = JobStatus(status=status, error=error, **counts)
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


def _check_stt_model(stt_model: str | None) -> None:
    if stt_model and stt_model not in FASTER_WHISPER_MODELS:
        raise HTTPException(400, f"Unknown speech model: {stt_model}")


def _save_upload(upload: UploadFile, directory: Path, default_suffix: str) -> Path:
    # Keep only the extension: the client's filename isn't safe to use as a path.
    suffix = Path(upload.filename or "").suffix.lower() or default_suffix
    path = directory / f"upload{default_suffix}{suffix}"
    with path.open("wb") as file:
        shutil.copyfileobj(upload.file, file)
    return path


@router.post("", status_code=202)
def create_job(
    pdf: UploadFile, audio: UploadFile | None = None, stt_model: str | None = Form(None)
) -> dict[str, str]:
    _check_stt_model(stt_model)
    if Path(pdf.filename or "").suffix.lower() != ".pdf":
        raise HTTPException(400, "The slides must be a PDF file.")
    if audio is None and get_settings().stt_provider != "mock":
        raise HTTPException(400, "An audio file is required.")
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        pdf_path = _save_upload(pdf, temporary, ".pdf")
        audio_path = _save_upload(audio, temporary, ".audio") if audio else None
        job_id = pipeline.create_job(pdf_path, audio_path, stt_model=stt_model)
    _write_status(pipeline.job_dir(job_id), "queued")
    _executor.submit(_run_job, job_id)
    return {"job_id": job_id}


def _run_prepare(session: LiveSession) -> None:
    try:
        session.prepare()
    except Exception as error:
        log.exception("Preparing job %s failed", session.job_id)
        _write_status(session.folder, "error", str(error))


@router.post("/live", status_code=202)
def create_live_job(pdf: UploadFile, stt_model: str | None = Form(None)) -> dict[str, str]:
    """Start preparing for a talk. Audio then arrives through the chunks endpoint."""
    _check_stt_model(stt_model)
    if Path(pdf.filename or "").suffix.lower() != ".pdf":
        raise HTTPException(400, "The slides must be a PDF file.")
    with tempfile.TemporaryDirectory() as directory:
        pdf_path = _save_upload(pdf, Path(directory), ".pdf")
        job_id = pipeline.create_job(pdf_path, None, stt_model=stt_model)
    folder = pipeline.job_dir(job_id)
    session = LiveSession(
        job_id,
        stt_model,
        lambda status, error=None, **counts: _write_status(folder, status, error, **counts),
    )
    _sessions[job_id] = session
    _write_status(folder, "preparing")
    _executor.submit(_run_prepare, session)
    return {"job_id": job_id}


def _session(job_id: str) -> LiveSession:
    _folder(job_id)
    session = _sessions.get(job_id)
    if session is None:
        raise HTTPException(409, "This job isn't taking live audio.")
    return session


@router.post("/{job_id}/chunks", status_code=202)
def add_chunk(job_id: str, index: int = Form(...), chunk: UploadFile = Form(...)) -> dict[str, int]:
    """Add the next part of the recording. Parts must arrive in order."""
    session = _session(job_id)
    if index < session.chunks_received:  # A retry of a part that already arrived.
        return {"chunks_received": session.chunks_received}
    if index > session.chunks_received:
        raise HTTPException(409, f"Expected chunk {session.chunks_received}, got {index}.")
    session.add_audio(chunk.file.read())
    _executor.submit(session.process)
    return {"chunks_received": session.chunks_received}


@router.post("/{job_id}/finish", status_code=202)
def finish_live_job(job_id: str) -> dict[str, str]:
    """The talk is over: handle what's left and finish the job."""
    session = _session(job_id)
    _executor.submit(session.process, True)
    return {"status": "finishing"}


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
    """Live jobs skip the export, to show notes sooner, so it's made on first request."""
    folder = _folder(job_id)
    if not (folder / EXPORT_NAME).exists() and (folder / pipeline.ANNOTATIONS_NAME).exists():
        pipeline.export_job(job_id)
    return _serve(job_id, EXPORT_NAME, "application/pdf", "The annotated PDF isn't ready yet.")


@router.get("/{job_id}/debug")
def get_debug(job_id: str) -> FileResponse:
    return _serve(
        job_id, pipeline.ALIGNMENT_NAME, "application/json", "The alignment isn't ready yet."
    )
