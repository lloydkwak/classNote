import logging
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

RETRYABLE_STATUS_CODES = {429, 503}
MAX_RETRIES = 8
INITIAL_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 120.0


class LLMProvider(ABC):
    """Callers send requests one at a time, which keeps free-tier use sequential."""

    name: str
    model: str

    @abstractmethod
    def complete_json(self, prompt: str) -> str:
        """Return the model's reply to `prompt` as JSON text."""


def is_retryable_error(error: Exception) -> bool:
    """Recognize rate limiting or overload from any SDK, which name the status differently."""
    status = getattr(error, "status_code", None) or getattr(error, "code", None)
    return status in RETRYABLE_STATUS_CODES


def with_backoff(
    call: Callable[[], T],
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run `call`, waiting and retrying on 429 and 503 errors. Other errors propagate."""
    delay = INITIAL_BACKOFF_SECONDS
    for attempt in range(MAX_RETRIES + 1):
        try:
            return call()
        except Exception as error:
            if not is_retryable_error(error) or attempt == MAX_RETRIES:
                raise
            wait = min(delay, MAX_BACKOFF_SECONDS) + random.uniform(0, 1)
            log.warning("LLM rate limited or overloaded; retrying in %.0fs", wait)
            sleep(wait)
            delay *= 2
    raise AssertionError("unreachable")
