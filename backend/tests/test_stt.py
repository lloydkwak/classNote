from types import SimpleNamespace

import pytest

from app.cache import CacheMissInDemoMode
from app.models import Segment
from app.pipeline import stt


class CountingProvider(stt.STTProvider):
    name = "counting"
    model = "test"

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, prompt, language):
        self.calls += 1
        return [Segment(start=0, end=1, text="hello")]


@pytest.fixture
def provider(monkeypatch):
    counting = CountingProvider()
    monkeypatch.setattr(stt, "get_stt_provider", lambda model=None: counting)
    return counting


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"fake audio")
    return path


def test_second_run_uses_the_cache(provider, audio):
    first = stt.transcribe(audio, ["recursion"])
    second = stt.transcribe(audio, ["recursion"])
    assert first == second
    assert provider.calls == 1


def test_different_prompt_misses_the_cache(provider, audio):
    stt.transcribe(audio, ["recursion"])
    stt.transcribe(audio, ["deadlock"])
    assert provider.calls == 2


def test_different_audio_misses_the_cache(provider, audio, tmp_path):
    stt.transcribe(audio, [])
    other = tmp_path / "other.mp3"
    other.write_bytes(b"different audio")
    stt.transcribe(other, [])
    assert provider.calls == 2


def test_demo_mode_uses_the_cache_but_never_the_provider(provider, audio, monkeypatch):
    stt.transcribe(audio, ["recursion"])
    monkeypatch.setenv("DEMO_MODE", "1")
    assert stt.transcribe(audio, ["recursion"])[0].text == "hello"
    with pytest.raises(CacheMissInDemoMode):
        stt.transcribe(audio, ["not cached"])
    assert provider.calls == 1


def test_prompt_respects_the_limit_and_keeps_order():
    terms = [f"term{index}" for index in range(500)]
    prompt = stt.build_prompt(terms)
    assert len(prompt) <= stt.MAX_PROMPT_CHARS
    assert prompt.startswith("term0, term1, term2")


def fake_faster_whisper(monkeypatch):
    captured = {}

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, audio, **options):
            captured.update(options)
            segment = SimpleNamespace(start=1.0, end=2.0, text=" 이 recursion이 멈춥니다 ")
            return iter([segment]), None

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeWhisperModel)
    return captured


def test_faster_whisper_auto_detects_language_per_segment_by_default(monkeypatch, tmp_path):
    captured = fake_faster_whisper(monkeypatch)
    provider = stt.FasterWhisperProvider("small", "cpu", "int8")
    segments = provider.transcribe(tmp_path / "a.mp3", "recursion", None)
    assert captured["language"] is None
    assert captured["multilingual"] is True
    assert captured["initial_prompt"] == "recursion"
    assert segments == [Segment(start=1.0, end=2.0, text="이 recursion이 멈춥니다")]


def test_faster_whisper_language_override(monkeypatch, tmp_path):
    captured = fake_faster_whisper(monkeypatch)
    stt.FasterWhisperProvider("small", "cpu", "int8").transcribe(tmp_path / "a.mp3", "", "ko")
    assert captured["language"] == "ko"
    assert captured["multilingual"] is False


def test_a_job_can_choose_its_own_model(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "faster-whisper")
    monkeypatch.setenv("STT_MODEL", "small")
    assert stt.get_stt_provider().model == "small"
    assert stt.get_stt_provider("base").model == "base"


def test_the_model_stays_loaded_between_calls(monkeypatch, tmp_path):
    loads = []

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            loads.append(args)

        def transcribe(self, audio, **options):
            return iter([]), None

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeWhisperModel)
    provider = stt.FasterWhisperProvider("small", "cpu", "int8")
    provider.prepare()
    provider.transcribe(tmp_path / "a.mp3", "", None)
    provider.transcribe(tmp_path / "b.mp3", "", None)
    assert len(loads) == 1


def test_openai_splits_large_files_and_corrects_timestamps(monkeypatch, tmp_path):
    large = tmp_path / "large.mp3"
    large.write_bytes(b"x" * 10)
    monkeypatch.setattr(stt, "OPENAI_MAX_BYTES", 5)
    monkeypatch.setattr(
        stt,
        "split_audio",
        lambda audio, directory, seconds: [(large, 0.0), (large, 3000.0)],
    )
    provider = stt.OpenAIWhisperProvider.__new__(stt.OpenAIWhisperProvider)
    monkeypatch.setattr(
        provider,
        "_transcribe_file",
        lambda chunk, prompt, language: [Segment(start=1, end=2, text="hi")],
    )
    segments = provider.transcribe(large, "", None)
    assert [(segment.start, segment.end) for segment in segments] == [(1, 2), (3001, 3002)]


def test_openai_only_sends_language_when_overridden(tmp_path):
    captured = {}

    def create(**options):
        captured.update(options)
        return SimpleNamespace(segments=[])

    provider = stt.OpenAIWhisperProvider.__new__(stt.OpenAIWhisperProvider)
    provider._client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))
    )
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    provider.transcribe(audio, "terms", None)
    assert "language" not in captured
    assert captured["prompt"] == "terms"
