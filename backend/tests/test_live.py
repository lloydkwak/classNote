import json
import subprocess
import time
import wave

import pytest
from fastapi.testclient import TestClient

from app.api import jobs
from app.llm.base import LLMProvider, QuotaExhausted
from app.llm.mock import MockProvider
from app.main import app
from app.models import Segment
from app.pipeline import live
from app.pipeline import run as pipeline
from app.pipeline.audio import FfmpegError, find_ffmpeg
from app.pipeline.stt import STTProvider
from app.samples import SLIDES, make_test_pdf

SECONDS_PER_CHUNK = 60


def write_silence(path, seconds):
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(16000)
        file.writeframes(b"\0\0" * int(16000 * seconds))


@pytest.fixture(autouse=True)
def fake_decoder(monkeypatch):
    """Each fake chunk is its length in seconds, so tests don't need real audio or ffmpeg."""

    def decode(recording, output):
        write_silence(output, len(recording.read_bytes()) * SECONDS_PER_CHUNK)

    monkeypatch.setattr(live, "decode_to_wav", decode)
    monkeypatch.setattr(live, "convert_to_mp3", lambda source, output: output.write_bytes(b"mp3"))


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "slides.pdf"
    make_test_pdf(path)
    return path


def session_for(pdf, llm=None):
    job_id = pipeline.create_job(pdf, None)
    statuses = []

    def on_status(status, error=None, **counts):
        statuses.append((status, error, counts))

    session = live.LiveSession(job_id, None, on_status)
    session.statuses = statuses
    session.prepare()
    if llm is not None:
        session._llm = llm
    return session


class CountingLLM(MockProvider):
    """The mock's replies, with the transcript of each request recorded."""

    def __init__(self):
        self.requests = []

    def complete_json(self, prompt):
        self.requests.append(json.loads(prompt.rpartition("INPUT_JSON:")[2])["transcript"])
        return super().complete_json(prompt)


def feed(session, chunks, finish=True):
    for _ in range(chunks):
        session.add_audio(b"x")
        session.process()
    if finish:
        session.process(True)


def test_notes_build_up_and_are_complete_at_the_end(pdf):
    session = session_for(pdf)
    feed(session, 7)
    status, error, counts = session.statuses[-1]
    assert status == "done"
    assert error is None
    assert counts["chunks_received"] == counts["chunks_processed"] == 7
    assert counts["note_count"] > 0
    annotations = pipeline.read_annotations(session.job_id)
    assert annotations.incomplete is False
    assert {page.page for page in annotations.pages} >= {1, 2, 3, 4, 5}
    assert (session.folder / pipeline.AUDIO_NAME).exists()


def test_each_span_gets_one_llm_call_however_many_chunks_arrive(pdf):
    """The transcript of every request must be new: nothing is asked about twice."""
    llm = CountingLLM()
    session = session_for(pdf, llm)
    feed(session, 7)
    assert llm.requests
    assert len(llm.requests) == len(set(llm.requests))
    words = " ".join(llm.requests)
    assert words.count("오늘은 recursion을 배울 거예요") == 1


def test_a_closed_span_does_not_wait_for_the_end(pdf):
    llm = CountingLLM()
    session = session_for(pdf, llm)
    feed(session, 5, finish=False)
    assert llm.requests
    assert session.chunks_processed == 5
    assert any("오늘은 recursion" in request for request in llm.requests)
    assert session.note_count > 0


def test_the_open_span_only_gets_notes_when_the_talk_ends(pdf):
    llm = CountingLLM()
    session = session_for(pdf, llm)
    feed(session, 1, finish=False)
    assert llm.requests == []
    session.process(True)
    assert llm.requests


def test_earlier_pages_do_not_change_after_they_are_frozen(pdf):
    session = session_for(pdf)
    feed(session, 4, finish=False)
    frozen = list(session._frozen_pages)
    assert frozen
    feed(session, 3)
    assert session._frozen_pages[: len(frozen)] == frozen


def test_timestamps_come_from_the_whole_recording_not_the_chunk(pdf, monkeypatch):
    class ChunkClock(STTProvider):
        name = "clock"
        model = "clock"

        def transcribe(self, audio, prompt, language):
            with wave.open(str(audio), "rb") as file:
                length = file.getnframes() / file.getframerate()
            return [Segment(start=1, end=length - 5, text=f"chunk of {length:.0f} seconds")]

    monkeypatch.setattr(live, "get_stt_provider", lambda model: ChunkClock())
    session = session_for(pdf)
    feed(session, 2, finish=False)
    assert [(s.start, s.end) for s in session._segments] == [(1, 55), (61, 115)]


