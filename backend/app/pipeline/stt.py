"""Speech-to-text behind one interface. Pick a provider with `STT_PROVIDER`."""

import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from app.cache import cached_json, hash_file
from app.config import get_settings
from app.models import Segment
from app.pipeline.audio import split_audio
from app.samples import mock_transcript

# Whisper only keeps the last 224 tokens of a prompt. Korean uses more tokens per
# character than English, so this limit stays under that for either language.
MAX_PROMPT_CHARS = 400
OPENAI_MAX_BYTES = 25 * 1024 * 1024
OPENAI_CHUNK_SECONDS = 50 * 60  # About 12MB at the bitrate audio.py produces.


class STTProvider(ABC):
    name: str
    model: str
    needs_audio: bool = True

    @abstractmethod
    def transcribe(self, audio: Path, prompt: str, language: str | None) -> list[Segment]:
        """Return timestamped segments. `language=None` means auto-detect."""


class FasterWhisperProvider(STTProvider):
    name = "faster-whisper"

    def __init__(self, model: str, device: str, compute_type: str) -> None:
        self.model = model
        self._device = device
        self._compute_type = compute_type

    def transcribe(self, audio: Path, prompt: str, language: str | None) -> list[Segment]:
        from faster_whisper import WhisperModel  # Slow import, so only when transcribing.

        whisper = WhisperModel(self.model, device=self._device, compute_type=self._compute_type)
        segments, _ = whisper.transcribe(
            str(audio),
            initial_prompt=prompt or None,
            language=language,
            # Without this, one language is detected at the start and kept for the
            # whole file, which breaks lectures that switch languages.
            multilingual=language is None,
            vad_filter=True,
            # Stops one misheard phrase from repeating through the rest of the file.
            condition_on_previous_text=False,
        )
        return [
            Segment(start=segment.start, end=segment.end, text=segment.text.strip())
            for segment in segments
        ]


class OpenAIWhisperProvider(STTProvider):
    name = "openai"
    model = "whisper-1"

    def __init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI()

    def _transcribe_file(self, audio: Path, prompt: str, language: str | None) -> list[Segment]:
        options = {"language": language} if language else {}
        with audio.open("rb") as file:
            result = self._client.audio.transcriptions.create(
                model=self.model,
                file=file,
                response_format="verbose_json",
                prompt=prompt,
                **options,
            )
        return [
            Segment(start=segment.start, end=segment.end, text=segment.text.strip())
            for segment in result.segments or []
        ]

    def transcribe(self, audio: Path, prompt: str, language: str | None) -> list[Segment]:
        if audio.stat().st_size <= OPENAI_MAX_BYTES:
            return self._transcribe_file(audio, prompt, language)
        merged = []
        with tempfile.TemporaryDirectory() as directory:
            for chunk, offset in split_audio(audio, Path(directory), OPENAI_CHUNK_SECONDS):
                for segment in self._transcribe_file(chunk, prompt, language):
                    merged.append(
                        Segment(
                            start=segment.start + offset,
                            end=segment.end + offset,
                            text=segment.text,
                        )
                    )
        return merged


class MockSTTProvider(STTProvider):
    name = "mock"
    model = "mock"
    needs_audio = False

    def transcribe(self, audio: Path | None, prompt: str, language: str | None) -> list[Segment]:
        return mock_transcript()


def get_stt_provider() -> STTProvider:
    settings = get_settings()
    name = settings.stt_provider
    if name == "faster-whisper":
        return FasterWhisperProvider(
            settings.stt_model, settings.stt_device, settings.stt_compute_type
        )
    if name == "openai":
        return OpenAIWhisperProvider()
    if name == "mock":
        return MockSTTProvider()
    raise ValueError(f"Unknown STT_PROVIDER: {name!r}")


def build_prompt(key_terms: list[str]) -> str:
    """Join as many key terms as fit, keeping the order (slide titles come first)."""
    prompt = ""
    for term in key_terms:
        candidate = f"{prompt}, {term}" if prompt else term
        if len(candidate) > MAX_PROMPT_CHARS:
            break
        prompt = candidate
    return prompt


def transcribe(audio: Path | None, key_terms: list[str]) -> list[Segment]:
    """Transcribe with the configured provider, using the cache when possible."""
    settings = get_settings()
    provider = get_stt_provider()
    prompt = build_prompt(key_terms)
    if not provider.needs_audio:
        return provider.transcribe(audio, prompt, settings.stt_language)
    if audio is None:
        raise ValueError(f"STT provider {provider.name!r} needs an audio file.")
    key = [hash_file(audio), provider.name, provider.model, prompt, settings.stt_language]
    rows = cached_json(
        "stt",
        key,
        lambda: [
            segment.model_dump()
            for segment in provider.transcribe(audio, prompt, settings.stt_language)
        ],
    )
    return [Segment(**row) for row in rows]
