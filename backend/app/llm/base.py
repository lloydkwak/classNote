import logging
import random
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

import httpx

log = logging.getLogger(__name__)

T = TypeVar("T")

RETRYABLE_STATUS_CODES = {429, 503, 504}
MAX_RETRIES = 4
RETRY_DELAY = re.compile(r"^(\d+(?:\.\d+)?)s$")


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = MAX_RETRIES
    initial_backoff: float = 5.0
    max_backoff: float = 120.0  # A longer server-requested wait means giving up.
    request_timeout: float = 120.0


DEFAULT_RETRY = RetryPolicy()
# During a live talk, a slow span isn't worth waiting for: skip it instead.
LIVE_RETRY = RetryPolicy(max_retries=1, initial_backoff=1.0, max_backoff=5.0, request_timeout=25.0)


class QuotaExhausted(RuntimeError):
    """The server asks for a wait too long to be worth it, such as a daily limit."""


class LLMProvider(ABC):
    """Callers send requests one at a time, which keeps free-tier use sequential."""

    name: str
    model: str
    retry_policy: RetryPolicy = DEFAULT_RETRY

    @abstractmethod
    def complete_json(self, prompt: str) -> str:
        """Return the model's reply to `prompt` as JSON text."""

    def describe_images(self, prompt: str, images: list[bytes]) -> str:
        """Return a JSON reply to `prompt` about these PNG images, given in order."""
        raise NotImplementedError(f"{self.name} can't read images.")


def is_retryable_error(error: Exception) -> bool:
    """Recognize network failures, and rate limiting or overload from any SDK."""
    if isinstance(error, httpx.TransportError):  # All three provider SDKs use httpx.
        return True
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    return status in RETRYABLE_STATUS_CODES


def suggested_delay(error: Exception) -> float | None:
    """Read the RetryInfo delay that Gemini's errors carry, if there is one."""
    details = getattr(error, "details", None)
    body = details.get("error", details) if isinstance(details, dict) else {}
    for item in body.get("details", []) if isinstance(body, dict) else []:
        match = RETRY_DELAY.match(str(item.get("retryDelay", "")))
        if match:
            return float(match.group(1))
    return None


def with_backoff(
    call: Callable[[], T],
    sleep: Callable[[float], None] = time.sleep,
    policy: RetryPolicy = DEFAULT_RETRY,
) -> T:
    """Run `call`, waiting and retrying on network, 429, and 503 errors. Other errors propagate."""
    delay = policy.initial_backoff
    for attempt in range(policy.max_retries + 1):
        try:
            return call()
        except Exception as error:
            if not is_retryable_error(error) or attempt == policy.max_retries:
                raise
            suggested = suggested_delay(error)
            if suggested is not None and suggested > policy.max_backoff:
                raise QuotaExhausted(f"Quota reached; retry in {suggested:.0f}s.") from error
            wait = max(suggested or 0, min(delay, policy.max_backoff)) + random.uniform(0, 1)
            log.warning("LLM call failed temporarily; retrying in %.0fs", wait)
            sleep(wait)
            delay *= 2
    raise AssertionError("unreachable")
