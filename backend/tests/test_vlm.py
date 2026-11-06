"""CPU-only lifecycle tests. These do NOT claim actual model/GPU quality."""

import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from app.vlm.provider import GPUWorkerProvider, VLMFailure
from app.vlm.runtime import StructuredOutputFailure
from app.vlm.schema import PageAnalysis, PageRequest, VLMConfig, VisualRegion


class MockOOM(RuntimeError):
    pass


class DeterministicRuntime:
    def __init__(self, config):
        self.calls = 0

    def analyze(self, request, reduced=False):
        self.calls += 1
        return {"analysis": {"title": "fixture", "texts": [str(request.page)], "regions": []},
                "metrics": {"mock": True, "calls": self.calls, "reduced_budget": reduced}}

    def close(self):
        pass


class OOMThenReduced(DeterministicRuntime):
    def analyze(self, request, reduced=False):
        if not reduced:
            raise MockOOM()
        return super().analyze(request, reduced)


class AlwaysOOM(DeterministicRuntime):
    def analyze(self, request, reduced=False):
        raise MockOOM()


class SlowRuntime(DeterministicRuntime):
    def analyze(self, request, reduced=False):
        time.sleep(10)
        return super().analyze(request, reduced)


class InvalidJSONRuntime(DeterministicRuntime):
    def analyze(self, request, reduced=False):
        raise ValueError("invalid model output")


class InvalidMeasuredOutput(DeterministicRuntime):
    def analyze(self, request, reduced=False):
        raise StructuredOutputFailure("{truncated", {"schema_valid": False, "output_tokens": 64})


class CrashingRuntime(DeterministicRuntime):
    def analyze(self, request, reduced=False):
        os._exit(27)


@pytest.fixture
def vlm_input(tmp_path):
    # Lifecycle tests hash these bytes but never invoke an image decoder/model.
    image = tmp_path / "image.png"
    image.write_bytes(b"deterministic image fixture")
    config = VLMConfig(cache_dir=tmp_path / "cache", lease_path=tmp_path / "gpu.lock", timeout_seconds=15)
    return config, PageRequest(image_path=image, page=1)


def test_lazy_sequential_cache_invalidation_and_close(vlm_input):
    config, request = vlm_input
    provider = GPUWorkerProvider(config, runtime_factory=DeterministicRuntime)
    assert provider._process is None
    first = provider.analyze_page(request)
    assert first["metrics"]["calls"] == 1
    assert provider.analyze_page(request)["cache_hit"] is True
    assert provider.analyze_page(request)["new_inference"] is False
    second = provider.analyze_page(request.model_copy(update={"page": 2}))
    assert second["metrics"]["calls"] == 2
    request.image_path.write_bytes(b"changed image")
    assert provider.analyze_page(request)["metrics"]["calls"] == 3
    provider.close()
    assert provider._process is None
    assert provider.analyze_page(request, use_cache=False)["metrics"]["calls"] == 1
    provider.close()
    for override in ({"max_pixels": 131072}, {"revision": "different-revision"}):
        changed = config.model_copy(update=override)
        with GPUWorkerProvider(changed, runtime_factory=DeterministicRuntime) as p:
            assert p.analyze_page(request)["cache_hit"] is False


def test_oom_terminates_worker_and_retries_smaller_once(vlm_input):
    config, request = vlm_input
    with GPUWorkerProvider(config, runtime_factory=OOMThenReduced) as provider:
        result = provider.analyze_page(request)
        assert result["oom_count"] == 1
        assert result["metrics"]["reduced_budget"] is True
        # Runtime counter reset proves that a new child handled the retry.
        assert result["metrics"]["calls"] == 1
    with GPUWorkerProvider(config, runtime_factory=AlwaysOOM) as provider:
        with pytest.raises(VLMFailure, match="oom") as failure:
            provider.analyze_page(request)
        assert failure.value.details["oom_count"] == 2
        assert provider._process is None


def test_crashed_child_is_reaped_and_provider_recovers(vlm_input):
    config, request = vlm_input
    with GPUWorkerProvider(config, runtime_factory=CrashingRuntime) as provider:
        with pytest.raises(VLMFailure, match="worker_crashed"):
            provider.analyze_page(request)
        assert provider._process is None
        provider.factory = DeterministicRuntime
        assert provider.analyze_page(request)["analysis"]["title"] == "fixture"


def test_invalid_json_preserves_measurements_without_caching(vlm_input):
    config, request = vlm_input
    with GPUWorkerProvider(config, runtime_factory=InvalidMeasuredOutput) as provider:
        with pytest.raises(VLMFailure) as failure:
            provider.analyze_page(request)
        details = failure.value.details["failure"]
        assert details["metrics"] == {"schema_valid": False, "output_tokens": 64}
        assert details["raw_output"] == "{truncated"
        assert not list(config.cache_dir.glob("*.json"))


def test_timeout_kills_worker_releases_lease_and_can_restart(vlm_input):
    config, request = vlm_input
    short = config.model_copy(update={"timeout_seconds": 1})
    with GPUWorkerProvider(short, runtime_factory=SlowRuntime) as provider:
        with pytest.raises(VLMFailure, match="worker_timeout"):
            provider.analyze_page(request)
        assert provider._process is None
    with GPUWorkerProvider(config, runtime_factory=DeterministicRuntime) as recovered:
        assert recovered.analyze_page(request)["analysis"]["title"] == "fixture"


def test_shared_gpu_lease_and_thread_serialization(vlm_input):
    config, request = vlm_input
    with GPUWorkerProvider(config, runtime_factory=DeterministicRuntime) as first:
        first.analyze_page(request, use_cache=False)
        short = config.model_copy(update={"timeout_seconds": 0.1})
        with GPUWorkerProvider(short, runtime_factory=DeterministicRuntime) as second:
            with pytest.raises(VLMFailure, match="gpu_lease_timeout"):
                second.analyze_page(request, use_cache=False)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: first.analyze_page(request, use_cache=False), range(3)))
        assert sorted(r["metrics"]["calls"] for r in results) == [2, 3, 4]


def test_invalid_output_never_cached_and_demo_never_starts(vlm_input, monkeypatch):
    config, request = vlm_input
    with GPUWorkerProvider(config, runtime_factory=InvalidJSONRuntime) as provider:
        with pytest.raises(VLMFailure, match="inference:ValueError"):
            provider.analyze_page(request)
        assert not list(config.cache_dir.glob("*.json"))
    monkeypatch.setenv("DEMO_MODE", "1")
    with GPUWorkerProvider(config, runtime_factory=DeterministicRuntime) as provider:
        with pytest.raises(VLMFailure, match="demo_cache_miss"):
            provider.analyze_page(request)
        assert provider._process is None


@pytest.mark.parametrize("bbox", [(-0.1, 0, 1, 1), (0, 0, 2, 1), (0.8, 0, 0.1, 1),
                                  (0, 0, 0, 1), (0, 0, float("nan"), 1)])
def test_invalid_visual_regions_rejected(bbox):
    with pytest.raises(ValidationError):
        VisualRegion(kind="formula", description="fixture", bbox=bbox)


def test_no_model_switch_or_unknown_fields():
    with pytest.raises(ValidationError):
        VLMConfig(model_id="Qwen/Qwen3-VL-4B-Instruct")
    with pytest.raises(ValidationError):
        PageAnalysis(title="fixture", texts=[], regions=[], target_element_id="invented")
