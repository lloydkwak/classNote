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
        # A single deadline includes queueing, model loading, generation and retry.
        deadline = time.monotonic() + self.config.timeout_seconds
        if not self._thread_lock.acquire(timeout=self.config.timeout_seconds):
            raise VLMFailure("queue_timeout")
        try:
            request = PageRequest.model_validate(request)
            payload = {"version": "worker-v1", "provider": f"{self.factory.__module__}.{self.factory.__qualname__}",
                       "config": self.config.model_dump(mode="json"),
                       "page": request.page, "image_sha256": hashlib.sha256(request.image_path.read_bytes()).hexdigest(),
                       "prompt": analysis_prompt()}
            key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            cache_path = self.config.cache_dir / f"{key}.json"
            if use_cache and cache_path.exists():
                result = json.loads(cache_path.read_text())
                PageAnalysis.model_validate(result["analysis"])
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
                    PageAnalysis.model_validate(result["analysis"])
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
