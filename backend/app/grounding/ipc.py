"""Bounded JSONL bridge to the existing provider in the isolated VLM interpreter."""

import json
import os
import select
import signal
import subprocess
import threading
import time
from pathlib import Path

from app.config import get_settings

MAX_MESSAGE = 8 * 1024 * 1024


class WorkerFailure(RuntimeError):
    def __init__(self, code, details=None):
        super().__init__(code)
        self.details = details or {}


def inside(path, root):
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_relative_to(Path(root).resolve(strict=True)) or not resolved.is_file():
        raise WorkerFailure("unsafe_input_path")
    return resolved


class IsolatedVLMProvider:
    def __init__(self, root: Path, *, python=None, timeout=None, entrypoint=None):
        self.root = root.resolve(strict=True)
        settings = get_settings()
        self.python = Path(python or settings.vlm_python)
        self.timeout = timeout or settings.vlm_timeout
        self.entrypoint = Path(entrypoint or Path(__file__).resolve().parents[2] / "scripts/vlm_worker.py")
        self.process = None
        self.buffer = b""
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self._log = None

    def _start(self):
        if self.process is not None:
            return
        self._log = (self.root / "vlm-stderr.log").open("ab")
        try:
            self.process = subprocess.Popen(
                [str(self.python.absolute()), str(self.entrypoint), "--root", str(self.root)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
                cwd=self.root, start_new_session=True,
            )
            os.set_blocking(self.process.stdin.fileno(),False)
        except OSError as error:
            self.close()
            raise WorkerFailure("worker_start_failed") from error

    def _terminate(self):
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=8)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        else:
            # A crashed IPC parent may have left its spawned GPU child in this group.
            try:
                os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()
        self.process = None
        self.buffer = b""

    def _read(self, deadline):
        while b"\n" not in self.buffer:
            if self.cancelled.is_set():
                raise WorkerFailure("worker_cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkerFailure("worker_timeout")
            ready, _, _ = select.select([self.process.stdout], [], [], min(remaining, 0.1))
            if ready:
                data = os.read(self.process.stdout.fileno(), 65536)
                if not data:
                    if self.cancelled.is_set():
                        raise WorkerFailure("worker_cancelled")
                    raise WorkerFailure("worker_exited", {"exit_code": self.process.poll()})
                self.buffer += data
                if len(self.buffer) > MAX_MESSAGE:
                    raise WorkerFailure("worker_response_too_large")
        line, self.buffer = self.buffer.split(b"\n", 1)
        try:
            return json.loads(line)
        except (ValueError, UnicodeDecodeError) as error:
            raise WorkerFailure("worker_protocol_error") from error

    def _write(self,message,deadline):
        view=memoryview(message)
        while view:
            if self.cancelled.is_set():
                raise WorkerFailure("worker_cancelled")
            remaining=deadline-time.monotonic()
            if remaining <= 0:
                raise WorkerFailure("worker_timeout")
            _,ready,_=select.select([],[self.process.stdin],[],min(remaining,0.1))
            if ready:
                written=os.write(self.process.stdin.fileno(),view)
                view=view[written:]

    def select_targets(self,request,*,use_cache=True):
        return self._call(request,'select_targets',use_cache=use_cache)

    def agent(self,request,*,use_cache=True):
        from app.annotation_agent.schema import VisionRequest
        request=VisionRequest.model_validate(request)
        return self._call(request,request.operation,use_cache=use_cache)

    def _call(self, request,operation, *, use_cache=True):
        canonical=inside(request.image_path, self.root)
        request=request.model_copy(update={"image_path":canonical})
        message = json.dumps({"op":operation, "request":request.model_dump(mode="json"),
                              "use_cache":use_cache}, ensure_ascii=False).encode() + b"\n"
        if len(message) > MAX_MESSAGE:
            raise WorkerFailure("worker_request_too_large")
        deadline = time.monotonic() + self.timeout
        if not self.lock.acquire(timeout=max(0,deadline-time.monotonic())):
            raise WorkerFailure("worker_queue_timeout")
        try:
            if self.cancelled.is_set():
                raise WorkerFailure("worker_cancelled")
            self._start()
            try:
                self._write(message,deadline)
                reply = self._read(deadline)
                if not isinstance(reply,dict):
                    raise WorkerFailure("worker_protocol_error")
                if not reply.get("ok"):
                    raise WorkerFailure(reply.get("error", "worker_error"), reply.get("details"))
                if not isinstance(reply.get("value"),dict):
                    raise WorkerFailure("worker_protocol_error")
                return reply["value"]
            except (OSError, ValueError) as error:
                self._terminate()
                raise WorkerFailure("worker_connection_failed") from error
            except WorkerFailure:
                self._terminate()
                raise
        finally:
            self.lock.release()

    def cancel(self):
        self.cancelled.set()
        process = self.process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def close(self):
        if self.process is not None and self.process.poll() is None:
            try:
                self._write(b'{"op":"close"}\n',time.monotonic()+1)
                self.process.wait(timeout=8)
            except (OSError, WorkerFailure, subprocess.TimeoutExpired):
                pass
        self._terminate()
        if self._log is not None:
            self._log.close()
            self._log = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
