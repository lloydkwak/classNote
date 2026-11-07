"""Tests for the v2 sentence-level aligner (app/pipeline/sentence_align.py)."""

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Block, Page, PdfExtraction, Segment, SpokenWord
from app.pipeline.sentence_align import (
    SentenceAlignConfig,
    align_sentence_similarities,
    align_sentence_windows,
    bm25_matrix,
    build_sentence_windows,
    normalize_max,
    sharpen,
)
from app.pipeline.page_align import MockEmbedder


# ---------------------------------------------------------------------------
# build_sentence_windows
# ---------------------------------------------------------------------------

def _make_words(*texts: str) -> list[SpokenWord]:
    words = []
    t = 0.0
    for text in texts:
        words.append(SpokenWord(start=t, end=t + 0.5, text=text, probability=1.0))
        t += 0.5
    return words


def _seg(text: str, words: list[SpokenWord]) -> Segment:
    return Segment(start=words[0].start, end=words[-1].end, text=text, words=words)


def test_build_sentence_windows_splits_on_period():
    words = _make_words("Hello", "world.", "How", "are", "you?")
    segs = [_seg("Hello world. How are you?", words)]
    windows = build_sentence_windows(segs)
    assert len(windows) == 2
    assert "Hello world." in windows[0].text
    assert "How are you?" in windows[1].text


def test_build_sentence_windows_splits_on_exclamation():
    words = _make_words("Stop!", "Now.")
    segs = [_seg("Stop! Now.", words)]
    windows = build_sentence_windows(segs)
    assert len(windows) == 2


def test_build_sentence_windows_no_terminal_punct_gives_one_window():
    words = _make_words("incomplete", "sentence")
    segs = [_seg("incomplete sentence", words)]
    windows = build_sentence_windows(segs)
    assert len(windows) == 1


def test_build_sentence_windows_fallback_no_words():
    segs = [
        Segment(start=0.0, end=1.0, text="First sentence.", words=[]),
        Segment(start=1.0, end=2.0, text="Second sentence.", words=[]),
    ]
    windows = build_sentence_windows(segs)
    assert len(windows) == 2
    assert windows[0].text == "First sentence."


def test_build_sentence_windows_empty():
    assert build_sentence_windows([]) == []


# ---------------------------------------------------------------------------
# normalize_max
# ---------------------------------------------------------------------------

def test_normalize_max_range_stretch():
    # Range normalization: min→0, max→1
    sim = np.array([[0.3, 0.6, 0.9], [0.1, 0.5, 0.2]])
    norm = normalize_max(sim)
    np.testing.assert_allclose(norm.max(axis=1), [1.0, 1.0], atol=1e-6)
    np.testing.assert_allclose(norm.min(axis=1), [0.0, 0.0], atol=1e-6)


def test_normalize_max_zero_range_no_nan():
    sim = np.array([[0.8, 0.8, 0.8]])
    norm = normalize_max(sim)
    # all-same row: range = 0, so result is 0 (eps guard avoids nan)
    assert not np.any(np.isnan(norm))


# ---------------------------------------------------------------------------
# sharpen
# ---------------------------------------------------------------------------

def test_sharpen_exponent_one_unchanged():
    arr = np.array([[0.5, 1.0, 0.3]])
    np.testing.assert_allclose(sharpen(arr, 1.0), arr)


def test_sharpen_increases_contrast():
    arr = np.array([[0.5, 1.0]])
    sharpened = sharpen(arr, 3.0)
    # 0.5^3 = 0.125, 1.0^3 = 1.0 — ratio increased
    assert sharpened[0, 0] < arr[0, 0]
    assert sharpened[0, 1] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Off-topic detection
# ---------------------------------------------------------------------------

def test_off_topic_flag_below_threshold():
    # Row 0: max = 0.05 < 0.08 → off-topic
    # Row 1: max = 0.5 → on-topic
    sim = np.array([[0.05, 0.03], [0.5, 0.2]])
    config = SentenceAlignConfig(off_topic_min_score=0.08)
    decisions = align_sentence_similarities(sim, config)
    assert decisions[0].page_index is None
    assert decisions[1].page_index is not None


# ---------------------------------------------------------------------------
# align_sentence_similarities — basic correctness
# ---------------------------------------------------------------------------