def test_a_segment_cut_off_by_the_chunk_end_is_transcribed_again_with_more_audio(pdf, monkeypatch):
    heard = []

    class CutOff(STTProvider):
        name = "cutoff"
        model = "cutoff"

        def transcribe(self, audio, prompt, language):
            with wave.open(str(audio), "rb") as file:
                length = file.getnframes() / file.getframerate()
            heard.append(length)
            if len(heard) == 1:
                return [
                    Segment(start=0, end=50, text="whole"),
                    Segment(start=50, end=60, text="half a wo"),
                ]
            return [Segment(start=0, end=20, text="half a word and more")]

    monkeypatch.setattr(live, "get_stt_provider", lambda model: CutOff())
    session = session_for(pdf)
    feed(session, 2)
    assert heard[0] == 60
    assert heard[1] == 70  # From the 50s where the cut-off segment began, to 120s.
    assert [(s.start, s.end, s.text) for s in session._segments] == [
        (0, 50, "whole"),
        (50, 70, "half a word and more"),
    ]


def test_final_chunk_keeps_a_segment_that_reaches_the_end(pdf, monkeypatch):
    class ToTheEnd(STTProvider):
        name = "end"
        model = "end"

        def transcribe(self, audio, prompt, language):
            return [Segment(start=0, end=60, text="the last words")]

    monkeypatch.setattr(live, "get_stt_provider", lambda model: ToTheEnd())
    session = session_for(pdf)
    session.add_audio(b"x")
    session.process(True)
    assert [s.text for s in session._segments] == ["the last words"]


class FailingOnce(MockProvider):
    def __init__(self, failure):
        self.failure = failure
        self.calls = 0

    def complete_json(self, prompt):
        self.calls += 1
        if self.calls == 1:
            raise self.failure
        return super().complete_json(prompt)


def test_one_overloaded_span_is_skipped_and_the_rest_continue(pdf):
    class Overloaded(Exception):
        code = 503

    llm = FailingOnce(Overloaded("503"))
    session = session_for(pdf, llm)
    feed(session, 7)
    annotations = pipeline.read_annotations(session.job_id)
    assert session.statuses[-1][0] == "done"
    assert annotations.pages  # The later spans still got notes.
    assert annotations.incomplete is True
    # The failed span isn't retried on every chunk, which would stall the talk.
    calls = llm.calls
    session._update_notes(pipeline._read_alignment(session.folder), True)
    assert llm.calls == calls


def test_llm_limit_stops_further_calls_but_the_talk_finishes(pdf):
    llm = FailingOnce(QuotaExhausted("limit"))
    session = session_for(pdf, llm)
    feed(session, 7)
    assert session.statuses[-1][0] == "done"
    assert llm.calls == 1
    assert pipeline.read_annotations(session.job_id).incomplete is True


