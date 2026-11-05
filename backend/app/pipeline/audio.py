"""ffmpeg wrappers: convert uploads to mono 16kHz mp3, and split long files."""

import shutil
import subprocess
from pathlib import Path

from app.config import get_settings

SAMPLE_RATE = 16000
BITRATE = "32k"


class FfmpegError(RuntimeError):
    pass


def find_ffmpeg() -> str:
    configured = get_settings().ffmpeg_path
    found = configured or shutil.which("ffmpeg")
    if not found:
        raise FfmpegError(
            "ffmpeg를 찾을 수 없습니다. 설치한 뒤(Windows: winget install Gyan.FFmpeg) "
            "터미널을 새로 열거나, .env에 FFMPEG_PATH를 설정해 주세요."
        )
    return found


def _run_ffmpeg(*args: str) -> None:
    command = [find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", *args]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise FfmpegError(f"ffmpeg 실행 실패: {result.stderr.strip()}")


def convert_to_mp3(source: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        "-i", str(source),
        "-vn",
        "-ac", "1",
        "-ar", str(SAMPLE_RATE),
        "-b:a", BITRATE,
        str(output),
    )  # fmt: skip
    return output


def split_audio(source: Path, output_dir: Path, chunk_seconds: int) -> list[tuple[Path, float]]:
    """Split into chunks of `chunk_seconds`. Returns (chunk path, start offset in seconds)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        "-i", str(source),
        "-f", "segment",
        "-segment_time", str(chunk_seconds),
        "-c", "copy",
        str(output_dir / "chunk_%04d.mp3"),
    )  # fmt: skip
    chunks = sorted(output_dir.glob("chunk_*.mp3"))
    return [(chunk, float(index * chunk_seconds)) for index, chunk in enumerate(chunks)]
