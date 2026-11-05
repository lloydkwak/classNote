"""Page alignment tests with synthetic similarity matrices: no API or model needed."""

import numpy as np
import pytest

from app.models import Block, Page, PdfExtraction, Window
from app.pipeline.page_align import (
    AlignConfig,
    MockEmbedder,
    align_similarities,
    align_windows,
    normalize_rows,
    similarity_matrix,
    transition_penalties,
    viterbi,
)

CONFIG = AlignConfig()

# Shaped like real e5 output: every page scores about 0.8, with small margins.
E5_LIKE = np.array(
    [
        [0.861, 0.805, 0.803, 0.795, 0.819],
        [0.805, 0.889, 0.801, 0.800, 0.815],
        [0.815, 0.799, 0.845, 0.807, 0.811],
        [0.872, 0.806, 0.804, 0.790, 0.818],
        [0.786, 0.784, 0.810, 0.880, 0.826],
        [0.799, 0.788, 0.812, 0.871, 0.824],
        [0.797, 0.808, 0.809, 0.825, 0.867],
    ]
)
E5_LIKE_CHATTER = np.array([[0.74, 0.72, 0.73, 0.71, 0.73], [0.76, 0.74, 0.75, 0.73, 0.75]])


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


def noisy_matrix():
    """Window 2 slightly prefers page 0, while page 2 stays nearly as good."""
    matrix = matrix_for([2, 2, 2, 2, 2], 4, high=0.85, low=0.8)
    matrix[2] = [0.85, 0.8, 0.84, 0.8]
    return matrix


def test_one_noisy_window_does_not_cause_a_jump_backward():
    """The reason for the DP: raw argmax would jump to page 0 and straight back."""
    decisions = align_similarities(noisy_matrix(), CONFIG)
    assert pages_of(decisions) == [2, 2, 2, 2, 2]
    assert [decision.raw_page_index for decision in decisions] == [2, 2, 0, 2, 2]


def test_one_noisy_window_does_not_cause_a_jump_forward():
    matrix = matrix_for([0, 0, 0, 1, 1], 8, high=0.85, low=0.8)
    matrix[2, 7] = 0.86  # Slightly beats the current page, far ahead in the deck.
    pages = pages_of(align_similarities(matrix, CONFIG))
    assert 7 not in pages
    assert pages[:2] == [0, 0]
    assert pages[-2:] == [1, 1]


def test_skipping_pages_is_allowed_with_strong_evidence():
    assert pages_of(align_similarities(matrix_for([0, 0, 5, 5, 5], 6), CONFIG)) == [0, 0, 5, 5, 5]


def test_off_topic_windows_get_no_page():
    matrix = matrix_for([0, 0, 1, 1, 1, 2, 2, 2], 3)
    matrix[3:5] = [0.3, 0.2, 0.25]
    decisions = align_similarities(matrix, CONFIG)
    assert pages_of(decisions) == [0, 0, 1, None, None, 2, 2, 2]
    assert decisions[3].confidence == 0.0


def test_compressed_e5_similarities_follow_the_lecture_including_going_back():
    """Margins of a few hundredths must still beat the penalties, once normalized."""
    assert pages_of(align_similarities(E5_LIKE, CONFIG)) == [0, 1, 2, 0, 3, 3, 4]


def test_off_topic_fires_on_compressed_e5_similarities():
    """Chatter here scores about 0.75, not far below the 0.85 of on-topic windows."""
    matrix = np.vstack([E5_LIKE[:4], E5_LIKE_CHATTER, E5_LIKE[4:]])
    decisions = align_similarities(matrix, CONFIG)
    assert pages_of(decisions) == [0, 1, 2, 0, None, None, 3, 3, 4]


def test_normalize_rows_gives_mean_zero_and_unit_variance():
    normalized = normalize_rows(E5_LIKE)
    assert normalized.mean(axis=1) == pytest.approx(0)
    assert normalized.std(axis=1) == pytest.approx(1)


