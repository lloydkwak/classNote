import pytest

from app.llm.base import MAX_RETRIES, with_backoff


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
