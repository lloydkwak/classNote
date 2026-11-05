import httpx
import pytest

from app.llm.base import MAX_RETRIES, QuotaExhausted, with_backoff


class RateLimited(Exception):
    code = 429


class Overloaded(Exception):
    code = 503


class ServerError(Exception):
    code = 500


def test_retries_on_429_until_success():
    calls = []
    waits = []

    def call():
        calls.append(1)
        if len(calls) < 3:
            raise RateLimited()
        return "ok"

    assert with_backoff(call, sleep=waits.append) == "ok"
    assert len(calls) == 3
    assert len(waits) == 2
    assert waits[1] > waits[0]


def test_retries_on_503():
    calls = []

    def call():
        calls.append(1)
        if len(calls) < 2:
            raise Overloaded()
        return "ok"

    assert with_backoff(call, sleep=lambda _: None) == "ok"


def test_retries_on_network_failure():
    calls = []

    def call():
        calls.append(1)
        if len(calls) < 2:
            raise httpx.ConnectError("getaddrinfo failed")
        return "ok"

    assert with_backoff(call, sleep=lambda _: None) == "ok"


class DailyLimit(Exception):
    code = 429
    details = {
        "error": {
            "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "77799s"}
            ]
        }
    }


class ShortLimit(DailyLimit):
    details = {"error": {"details": [{"retryDelay": "30s"}]}}


def test_a_daily_limit_fails_at_once_instead_of_waiting():
    calls = []

    def call():
        calls.append(1)
        raise DailyLimit()

    with pytest.raises(QuotaExhausted):
        with_backoff(call, sleep=lambda _: None)
    assert len(calls) == 1


def test_the_servers_short_delay_is_respected():
    waits = []
    outcomes = [ShortLimit(), "ok"]

    def call():
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert with_backoff(call, sleep=waits.append) == "ok"
    assert waits[0] >= 30


def test_other_errors_are_not_retried():
    calls = []

    def call():
        calls.append(1)
        raise ServerError()

    with pytest.raises(ServerError):
        with_backoff(call, sleep=lambda _: None)
    assert len(calls) == 1


def test_gives_up_after_max_retries():
    calls = []

    def call():
        calls.append(1)
        raise RateLimited()

    with pytest.raises(RateLimited):
        with_backoff(call, sleep=lambda _: None)
    assert len(calls) == MAX_RETRIES + 1
