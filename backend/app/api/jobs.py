"""Upload a lecture, follow its progress, and fetch the results."""

import logging
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.models import JobStatus, PageAudio
from app.pipeline import run as pipeline
from app.pipeline.export_pdf import EXPORT_NAME
from app.pipeline.live import LiveSession
from app.pipeline.stt import FASTER_WHISPER_MODELS
from app.pipeline.rich_notes import Density
from app.pipeline.export_pdf import export_annotated_pdf

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
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return folder


def _write_status(folder: Path, status: str, error: str | None = None, **counts: int) -> None:
    job_status = JobStatus(status=status, error=error, **counts)
    # Polls read memory, so they can't catch the file half-written. The file is for
    # finding out about past jobs after a server restart.
    _live_statuses[folder.name] = job_status
    (folder / STATUS_NAME).write_text(job_status.model_dump_json(), encoding="utf-8")


def _run_job(job_id: str, from_step: str | None = None) -> None:
    folder = pipeline.job_dir(job_id)
    try:
        options={"from_step":from_step} if from_step else {}
        pipeline.run_pipeline(job_id, on_status=lambda status: _write_status(folder, status),**options)
        _write_status(folder, "done")
    except Exception as error:
        log.exception("Job %s failed", job_id)
        _write_status(folder, "error", str(error))


def _check_stt_model(stt_model: str | None) -> None:
    if stt_model and stt_model not in FASTER_WHISPER_MODELS:
        raise HTTPException(400, f"알 수 없는 음성 인식 모델입니다: {stt_model}")


def _save_upload(upload: UploadFile, directory: Path, default_suffix: str) -> Path:
    # Keep only the extension: the client's filename isn't safe to use as a path.
    suffix = Path(upload.filename or "").suffix.lower() or default_suffix
    path = directory / f"upload{default_suffix}{suffix}"
    with path.open("wb") as file:
        limit=(100 if default_suffix==".pdf" else 1024)*1024*1024
        size=0
        while chunk:=upload.file.read(1024*1024):
            size+=len(chunk)
            if size>limit:
                raise HTTPException(413,"파일이 업로드 크기 제한을 초과했습니다.")
            file.write(chunk)
        if not size:
            raise HTTPException(400,"빈 파일은 업로드할 수 없습니다.")
    return path


@router.post("", status_code=202)
def create_job(
    pdf: UploadFile, audio: UploadFile | None = None, stt_model: str | None = Form(None),
    visual_grounding: bool | None = Form(None),
    annotation_mode: Literal["legacy","ai_planner","visual_agent"] | None = Form(None),
) -> dict[str, str]:
    _check_stt_model(stt_model)
    if Path(pdf.filename or "").suffix.lower() != ".pdf":
        raise HTTPException(400, "슬라이드는 PDF 파일이어야 합니다.")
    if audio is None and get_settings().stt_provider != "mock":
        raise HTTPException(400, "오디오 파일이 필요합니다.")
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        pdf_path = _save_upload(pdf, temporary, ".pdf")
        audio_path = _save_upload(audio, temporary, ".audio") if audio else None
        job_id = pipeline.create_job(pdf_path, audio_path, stt_model=stt_model,visual_grounding=visual_grounding,annotation_mode=annotation_mode)
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
        raise HTTPException(400, "슬라이드는 PDF 파일이어야 합니다.")
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
        raise HTTPException(409, "이 작업은 실시간 녹음을 받지 않습니다.")
    return session


@router.post("/{job_id}/chunks", status_code=202)
def add_chunk(job_id: str, index: int = Form(...), chunk: UploadFile = Form(...)) -> dict[str, int]:
    """Add the next part of the recording. Parts must arrive in order."""
    session = _session(job_id)
    if index < session.chunks_received:  # A retry of a part that already arrived.
        return {"chunks_received": session.chunks_received}
    if index > session.chunks_received:
        raise HTTPException(409, f"{session.chunks_received}번 구간을 기다리고 있었는데 {index}번이 왔습니다.")
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
        return JobStatus(status="error", error="작업 중에 서버가 다시 시작되었습니다.")
    # Jobs made with the CLI have no status file, so report what they produced.
    done = (folder / pipeline.ANNOTATIONS_NAME).exists()
    return JobStatus(status="done" if done else "queued")


