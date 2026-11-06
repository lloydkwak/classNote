"""One subprocess per provider, serialized across threads/processes, killed on timeout/OOM."""

import fcntl
import hashlib
import json
import multiprocessing
import os
import tempfile
import threading
import time

from app.vlm.runtime import QwenRuntime, worker_main
from app.vlm.schema import PageAnalysis, PageRequest, VLMConfig, analysis_prompt


class VLMFailure(RuntimeError):
    def __init__(self, reason, *, details=None):
        super().__init__(reason)
        self.details = details or {}


class GPUWorkerProvider:
    def __init__(self, config: VLMConfig, *, runtime_factory=QwenRuntime):
        self.config, self.factory = config, runtime_factory
        self._thread_lock = threading.Lock()
        self._process = self._pipe = self._lease = None

    def _start(self, deadline):
        if self._process is not None:
            return
        self.config.lease_path.parent.mkdir(parents=True, exist_ok=True)
        self._lease = self.config.lease_path.open("a+")
        while True:
            try:
                fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._stop()
                    raise VLMFailure("gpu_lease_timeout")
                time.sleep(0.02)
        ctx = multiprocessing.get_context("spawn")
        self._pipe, child = ctx.Pipe()
        self._process = ctx.Process(target=worker_main, args=(child, self.config, self.factory), daemon=True)
        try:
            self._process.start()
        except Exception:
            self._stop()
            raise
        finally:
            child.close()

    def _stop(self, *, graceful=False):
        if self._process is not None:
            if self._process.pid is not None:
                if graceful and self._process.is_alive():
                    try:
                        self._pipe.send(None)
                        self._process.join(timeout=5)
                    except (BrokenPipeError, OSError):
                        pass
                if self._process.is_alive():
                    self._process.terminate()
                self._process.join(timeout=5)
                if self._process.is_alive():
                    self._process.kill()
                    self._process.join(timeout=5)
            self._process.close()
        if self._pipe is not None:
            self._pipe.close()
        if self._lease is not None:
            self._lease.close()
        self._process = self._pipe = self._lease = None

    def analyze_page(self, request: PageRequest, *, use_cache=True):
        request = PageRequest.model_validate(request)
        return self._request(request, analysis_prompt(), PageAnalysis, "analysis", use_cache=use_cache)

    def select_targets(self, request, *, use_cache=True):
        from app.grounding.schema import TargetRequest, TargetSelections
        from app.grounding.selection import selection_prompt, speech_error, validate_selection

        request = TargetRequest.model_validate(request)
        errors = {item.id: reason for item in request.items if (reason := speech_error(item, request.segments))}
        if errors:
            raise VLMFailure("invalid_speech_evidence", details={"items": errors})
        result = self._request(request, selection_prompt(request), TargetSelections, "selection", use_cache=use_cache,
                               context=request.model_dump(mode="json", exclude={"image_path"}))
        result["grounding"] = validate_selection(request, TargetSelections.model_validate(result["selection"]))
        return result

    def agent(self,request,*,use_cache=True):
        from app.annotation_agent.schema import VisionRequest,VisualPlan,Review
        from app.annotation_agent.prompts import vision_prompt
        request=VisionRequest.model_validate(request)
        schema=VisualPlan if request.operation=='agent_plan' else Review
        return self._request(request,vision_prompt(request),schema,request.operation,use_cache=use_cache,
                             context=request.model_dump(mode='json',exclude={'image_path'}))

    def _request(self, request, prompt, output_schema, result_field, *, use_cache, context=None):
        # A single deadline includes queueing, model loading, generation and retry.
        deadline = time.monotonic() + self.config.timeout_seconds
        if not self._thread_lock.acquire(timeout=self.config.timeout_seconds):
            raise VLMFailure("queue_timeout")
        try:
            payload = {"version": "worker-v1", "provider": f"{self.factory.__module__}.{self.factory.__qualname__}",
                       "config": self.config.model_dump(mode="json"),
                       "page": request.page if hasattr(request,"page") else request.registry.page,
                       "image_sha256": hashlib.sha256(request.image_path.read_bytes()).hexdigest(),
                       "prompt": prompt, "operation": result_field, "context": context}
            key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            cache_path = self.config.cache_dir / f"{key}.json"
            if use_cache and cache_path.exists():
                result = json.loads(cache_path.read_text())
                output_schema.model_validate(result[result_field])
                # Historical metrics remain explicitly historical.
                return {**result, "cache_hit": True, "new_inference": False}
            from app.config import get_settings
            if get_settings().demo_mode:
                raise VLMFailure("demo_cache_miss")
            for attempt in range(2):
                self._start(deadline)
                try:
                    self._pipe.send((request, attempt == 1))
                    remaining = max(0, deadline - time.monotonic())
                    if not self._pipe.poll(remaining):
                        raise VLMFailure("worker_timeout")
                    response = self._pipe.recv()
                except (EOFError, BrokenPipeError, OSError) as error:
                    self._stop()
                    raise VLMFailure("worker_crashed") from error
                except VLMFailure:
                    self._stop()
                    raise
                if response["ok"]:
                    result = response["value"]
                    output_schema.model_validate(result[result_field])
                    result.update(cache_hit=False, new_inference=True, oom_count=attempt)
                    result["provenance"] = payload
                    if use_cache:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        fd, temporary = tempfile.mkstemp(dir=cache_path.parent, suffix=".tmp")
                        try:
                            with os.fdopen(fd, "w") as stream:
                                json.dump(result, stream, ensure_ascii=False)
                            os.replace(temporary, cache_path)
                        finally:
                            if os.path.exists(temporary):
                                os.unlink(temporary)
                    return result
                # Invalid JSON is a response failure, not a corrupted CUDA context. Keep the
                # healthy lazy model for the next request; OOM still disposes of its child.
                if response["error_type"] != "StructuredOutputFailure":
                    self._stop(graceful=True)
                if response["kind"] != "oom" or attempt == 1:
                    raise VLMFailure(f"{response['kind']}:{response['error_type']}", details={
                        "oom_count": attempt + 1 if response["kind"] == "oom" else attempt,
                        "failure": response,
                    })
            raise AssertionError("unreachable")
        finally:
            self._thread_lock.release()

    def close(self):
        with self._thread_lock:
            self._stop(graceful=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
