import pytest

from app.pipeline import page_align, stt


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Keep tests away from the real cache, keys, and paid or slow providers."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DEMO_MODE", "0")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("STT_PROVIDER", "mock")
    monkeypatch.setenv("STT_LANGUAGE", "")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Loaded models are kept between calls, and tests replace them with fakes.
    stt._load_whisper.cache_clear()
    page_align._load_e5.cache_clear()