def _serve(job_id: str, name: str, media_type: str, not_ready: str) -> FileResponse:
    path = _folder(job_id) / name
    if not path.exists():
        raise HTTPException(404, not_ready)
    return FileResponse(path, media_type=media_type)


@router.get("/{job_id}/annotations")
def get_annotations(job_id: str, density: Density | None = None, layout: Literal["v1","v2"] | None = None,
                    ink_style: Literal["clean","natural","highlighter"] = "natural"):
    folder = _folder(job_id)
    if (folder / pipeline.CONTENT_NAME).exists():
        try:
            return pipeline.density_annotations(job_id, density or "balanced",layout,ink_style)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
    if not (folder/pipeline.ANNOTATIONS_NAME).exists():
        raise HTTPException(404,"노트가 아직 준비되지 않았습니다.")
    return pipeline.density_annotations(job_id,density or "balanced",layout,ink_style)


@router.get("/{job_id}/detailed-notes")
def get_detailed_notes(job_id: str):
    folder = _folder(job_id)
    if not (folder / pipeline.CONTENT_NAME).exists():
        raise HTTPException(404, "상세 노트가 없습니다. notes 단계부터 다시 실행해 주세요.")
    try:
        return pipeline.read_detailed_notes(job_id)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/{job_id}/page-audio")
def get_page_audio(job_id: str) -> PageAudio:
    """Each page's ranges of the recording. Works with or without notes."""
    folder = _folder(job_id)
    if not (folder / pipeline.ALIGNMENT_NAME).exists():
        raise HTTPException(404, "정렬 결과가 아직 준비되지 않았습니다.")
    return pipeline.read_page_audio(job_id)


@router.get("/{job_id}/pdf")
def get_pdf(job_id: str) -> FileResponse:
    return _serve(job_id, pipeline.PDF_NAME, "application/pdf", "PDF를 찾을 수 없습니다.")


@router.get("/{job_id}/audio")
def get_audio(job_id: str) -> FileResponse:
    """FileResponse answers HTTP Range requests, which seeking in the player needs."""
    return _serve(job_id, pipeline.AUDIO_NAME, "audio/mpeg", "이 작업에는 오디오가 없습니다.")


@router.get("/{job_id}/annotated.pdf")
def get_annotated_pdf(job_id: str, density: Density | None = None, layout: Literal["v1","v2"] | None = None,
                      ink_style: Literal["clean","natural","highlighter"] = "natural") -> FileResponse:
    """Live jobs skip the export, to show notes sooner, so it's made on first request."""
    folder = _folder(job_id)
    if (folder / pipeline.CONTENT_NAME).exists():
        density = density or "balanced"
        try:
            target=pipeline.export_density(job_id,density,layout=layout,ink_style=ink_style)
        except ValueError as error:
            raise HTTPException(409,str(error)) from error
        return FileResponse(target, media_type="application/pdf")
    if not (folder/pipeline.ANNOTATIONS_NAME).exists():
        raise HTTPException(404,"필기 PDF가 아직 준비되지 않았습니다.")
    return FileResponse(pipeline.export_density(job_id,density or "balanced",layout=layout,ink_style=ink_style),media_type="application/pdf")


@router.post("/{job_id}/retry", status_code=202)
def retry_job(job_id: str):
    folder=_folder(job_id)
    status=get_status(job_id)
    incomplete=False
    if status.status=="done" and (folder/pipeline.CONTENT_NAME).exists():
        try:
            incomplete=not pipeline.read_detailed_notes(job_id).complete
        except ValueError:
            raise HTTPException(409,"원본 또는 정렬이 변경되어 작업을 다시 확인해야 합니다.")
    if status.status!="error" and not incomplete:
        raise HTTPException(409,"실패한 작업만 다시 실행할 수 있습니다.")
    _write_status(folder,"queued")
    # Original uploads stay in place. Existing stage caches avoid repeat provider calls.
    if incomplete:
        _executor.submit(_run_job,job_id,"notes")
    else:
        _executor.submit(_run_job,job_id)
    return {"job_id":job_id}


@router.get("/{job_id}/debug")
def get_debug(job_id: str) -> FileResponse:
    return _serve(
        job_id, pipeline.ALIGNMENT_NAME, "application/json", "정렬 결과가 아직 준비되지 않았습니다."
    )
