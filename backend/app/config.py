"""Settings read from the environment, with `.env` in the repo root loaded first."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


def _optional(name: str) -> str | None:
    return os.environ.get(name) or None


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_model: str | None
    stt_provider: str
    stt_model: str
    stt_device: str
    stt_compute_type: str
    stt_language: str | None
    live_stt_model: str
    live_stt_cpu_threads: int
    note_font: str
    annotation_engine: str
    embedding_provider: str
    embedding_model: str
    demo_mode: bool
    data_dir: Path
    ffmpeg_path: str | None
    visual_grounding: bool = False
    vlm_python: Path = REPO_ROOT / ".venv-vlm/bin/python"
    vlm_timeout: float = 180
    layout_engine: str = "v2"
    annotation_mode: str = "legacy"
    annotation_review_rounds: int = 2
    overview_mode: str = "synthesis"
    alignment_version: str = "v1"


def get_settings() -> Settings:
    """Read settings on each call so tests can change them through the environment."""
    return Settings(
        llm_provider=os.environ.get("LLM_PROVIDER", "gemini"),
        llm_model=_optional("LLM_MODEL"),
        stt_provider=os.environ.get("STT_PROVIDER", "faster-whisper"),
        stt_model=os.environ.get("STT_MODEL", "large-v3-turbo"),
        stt_device=os.environ.get("STT_DEVICE", "cpu"),
        stt_compute_type=os.environ.get("STT_COMPUTE_TYPE", "int8"),
        stt_language=_optional("STT_LANGUAGE"),
        live_stt_model=os.environ.get("LIVE_STT_MODEL", "small"),
        live_stt_cpu_threads=int(os.environ.get("LIVE_STT_CPU_THREADS", "6")),
        note_font=os.environ.get("NOTE_FONT", "pretendard"),
        annotation_engine=os.environ.get("ANNOTATION_ENGINE", "v1"),
        embedding_provider=os.environ.get("EMBEDDING_PROVIDER", "e5"),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "intfloat/multilingual-e5-base"),
        demo_mode=os.environ.get("DEMO_MODE", "0") == "1",
        data_dir=Path(os.environ.get("DATA_DIR") or REPO_ROOT / "data"),
        ffmpeg_path=_optional("FFMPEG_PATH"),
        visual_grounding=os.environ.get("VISUAL_GROUNDING", "0") == "1",
        vlm_python=Path(os.environ.get("VLM_PYTHON") or REPO_ROOT / ".venv-vlm/bin/python"),
        vlm_timeout=float(os.environ.get("VLM_TIMEOUT_SECONDS", "180")),
        layout_engine=os.environ.get("LAYOUT_ENGINE","v2"),
        annotation_mode=os.environ.get("ANNOTATION_MODE","legacy"),
        annotation_review_rounds=int(os.environ.get("ANNOTATION_REVIEW_ROUNDS","2")),
        overview_mode=os.environ.get("OVERVIEW_MODE","synthesis"),
        alignment_version=os.environ.get("ALIGNMENT_VERSION","v1"),
    )
