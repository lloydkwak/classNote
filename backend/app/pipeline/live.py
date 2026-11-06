"""Process a talk while it's being recorded, so results are ready soon after it ends.

Audio arrives as parts of one growing webm file. Each time, only the new audio is
transcribed. To keep Gemini use low and notes stable:

- Decided windows are frozen, so earlier spans never change and never need a new LLM call.
- A span only gets notes once it has closed. An open one would change with every chunk.
"""

import json
import logging
import subprocess
import time
import wave
from collections.abc import Callable
from pathlib import Path

from app.llm import get_provider
from app.llm.base import LIVE_RETRY, LLMProvider, QuotaExhausted
from app.models import AlignedWindow, Alignment, Note, Segment, Span
from app.pipeline import run as pipeline
from app.pipeline.audio import FfmpegError, convert_to_mp3, find_ffmpeg
from app.pipeline.block_align import build_spans, generate_span_notes
from app.pipeline.figure_caption import caption_figures
from app.pipeline.page_align import align_windows, get_embedder, page_text
from app.pipeline.stt import build_prompt as build_stt_prompt
from app.pipeline.stt import get_stt_provider
from app.pipeline.windowing import DEFAULT_WINDOW_SECONDS, build_windows

log = logging.getLogger(__name__)

RECORDING_NAME = "recording.webm"
DECODED_NAME = "recording.wav"
CHUNK_NAME = "chunk.wav"
TIMINGS_NAME = "live_timings.json"

# A segment ending this close to the audio's end may be cut off, so it waits for more audio.
CUT_OFF_SECONDS = 1.0
# Whisper sometimes returns one huge segment. Redoing it would double the work, so keep it.
MAX_HELD_BACK_SECONDS = 15.0
# A span gets notes once it closes, so a long stay on one slide must not delay them for long.
MAX_SPAN_SECONDS = 90.0
MIN_AUDIO_SECONDS = 0.2  # Less than this isn't worth transcribing.
# The DP may still change its mind about this many of the latest closed windows. More would
# give it hindsight, but each one delays the notes on screen by about a window's length.
UNFROZEN_WINDOWS = 1

StatusCallback = Callable[..., None]


def decode_to_wav(recording: Path, output: Path) -> None:
    """Decode the whole recording. Opus decodes far faster than real time."""
    command = [
        find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(recording), "-vn", "-ac", "1", "-ar", "16000", str(output),
    ]  # fmt: skip
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise FfmpegError(f"ffmpeg 실행 실패: {result.stderr.strip()}")


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as file:
        return file.getnframes() / file.getframerate()


def _cut_wav(source: Path, start: float, output: Path) -> None:
    with wave.open(str(source), "rb") as reader:
        reader.setpos(min(int(start * reader.getframerate()), reader.getnframes()))
        frames = reader.readframes(reader.getnframes() - reader.tell())
        with wave.open(str(output), "wb") as writer:
            writer.setparams(reader.getparams())
            writer.writeframes(frames)


