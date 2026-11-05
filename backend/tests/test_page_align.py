"""Page alignment tests with synthetic similarity matrices: no API or model needed."""

import numpy as np
import pytest

from app.models import Block, Page, PdfExtraction, Window
from app.pipeline.page_align import (
    AlignConfig,
    MockEmbedder,
    align_similarities,
    align_windows,
    similarity_matrix,
    transition_penalties,
    viterbi,
)

CONFIG = AlignConfig(
    next_page_penalty=0.01,
    skip_penalty_per_page=0.02,
    back_penalty=0.1,
    off_topic_threshold=0.5,
)


def matrix_for(pages, page_count, high=0.9, low=0.6):
    """One row per window, with `high` on the given page index."""
    matrix = np.full((len(pages), page_count), low)
    for row, page in enumerate(pages):
        matrix[row, page] = high
    return matrix


def pages_of(decisions):
    return [decision.page_index for decision in decisions]


def test_moves_forward_through_the_pages():
    decisions = align_similarities(matrix_for([0, 0, 1, 1, 2, 3], 4), CONFIG)
    assert pages_of(decisions) == [0, 0, 1, 1, 2, 3]


def test_goes_back_when_the_evidence_is_sustained():
    expected = [0, 1, 2, 2, 0, 0, 0, 3, 3]
    assert pages_of(align_similarities(matrix_for(expected, 4), CONFIG)) == expected


def test_one_noisy_window_does_not_cause_a_jump_backward():
    """The reason for the DP: raw argmax would jump to page 0 and straight back."""
    matrix = matrix_for([2, 2, 0, 2, 2], 4, high=0.85, low=0.8)
    decisions = align_similarities(matrix, CONFIG)
    assert pages_of(decisions) == [2, 2, 2, 2, 2]
    assert [decision.raw_page_index for decision in decisions] == [2, 2, 0, 2, 2]


def test_one_noisy_window_does_not_cause_a_jump_forward():
    matrix = matrix_for([0, 0, 7, 1, 1], 8, high=0.85, low=0.8)
    pages = pages_of(align_similarities(matrix, CONFIG))
    assert 7 not in pages
    assert pages[:2] == [0, 0]
    assert pages[-2:] == [1, 1]


def test_skipping_pages_is_allowed_with_strong_evidence():
    assert pages_of(align_similarities(matrix_for([0, 0, 5, 5, 5], 6), CONFIG)) == [0, 0, 5, 5, 5]


def test_off_topic_windows_get_no_page():
    matrix = matrix_for([0, 1, 1, 2], 3)
    matrix[1:3] = 0.3
    decisions = align_similarities(matrix, CONFIG)
    assert pages_of(decisions) == [0, None, None, 2]
    assert decisions[1].confidence == 0.0


def test_off_topic_span_does_not_break_the_page_order():
    """After chatter, a noisy window must still be judged against the page before it."""
    matrix = matrix_for([2, 2, 0, 0, 0, 2], 3, high=0.85, low=0.8)
    matrix[2:4] = 0.3
    assert pages_of(align_similarities(matrix, CONFIG)) == [2, 2, None, None, 2, 2]


def test_everything_off_topic():
    decisions = align_similarities(np.full((3, 2), 0.1), CONFIG)
    assert pages_of(decisions) == [None, None, None]


def test_threshold_applies_to_the_best_page_only():
    matrix = np.array([[0.1, 0.1, 0.51]])
    assert pages_of(align_similarities(matrix, CONFIG)) == [2]


def test_confidence_follows_the_gap_between_best_and_second_best():
    clear = align_similarities(np.array([[0.9, 0.6]]), CONFIG)[0]
    close = align_similarities(np.array([[0.72, 0.7]]), CONFIG)[0]
    assert clear.confidence == 1.0
    assert close.confidence == pytest.approx(0.2)


def test_confidence_is_zero_when_the_dp_overrides_the_best_page():
    decisions = align_similarities(matrix_for([2, 2, 0, 2, 2], 4, high=0.85, low=0.8), CONFIG)
    assert decisions[2].page_index == 2
    assert decisions[2].confidence == 0.0


def test_penalty_shape():
    penalties = transition_penalties(4, CONFIG)
    assert penalties[1, 1] == 0
    assert penalties[1, 2] == pytest.approx(0.01)
    assert penalties[0, 3] == pytest.approx(0.05)
    assert penalties[3, 0] == pytest.approx(0.1)
    assert penalties[3, 2] == pytest.approx(0.1)


def test_penalties_are_configurable():
    matrix = matrix_for([2, 2, 0, 2, 2], 3, high=0.85, low=0.8)
    assert viterbi(matrix, CONFIG) == [2, 2, 2, 2, 2]
    free_moves = AlignConfig(back_penalty=0, next_page_penalty=0, skip_penalty_per_page=0)
    assert viterbi(matrix, free_moves) == [2, 2, 0, 2, 2]


def test_penalties_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("ALIGN_BACK_PENALTY", "0.5")
    monkeypatch.setenv("ALIGN_OFF_TOPIC_THRESHOLD", "0.2")
    config = AlignConfig.from_env(default_threshold=0.9)
    assert config.back_penalty == 0.5
    assert config.off_topic_threshold == 0.2


def test_empty_inputs():
    assert align_similarities(np.zeros((0, 3)), CONFIG) == []
    assert pages_of(align_similarities(np.zeros((2, 0)), CONFIG)) == [None, None]


def test_e5_prefixes_are_used():
    from app.pipeline.page_align import E5Embedder

    seen = []

    class FakeModel:
        def encode(self, texts, **_):
            seen.extend(texts)
            return np.zeros((len(texts), 2))

    embedder = E5Embedder.__new__(E5Embedder)
    embedder._model = FakeModel()
    embedder.embed_queries(["q"])
    embedder.embed_passages(["p"])
    assert seen == ["query: q", "passage: p"]


def make_page(number, *texts):
    blocks = [
        Block(block_id=f"p{number}_b{index}", page=number, text=text, bbox=(0, 0, 1, 1))
        for index, text in enumerate(texts)
    ]
    return Page(page=number, title=texts[0] if texts else "", blocks=blocks)


def test_page_with_no_text_scores_zero():
    windows = [Window(window_id=0, start=0, end=1, text="recursion base case")]
    matrix = similarity_matrix(
        windows, [make_page(1, "recursion base case"), make_page(2)], MockEmbedder()
    )
    assert matrix[0, 0] > 0.9
    assert matrix[0, 1] == 0


def test_align_windows_uses_real_page_numbers_and_records_debug_data():
    extraction = PdfExtraction(
        pages=[make_page(1, "recursion base case"), make_page(2, "merge sort array")],
        key_terms=[],
    )
    windows = [
        Window(window_id=0, start=0, end=30, text="recursion의 base case"),
        Window(window_id=1, start=30, end=60, text="merge sort splits the array"),
        Window(window_id=2, start=60, end=90, text="zzz qqq"),
    ]
    result = align_windows(windows, extraction, MockEmbedder())
    assert result.pages == [1, 2]
    assert [window.page for window in result.windows] == [1, 2, None]
    assert result.windows[0].raw_page == 1
    assert len(result.windows[0].similarities) == 2
