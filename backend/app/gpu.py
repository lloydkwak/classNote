"""One shared GPU lease for opt-in STT stages and the existing VLM provider."""

import fcntl
import time
from contextlib import contextmanager

from app.config import REPO_ROOT, get_settings


@contextmanager
def gpu_lease():
    path=REPO_ROOT/"data/gpu.lock"
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a+") as lease:
        deadline=time.monotonic()+get_settings().vlm_timeout
        while True:
            try:
                fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("gpu_lease_timeout")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(lease,fcntl.LOCK_UN)