def test_align_3x3_clear_diagonal():
    # Clear diagonal → should assign page 0,1,2
    sim = np.array([
        [0.9, 0.1, 0.1],
        [0.1, 0.9, 0.1],
        [0.1, 0.1, 0.9],
    ])
    config = SentenceAlignConfig(off_topic_min_score=0.0)
    decisions = align_sentence_similarities(sim, config)
    assert [d.page_index for d in decisions] == [0, 1, 2]


def test_align_empty_matrix():
    sim = np.zeros((0, 3))
    decisions = align_sentence_similarities(sim, SentenceAlignConfig())
    assert decisions == []


def test_align_all_off_topic():
    sim = np.array([[0.01, 0.01], [0.02, 0.01]])
    config = SentenceAlignConfig(off_topic_min_score=0.1)
    decisions = align_sentence_similarities(sim, config)
    assert all(d.page_index is None for d in decisions)


# ---------------------------------------------------------------------------
# bm25_matrix
# ---------------------------------------------------------------------------

def _make_page(text: str, page_num: int = 1) -> Page:
    return Page(page=page_num, title="", width=100.0, height=100.0, blocks=[
        Block(block_id="b1", page=page_num, text=text, bbox=[0, 0, 1, 1])
    ])


def test_bm25_matrix_returns_zeros_or_valid():
    from app.models import Window
    windows = [Window(window_id=0, start=0.0, end=1.0, text="cosine similarity example")]
    pages = [_make_page("cosine similarity"), _make_page("quantum mechanics")]
    result = bm25_matrix(windows, pages)
    assert result.shape == (1, 2)
    assert not np.any(np.isnan(result))
    # Regardless of BM25 availability, scores must be in [0,1]
    assert result.min() >= 0.0
    assert result.max() <= 1.0 + 1e-6


def test_bm25_matrix_shape():
    from app.models import Window
    windows = [Window(window_id=i, start=float(i), end=float(i)+1, text=f"word{i}") for i in range(5)]
    pages = [_make_page(f"page text {j}") for j in range(3)]
    mat = bm25_matrix(windows, pages)
    assert mat.shape == (5, 3)


# ---------------------------------------------------------------------------
# SentenceAlignConfig.from_env / from_dict
# ---------------------------------------------------------------------------

def test_from_env_reads_variables(monkeypatch):
    monkeypatch.setenv("SENT_SHARPENING_EXP", "5.0")
    monkeypatch.setenv("SENT_NEXT_PAGE_COST", "0.1")
    cfg = SentenceAlignConfig.from_env()
    assert cfg.sharpening_exp == pytest.approx(5.0)
    assert cfg.next_page_cost == pytest.approx(0.1)


def test_from_dict():
    cfg = SentenceAlignConfig.from_dict({"sharpening_exp": 2.0, "bm25_weight": 0.3})
    assert cfg.sharpening_exp == pytest.approx(2.0)
    assert cfg.bm25_weight == pytest.approx(0.3)


def test_from_dict_ignores_unknown():
    cfg = SentenceAlignConfig.from_dict({"unknown_field": 999})
    assert cfg == SentenceAlignConfig()


# ---------------------------------------------------------------------------
# End-to-end: align_sentence_windows with mock embedder
# ---------------------------------------------------------------------------

def _make_extraction(page_texts: list[str]) -> PdfExtraction:
    pages = []
    for i, text in enumerate(page_texts, 1):
        pages.append(Page(
            page=i, title="", width=100.0, height=100.0,
            blocks=[Block(block_id=f"b{i}", page=i, text=text, bbox=[0, 0, 1, 1])]
        ))
    return PdfExtraction(pages=pages, key_terms=[])


def _segs_from_texts(texts: list[str]) -> list[Segment]:
    segs = []
    for i, text in enumerate(texts):
        segs.append(Segment(start=float(i), end=float(i) + 1.0, text=text, words=[]))
    return segs


def test_end_to_end_returns_alignment():
    from app.models import Window

    words1 = _make_words("Definition", "of", "cosine.")
    words2 = _make_words("Quantum", "mechanics", "intro.")
    segs = [
        _seg("Definition of cosine.", words1),
        _seg("Quantum mechanics intro.", words2),
    ]
    extraction = _make_extraction(["cosine similarity math", "quantum physics"])
    embedder = MockEmbedder()

    windows = build_sentence_windows(segs)
    alignment = align_sentence_windows(
        windows, extraction, embedder=embedder, config=SentenceAlignConfig(off_topic_min_score=0.0)
    )
    assert len(alignment.windows) == 2
    assert alignment.pages == [1, 2]
    for w in alignment.windows:
        assert w.page is None or w.page in alignment.pages