def test_no_llm_key_still_finishes_with_the_transcript_and_alignment(pdf, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    session = session_for(pdf)
    feed(session, 3)
    assert session.statuses[-1][0] == "done"
    assert pipeline.read_annotations(session.job_id).incomplete is True
    assert pipeline.read_alignment(session.job_id).windows


def test_a_failed_chunk_does_not_lose_its_audio(pdf, monkeypatch):
    session = session_for(pdf)
    real = live.decode_to_wav
    calls = []

    def fails_once(recording, output):
        calls.append(1)
        if len(calls) == 1:
            raise FfmpegError("not enough data yet")
        real(recording, output)

    monkeypatch.setattr(live, "decode_to_wav", fails_once)
    session.add_audio(b"x")
    session.process()
    assert session._committed == 0
    assert session.statuses[-1][0] == "recording"
    session.add_audio(b"x")
    session.process()
    assert session._committed > 0


def test_a_failure_at_the_end_is_reported(pdf, monkeypatch):
    session = session_for(pdf)
    session.add_audio(b"x")

    def fail(*_):
        raise FfmpegError("broken")

    monkeypatch.setattr(live, "decode_to_wav", fail)
    session.process(True)
    assert session.statuses[-1][:2] == ("error", "broken")


def test_finishing_with_no_audio_is_an_error_not_a_hang(pdf):
    session = session_for(pdf)
    session.process(True)
    assert session.statuses[-1][0] == "error"


def test_real_ffmpeg_decodes_a_webm_delivered_in_parts(tmp_path, monkeypatch):
    """MediaRecorder's blobs are arbitrary slices of one file, so test exactly that."""
    monkeypatch.undo()
    try:
        ffmpeg = find_ffmpeg()
    except FfmpegError:
        pytest.skip("ffmpeg is not installed")
    wav = tmp_path / "in.wav"
    write_silence(wav, 12)
    webm = tmp_path / "whole.webm"
    made = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "libopus", str(webm)],
        capture_output=True,
    )
    if made.returncode != 0:
        pytest.skip("this ffmpeg has no Opus encoder")
    data = webm.read_bytes()
    growing = tmp_path / "growing.webm"
    growing.write_bytes(data[: len(data) // 2])
    decoded = tmp_path / "out.wav"
    live.decode_to_wav(growing, decoded)
    partial = live._wav_duration(decoded)
    assert 2 < partial < 11  # A file cut mid-stream still decodes what it has.

    with growing.open("ab") as file:
        file.write(data[len(data) // 2 :])
    live.decode_to_wav(growing, decoded)
    assert live._wav_duration(decoded) == pytest.approx(12, abs=0.2)


# The HTTP API, in order, the way the browser uses it.


@pytest.fixture
def client():
    return TestClient(app)


def wait_for(client, job_id, *statuses):
    for _ in range(300):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.05)
    raise AssertionError(f"Never reached {statuses}: {body}")


def start_live(client, pdf, **data):
    response = client.post("/api/jobs/live", files={"pdf": ("slides.pdf", pdf.read_bytes())}, data=data)
    assert response.status_code == 202
    return response.json()["job_id"]


def send_chunk(client, job_id, index):
    return client.post(
        f"/api/jobs/{job_id}/chunks",
        data={"index": index},
        files={"chunk": ("chunk.webm", b"x")},
    )


def test_api_walks_through_a_whole_talk(client, pdf):
    job_id = start_live(client, pdf)
    wait_for(client, job_id, "ready", "recording")
    for index in range(7):
        assert send_chunk(client, job_id, index).status_code == 202
    assert client.post(f"/api/jobs/{job_id}/finish").status_code == 202
    body = wait_for(client, job_id, "done", "error")
    assert body["status"] == "done"
    assert body["chunks_received"] == body["chunks_processed"] == 7
    assert body["note_count"] > 0

    annotations = client.get(f"/api/jobs/{job_id}/annotations").json()
    assert {page["page"] for page in annotations["pages"]} <= set(range(1, len(SLIDES) + 1))
    assert client.get(f"/api/jobs/{job_id}/audio").status_code == 200
    assert client.get(f"/api/jobs/{job_id}/debug").status_code == 200
    # Made on request, since a live job skips it to show notes sooner.
    exported = client.get(f"/api/jobs/{job_id}/annotated.pdf")
    assert exported.status_code == 200
    assert exported.content.startswith(b"%PDF")


def test_chunks_must_arrive_in_order_and_retries_are_harmless(client, pdf):
    job_id = start_live(client, pdf)
    assert send_chunk(client, job_id, 1).status_code == 409
    assert send_chunk(client, job_id, 0).status_code == 202
    assert send_chunk(client, job_id, 0).status_code == 202  # A retry.
    assert jobs._sessions[job_id].chunks_received == 1


def test_chunks_are_refused_for_a_job_that_is_not_live(client, pdf):
    job_id = pipeline.create_job(pdf, None)
    assert send_chunk(client, job_id, 0).status_code == 409
    assert client.post(f"/api/jobs/{job_id}/finish").status_code == 409
    assert send_chunk(client, "no-such-job", 0).status_code == 404


def test_speech_model_is_chosen_per_job_and_checked(client, pdf, monkeypatch):
    bad = client.post(
        "/api/jobs/live", files={"pdf": ("s.pdf", pdf.read_bytes())}, data={"stt_model": "../evil"}
    )
    assert bad.status_code == 400
    monkeypatch.setenv("STT_PROVIDER", "faster-whisper")
    monkeypatch.setattr("app.pipeline.stt._load_whisper", lambda *args: None)
    job_id = start_live(client, pdf, stt_model="base")
    other = start_live(client, pdf)
    # Wait, so nothing tries to load a real model after the fake loader is removed.
    wait_for(client, job_id, "ready")
    wait_for(client, other, "ready")
    assert jobs._sessions[job_id]._stt.model == "base"
    assert jobs._sessions[other]._stt.model == "large-v3-turbo"


def test_uploaded_job_uses_its_chosen_model(pdf, monkeypatch):
    chosen = []
    monkeypatch.setattr(
        pipeline, "transcribe", lambda audio, terms, model=None: chosen.append(model) or []
    )
    job_id = pipeline.create_job(pdf, None, stt_model="small")
    pipeline.run_pipeline(job_id, to_step="transcribe")
    assert chosen == ["small"]