def test_page_that_attracts_every_window_does_not_win_everything():
    """Like one Korean slide in an English deck: high for all Korean speech."""
    true_pages = [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2]
    matrix = matrix_for(true_pages, 4, high=0.84, low=0.8)
    matrix[:, 3] = 0.86  # Beats the true page in every window, so raw argmax is always 3.
    assert {decision.raw_page_index for decision in align_similarities(matrix, CONFIG)} == {3}
    assert pages_of(align_similarities(matrix, CONFIG)) == true_pages


def test_normalize_rows_handles_a_flat_row():
    assert normalize_rows(np.array([[0.5, 0.5]])).tolist() == [[0.0, 0.0]]


def test_off_topic_span_does_not_break_the_page_order():
    """After chatter, a noisy window must still be judged against the page before it."""
    matrix = matrix_for([2, 2, 2, 2, 0, 0, 0, 2, 2], 3, high=0.85, low=0.8)
    matrix[4:6] = [0.3, 0.2, 0.25]
    matrix[6] = [0.85, 0.8, 0.84]  # Barely favors page 0, so going back isn't worth it.
    pages = pages_of(align_similarities(matrix, CONFIG))
    assert pages == [2, 2, 2, 2, None, None, 2, 2, 2]


def test_everything_off_topic():
    decisions = align_similarities(np.full((3, 2), 0.1), CONFIG)
    assert pages_of(decisions) == [None, None, None]


def test_off_topic_threshold_is_configurable():
    matrix = np.vstack([E5_LIKE, E5_LIKE_CHATTER])
    assert pages_of(align_similarities(matrix, CONFIG))[-2:] == [None, None]
    never = AlignConfig(off_topic_threshold=-1000)
    assert None not in pages_of(align_similarities(matrix, never))


def test_confidence_follows_the_gap_between_best_and_second_best():
    matrix = np.array([[0.9, 0.6, 0.6, 0.6], [0.70, 0.68, 0.50, 0.50]])
    clear, close = align_similarities(matrix, AlignConfig(off_topic_threshold=-1000))
    assert clear.confidence == 1.0
    assert close.confidence == pytest.approx(0.2, abs=0.05)


def test_confidence_is_zero_when_the_dp_overrides_the_best_page():
    decisions = align_similarities(noisy_matrix(), CONFIG)
    assert decisions[2].page_index == 2
    assert decisions[2].confidence == 0.0


def test_penalty_shape():
    penalties = transition_penalties(4, CONFIG)
    assert penalties[1, 1] == 0
    assert penalties[1, 2] == pytest.approx(0.2)
    assert penalties[0, 3] == pytest.approx(1.0)
    assert penalties[3, 0] == pytest.approx(1.0)
    assert penalties[3, 2] == pytest.approx(1.0)


def test_penalties_are_configurable():
    matrix = matrix_for([2, 2, 0, 2, 2], 3, high=0.5, low=0.0)
    assert viterbi(matrix, CONFIG) == [2, 2, 2, 2, 2]
    free_moves = AlignConfig(back_penalty=0, next_page_penalty=0, skip_penalty_per_page=0)
    assert viterbi(matrix, free_moves) == [2, 2, 0, 2, 2]


def test_penalties_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("ALIGN_BACK_PENALTY", "0.5")
    monkeypatch.setenv("ALIGN_OFF_TOPIC_THRESHOLD", "0.2")
    config = AlignConfig.from_env()
    assert config.back_penalty == 0.5
    assert config.off_topic_threshold == 0.2
    assert config.next_page_penalty == AlignConfig().next_page_penalty


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


def test_raw_similarities_are_kept_for_the_debug_view():
    extraction = PdfExtraction(
        pages=[make_page(1, "recursion"), make_page(2, "merge")], key_terms=[]
    )
    windows = [Window(window_id=0, start=0, end=1, text="recursion and merge recursion")]
    result = align_windows(windows, extraction, MockEmbedder())
    expected = similarity_matrix(windows, extraction.pages, MockEmbedder())[0].tolist()
    assert result.windows[0].similarities == pytest.approx(expected)


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
