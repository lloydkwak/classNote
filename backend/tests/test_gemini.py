from types import SimpleNamespace

import pytest

from app.llm import gemini
from app.llm.base import LIVE_RETRY, QuotaExhausted


class DailyLimit(Exception):
    code = 429
    details = {"error": {"details": [{"retryDelay": "77799s"}]}}


class FakeModels:
    def __init__(self, exhausted):
        self.exhausted = exhausted
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents))
        if model in self.exhausted:
            raise DailyLimit()
        return SimpleNamespace(text='{"ok": true}')


def provider(monkeypatch, exhausted=(), model=None):
    monkeypatch.setenv("GEMINI_API_KEY", "test-value")
    instance = gemini.GeminiProvider(model)
    fake = FakeModels(set(exhausted))
    instance._client = SimpleNamespace(models=fake)
    return instance, fake


def test_falls_back_to_the_next_model_at_the_daily_limit(monkeypatch):
    first, second = gemini.DEFAULT_MODELS[:2]
    instance, fake = provider(monkeypatch, exhausted=[first])
    assert instance.complete_json("hi") == '{"ok": true}'
    assert [model for model, _ in fake.calls] == [first, second]
    # The next call skips the exhausted model, and the cache key doesn't change.
    instance.complete_json("again")
    assert fake.calls[-1][0] == second
    assert instance.model == first


def test_reports_when_every_model_is_used_up(monkeypatch):
    instance, _ = provider(monkeypatch, exhausted=gemini.DEFAULT_MODELS)
    with pytest.raises(QuotaExhausted, match="tomorrow"):
        instance.complete_json("hi")


def test_an_explicit_model_does_not_fall_back(monkeypatch):
    instance, fake = provider(monkeypatch, exhausted=["chosen"], model="chosen")
    with pytest.raises(QuotaExhausted):
        instance.complete_json("hi")
    assert [model for model, _ in fake.calls] == ["chosen"]


def test_request_timeout_follows_the_retry_policy(monkeypatch):
    seen = []

    class Recording(FakeModels):
        def generate_content(self, model, contents, config):
            seen.append(config.http_options.timeout)
            return super().generate_content(model, contents, config)

    instance, _ = provider(monkeypatch)
    instance._client = SimpleNamespace(models=Recording(set()))
    instance.retry_policy = LIVE_RETRY
    instance.complete_json("hi")
    assert seen == [25000]


def test_images_are_sent_as_png_parts_before_the_prompt(monkeypatch):
    instance, fake = provider(monkeypatch)
    instance.describe_images("Describe", [b"\x89PNG one", b"\x89PNG two"])
    contents = fake.calls[0][1]
    assert contents[-1] == "Describe"
    assert [part.inline_data.mime_type for part in contents[:2]] == ["image/png", "image/png"]
    assert contents[0].inline_data.data == b"\x89PNG one"
