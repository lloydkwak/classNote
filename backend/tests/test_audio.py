import math
import struct
import wave

import pymupdf
import pytest

from app.pipeline.audio import FfmpegError, convert_to_mp3, find_ffmpeg, split_audio


def ffmpeg_available():
    try:
        find_ffmpeg()
    except FfmpegError:
        return False
    return True


needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg is not installed")


def write_wav(path, seconds, rate=44100, channels=2):
    with wave.open(str(path), "wb") as file:
        file.setnchannels(channels)
        file.setsampwidth(2)
        file.setframerate(rate)
        for index in range(int(seconds * rate)):
            sample = int(10000 * math.sin(2 * math.pi * 440 * index / rate))
            file.writeframes(struct.pack("<h", sample) * channels)


def mp3_duration_and_format(path):
    """Read the mp3 back through ffmpeg's decoder, by way of PyAV from faster-whisper."""
    import av

    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        return float(container.duration / av.time_base), stream.rate, stream.channels


@needs_ffmpeg
def test_convert_makes_mono_16khz_mp3(tmp_path):
    source = tmp_path / "input.wav"
    write_wav(source, seconds=2)
    output = convert_to_mp3(source, tmp_path / "out" / "audio.mp3")
    duration, rate, channels = mp3_duration_and_format(output)
    assert rate == 16000
    assert channels == 1
    assert duration == pytest.approx(2, abs=0.2)


@needs_ffmpeg
def test_split_returns_chunks_with_offsets(tmp_path):
    source = tmp_path / "input.wav"
    write_wav(source, seconds=5, rate=16000, channels=1)
    audio = convert_to_mp3(source, tmp_path / "audio.mp3")
    chunks = split_audio(audio, tmp_path / "chunks", chunk_seconds=2)
    assert [offset for _, offset in chunks] == [0.0, 2.0, 4.0]
    total = sum(mp3_duration_and_format(chunk)[0] for chunk, _ in chunks)
    assert total == pytest.approx(5, abs=0.3)


@needs_ffmpeg
def test_convert_reports_a_bad_input_file(tmp_path):
    document = pymupdf.open()
    document.new_page()
    not_audio = tmp_path / "slides.pdf"
    document.save(not_audio)
    with pytest.raises(FfmpegError, match="ffmpeg 실행 실패"):
        convert_to_mp3(not_audio, tmp_path / "audio.mp3")


def test_missing_ffmpeg_tells_the_user_what_to_install(monkeypatch):
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(FfmpegError, match="winget install"):
        find_ffmpeg()
