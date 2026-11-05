import pytest
from eval_alignment import evaluate

from app.models import AlignedWindow, Alignment


def window(index, start, end, page, raw_page):
    return AlignedWindow(
        window_id=index,
        start=start,
        end=end,
        text="",
        page=page,
        raw_page=raw_page,
        confidence=1,
        similarities=[],
    )


def test_reports_accuracy_before_and_after_the_dp():
    alignment = Alignment(
        windows=[
            window(0, 0, 10, page=1, raw_page=1),
            window(1, 10, 20, page=1, raw_page=3),  # DP fixed a noisy window.
            window(2, 20, 30, page=2, raw_page=2),
            window(3, 30, 40, page=None, raw_page=2),  # Off-topic; raw can't say so.
        ],
        pages=[1, 2, 3],
    )
    truth = [
        {"start": 0, "end": 20, "page": 1},
        {"start": 20, "end": 30, "page": 2},
        {"start": 30, "end": 40, "page": None},
    ]
    result = evaluate(alignment, truth)
    assert result["windows"] == 4
    assert result["raw_accuracy"] == pytest.approx(0.5)
    assert result["dp_accuracy"] == pytest.approx(1.0)
    assert result["on_topic_windows"] == 3
    assert result["on_topic_raw_accuracy"] == pytest.approx(2 / 3)


def test_unlabeled_windows_are_ignored():
    alignment = Alignment(windows=[window(0, 100, 110, page=1, raw_page=1)], pages=[1])
    assert evaluate(alignment, [{"start": 0, "end": 10, "page": 1}])["windows"] == 0
