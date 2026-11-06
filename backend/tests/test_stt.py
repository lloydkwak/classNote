from types import SimpleNamespace

import pytest

from app.cache import CacheMissInDemoMode
from app.models import Segment, SpokenWord
from app.pipeline import stt


class CountingProvider(stt.STTProvider):
    name = "counting"
    model = "test"

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, prompt, language):
        self.calls += 1
        return [Segment(start=0, end=1, text="hello")]


def test_opt_in_gpu_stt_releases_model_and_lease_on_failure(monkeypatch):
    from contextlib import contextmanager
    events=[]
    @contextmanager
    def lease():
        events.append("acquire")
        try:
            yield
        finally:
            events.append("release")
    class Loader:
        def __call__(self,*args):
            return SimpleNamespace(model=SimpleNamespace(unload_model=lambda:events.append("unload")))
        def cache_clear(self):
            events.append("clear")
    monkeypatch.setattr("app.gpu.gpu_lease",lease)
    monkeypatch.setattr(stt,"_load_whisper",Loader())
    provider=stt.FasterWhisperProvider("small","cuda","float16")
    provider.serialize_gpu=True
    with pytest.raises(RuntimeError):
        with provider._session():
            events.append("infer")
            raise RuntimeError("STT failure")
    assert events==["acquire","infer","unload","clear","release"]


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
            words = [
                SimpleNamespace(start=1.0, end=1.2, word=" 이", probability=0.9),
                SimpleNamespace(start=1.2, end=1.8, word=" recursion이", probability=0.8),
            ]
            segment = SimpleNamespace(start=1.0, end=2.0, text=" 이 recursion이 멈춥니다 ", words=words)
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
    assert [(s.start, s.end, s.text) for s in segments] == [(1.0, 2.0, "이 recursion이 멈춥니다")]


def test_faster_whisper_keeps_word_times(monkeypatch, tmp_path):
    captured = fake_faster_whisper(monkeypatch)
    provider = stt.FasterWhisperProvider("small", "cpu", "int8")
    words = provider.transcribe(tmp_path / "a.mp3", "", None)[0].words
    assert captured["word_timestamps"] is True
    assert [(w.start, w.end, w.text, w.probability) for w in words] == [
        (1.0, 1.2, "이", 0.9),
        (1.2, 1.8, "recursion이", 0.8),
    ]


def test_word_times_survive_the_cache(provider, audio):
    provider.transcribe = lambda *args: [
        Segment(start=0, end=1, text="hi", words=[SpokenWord(start=0, end=1, text="hi")])
    ]
    stt.transcribe(audio, [])
    assert stt.transcribe(audio, [])[0].words[0].text == "hi"


def test_turning_on_word_times_misses_an_older_cache(provider, audio):
    stt.transcribe(audio, [])  # Cached under the older key, without word times.
    provider.word_times = True
    stt.transcribe(audio, [])
    assert provider.calls == 2


def test_demo_mode_falls_back_to_a_transcript_cached_before_word_times(
    provider, audio, monkeypatch
):
    stt.transcribe(audio, [])
    provider.word_times = True
    monkeypatch.setenv("DEMO_MODE", "1")
    assert stt.transcribe(audio, [])[0].text == "hello"
    assert provider.calls == 1


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


def test_live_mode_decodes_small_models_greedily_but_not_turbo(monkeypatch, tmp_path):
    monkeypatch.setenv("STT_PROVIDER", "faster-whisper")
    monkeypatch.setenv("STT_MODEL", "large-v3-turbo")
    monkeypatch.delenv("LIVE_STT_MODEL", raising=False)
    captured = fake_faster_whisper(monkeypatch)

    fast = stt.get_stt_provider(live=True)
    assert fast.model == "small"
    fast.transcribe(tmp_path / "a.mp3", "terms", None)
    assert (captured["beam_size"], captured["temperature"]) == (1, 0.0)
    assert captured["initial_prompt"] == "terms"

    captured.clear()
    stt.get_stt_provider("large-v3-turbo", live=True).transcribe(tmp_path / "a.mp3", "terms", None)
    assert "beam_size" not in captured

    captured.clear()
    upload = stt.get_stt_provider()
    assert upload.model == "large-v3-turbo"
    upload.transcribe(tmp_path / "a.mp3", "terms", None)
    assert "beam_size" not in captured


def test_prepare_runs_the_model_once_to_warm_it_up(monkeypatch):
    captured = fake_faster_whisper(monkeypatch)
    stt.FasterWhisperProvider("small", "cpu", "int8").prepare()
    assert captured["vad_filter"] is True


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