class LiveSession:
    def __init__(self, job_id: str, stt_model: str | None, on_status: StatusCallback) -> None:
        self.job_id = job_id
        self.folder = pipeline.job_dir(job_id)
        self.on_status = on_status
        self.chunks_received = 0
        self.chunks_processed = 0
        self.note_count = 0
        self._stt = get_stt_provider(stt_model, live=True)
        self._language = None
        self._stt_prompt = ""
        self._embedder = None
        self._llm: LLMProvider | None = None
        self._llm_available = True
        self._committed = 0.0  # Audio before this time has been transcribed for good.
        self._segments: list[Segment] = []
        self._frozen_pages: list[int | None] = []
        self._notes_by_span: dict[tuple[int, float], list[Note]] = {}
        self._failed_spans: set[tuple[int, float]] = set()
        self._complete = True
        self._timings: list[dict] = []

    def _report(self, status: str, error: str | None = None) -> None:
        self.on_status(
            status,
            error,
            chunks_received=self.chunks_received,
            chunks_processed=self.chunks_processed,
            note_count=self.note_count,
        )

    def prepare(self) -> None:
        """Do everything slow before the talk: read the slides and load the models."""
        self._report("preparing")
        pipeline.run_pipeline(self.job_id, to_step="extract")
        extraction = pipeline._read_extraction(self.folder)
        caption_figures(self.folder / pipeline.PDF_NAME, extraction, retry=LIVE_RETRY)
        (self.folder / pipeline.EXTRACTION_NAME).write_text(
            extraction.model_dump_json(indent=2), encoding="utf-8"
        )
        self._stt_prompt = build_stt_prompt(extraction.key_terms)
        self._stt.prepare()
        self._embedder = get_embedder()
        # Computes the page embeddings now, so the first chunk doesn't have to.
        self._embedder.embed_passages([text for page in extraction.pages if (text := page_text(page))])
        try:
            self._llm = get_provider()
            self._llm.retry_policy = LIVE_RETRY
        except Exception as error:  # Transcription and alignment can still run.
            log.warning("No LLM for this talk: %s", error)
            self._llm_available = False
        self._report("ready")

    def add_audio(self, data: bytes) -> None:
        with (self.folder / RECORDING_NAME).open("ab") as file:
            file.write(data)
        self.chunks_received += 1
        self._report("recording")

    def _transcribe_new_audio(self) -> tuple[list[Segment], float]:
        """Return new segments and the audio's total duration."""
        duration = _wav_duration(self.folder / DECODED_NAME)
        if duration - self._committed < MIN_AUDIO_SECONDS:
            new = []
        elif not self._stt.needs_audio:  # The mock knows its timeline; reveal it as time passes.
            transcript = self._stt.transcribe(None, "", None)
            new = [s for s in transcript if s.start >= self._committed and s.end <= duration]
        else:
            chunk = self.folder / CHUNK_NAME
            _cut_wav(self.folder / DECODED_NAME, self._committed, chunk)
            new = [
                segment.shifted(self._committed)
                for segment in self._stt.transcribe(chunk, self._stt_prompt, self._language)
            ]
        return new, duration

    def _commit_segments(self, new: list[Segment], duration: float, final: bool) -> None:
        if not self._stt.needs_audio:
            # The mock's lines don't line up with chunks, and a line that's still in progress
            # must stay available for the next chunk.
            if new:
                self._committed = new[-1].end
        elif (
            not final
            and new
            and new[-1].end >= duration - CUT_OFF_SECONDS
            and new[-1].end - new[-1].start <= MAX_HELD_BACK_SECONDS
        ):
            held_back = new.pop()
            self._committed = held_back.start
        else:
            self._committed = duration
        self._segments.extend(new)

    def _align(self, final: bool) -> Alignment:
        windows = build_windows(self._segments)
        # A window that's still filling up could change, until the talk ends.
        closed = (
            windows
            if final
            else [w for w in windows if w.end - w.start >= DEFAULT_WINDOW_SECONDS]
        )
        extraction = pipeline._read_extraction(self.folder)
        alignment = align_windows(
            closed, extraction, self._embedder, frozen_pages=self._frozen_pages
        )
        decided = [window.page for window in alignment.windows]
        keep = len(decided) if final else max(len(decided) - UNFROZEN_WINDOWS, len(self._frozen_pages))
        self._frozen_pages = decided[:keep]
        return alignment

    def _span_key(self, span: Span) -> tuple[int, float]:
        return span.page, span.start

    def _write_span_notes(self, span: Span, extraction) -> None:
        page = next(item for item in extraction.pages if item.page == span.page)
        key = self._span_key(span)
        if key in self._failed_spans or not self._llm_available or self._llm is None:
            self._complete = False
            return
        try:
            notes = generate_span_notes(span, page, self._llm, self._segments)
        except QuotaExhausted as error:
            log.warning("LLM limit reached during the talk: %s", error)
            self._llm_available = False
            self._complete = False
            return
        except Exception as error:  # One overloaded call must not stall the talk.
            log.warning("Skipping notes for page %d at %.0fs: %s", span.page, span.start, error)
            self._failed_spans.add(key)
            self._complete = False
            return
        self._notes_by_span[self._span_key(span)] = notes

    def _update_notes(self, alignment: Alignment, final: bool) -> None:
        frozen: list[AlignedWindow] = alignment.windows[: len(self._frozen_pages)]
        spans = build_spans(Alignment(windows=frozen, pages=alignment.pages), MAX_SPAN_SECONDS)
        if not final:
            spans = spans[:-1]  # The last span may still grow.
        extraction = pipeline._read_extraction(self.folder)
        for span in spans:
            if self._span_key(span) not in self._notes_by_span:
                self._write_span_notes(span, extraction)
        notes = [note for rows in self._notes_by_span.values() for note in rows]
        pipeline._write_models(self.folder / pipeline.NOTES_NAME, notes)
        (self.folder / pipeline.NOTES_COMPLETE_NAME).write_text(
            json.dumps(self._complete), encoding="utf-8"
        )
        pipeline._step_consolidate(self.folder)
        annotations = pipeline.read_annotations(self.job_id)
        self.note_count = sum(len(page.notes) for page in annotations.pages)

    def _save_progress(self, alignment: Alignment) -> None:
        pipeline._write_models(self.folder / pipeline.SEGMENTS_NAME, self._segments)
        (self.folder / pipeline.ALIGNMENT_NAME).write_text(
            alignment.model_dump_json(indent=2), encoding="utf-8"
        )

    def process(self, final: bool = False) -> None:
        """Handle all audio received so far. Runs on the single worker thread."""
        try:
            self._process(final)
        except Exception as error:
            log.exception("Live chunk failed for job %s", self.job_id)
            if final:
                self._report("error", str(error))
            # Otherwise the next chunk covers this audio too, since nothing was committed.

    def _process(self, final: bool) -> None:
        recording = self.folder / RECORDING_NAME
        if not recording.exists():
            raise ValueError("녹음된 내용이 없습니다.")
        timing: dict = {"final": final}
        clock = time.monotonic()

        def lap(name: str) -> None:
            nonlocal clock
            now = time.monotonic()
            timing[name] = round(now - clock, 2)
            clock = now

        self._report("recording")
        decode_to_wav(recording, self.folder / DECODED_NAME)
        lap("decode")
        before = self._committed
        new, duration = self._transcribe_new_audio()
        timing["audio_seconds"] = round(duration - before, 1)
        lap("transcribe")
        self._commit_segments(new, duration, final)
        alignment = self._align(final)
        lap("align")
        self._save_progress(alignment)
        self._update_notes(alignment, final)
        lap("notes")
        if final:
            convert_to_mp3(self.folder / DECODED_NAME, self.folder / pipeline.AUDIO_NAME)
            lap("audio")
        self.chunks_processed = self.chunks_received
        self._timings.append(timing)
        (self.folder / TIMINGS_NAME).write_text(json.dumps(self._timings, indent=1), encoding="utf-8")
        self._report("done" if final else "recording")
