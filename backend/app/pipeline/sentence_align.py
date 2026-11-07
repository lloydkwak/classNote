"""Sentence-level page alignment (v2 aligner).

Algorithm:
  1. Build sentence windows from SpokenWord timestamps.
  2. Embed sentences (queries) and pages (passages).
  3. Optionally mix in a BM25 lexical channel.
  4. Detect off-topic sentences (max raw cosine below threshold).
  5. max-normalize each row (best score = 1 per sentence).
  6. Sharpen with exponent k (norm^k concentrates mass on the best page).
  7. If confidence_boost: scale each sharpened row by the original max cosine.
  8. Run Viterbi DP with page-ordered costs.
  9. Confidence = gap between best and 2nd-best in the boosted space.

Reuses viterbi() and transition_penalties() from page_align.
"""

import os
from dataclasses import dataclass, replace

import numpy as np

from app.models import AlignedWindow, Alignment, Page, PdfExtraction, Segment, SpokenWord, Window
from app.pipeline.page_align import (
    AlignConfig,
    WindowDecision,
    normalize_rows,
    page_text,
    remove_page_bias,
    similarity_matrix,
    viterbi,
)

_EPS = 1e-9


@dataclass(frozen=True)
class SentenceAlignConfig:
    sharpening_exp: float = 1.0   # tuned on MaViLS: z-score+range-norm already concentrates signal
    confidence_boost: bool = True
    next_page_cost: float = 0.3
    skip_penalty: float = 0.3
    back_cost: float = 1.5
    back_skip_penalty: float = 0.3
    off_topic_min_score: float = 0.08
    bm25_weight: float = 0.0

    @classmethod
    def from_env(cls) -> "SentenceAlignConfig":
        cfg = cls()
        env_map = {
            "sharpening_exp":    "SENT_SHARPENING_EXP",
            "next_page_cost":    "SENT_NEXT_PAGE_COST",
            "skip_penalty":      "SENT_SKIP_PENALTY",
            "back_cost":         "SENT_BACK_COST",
            "back_skip_penalty": "SENT_BACK_SKIP_PENALTY",
            "off_topic_min_score": "SENT_OFF_TOPIC_MIN_SCORE",
            "bm25_weight":       "SENT_BM25_WEIGHT",
        }
        bool_map = {"confidence_boost": "SENT_CONFIDENCE_BOOST"}
        overrides: dict = {}
        for field, name in env_map.items():
            if os.environ.get(name):
                overrides[field] = float(os.environ[name])
        for field, name in bool_map.items():
            if os.environ.get(name):
                overrides[field] = os.environ[name].lower() not in ("0", "false", "no")
        return replace(cfg, **overrides)

    @classmethod
    def from_dict(cls, d: dict) -> "SentenceAlignConfig":
        cfg = cls()
        return replace(cfg, **{k: v for k, v in d.items() if hasattr(cfg, k)})


def _to_align_config(config: SentenceAlignConfig) -> AlignConfig:
    """Map sentence aligner costs to an AlignConfig for reuse in viterbi()."""
    return AlignConfig(
        next_page_penalty=config.next_page_cost,
        skip_penalty_per_page=config.skip_penalty,
        back_penalty=config.back_cost,
        back_penalty_per_page=config.back_skip_penalty,
        off_topic_threshold=-999.0,  # off-topic is handled before DP
    )


# ---------------------------------------------------------------------------
# Sentence windowing
# ---------------------------------------------------------------------------

_SENT_ENDS = frozenset((".", "?", "!"))


def _is_sent_end(w: SpokenWord) -> bool:
    return w.text.rstrip()[-1:] in _SENT_ENDS if w.text.rstrip() else False


def build_sentence_windows(segments: list[Segment]) -> list[Window]:
    """Group SpokenWord tokens into sentence-level Windows.

    If segments have no word-level timestamps (mock STT), each segment becomes
    one window, using its own start/end.
    """
    # Flatten words from all segments
    all_words: list[SpokenWord] = []
    for seg in segments:
        all_words.extend(seg.words)

    if not all_words:
        # Fallback: treat each segment as one window
        return [
            Window(window_id=i, start=seg.start, end=seg.end, text=seg.text)
            for i, seg in enumerate(segments)
        ]

    windows: list[Window] = []
    buf: list[SpokenWord] = []
    for word in all_words:
        buf.append(word)
        if _is_sent_end(word):
            windows.append(_buf_to_window(len(windows), buf))
            buf = []
    if buf:
        windows.append(_buf_to_window(len(windows), buf))
    return windows


def _buf_to_window(window_id: int, words: list[SpokenWord]) -> Window:
    start = words[0].start
    end   = words[-1].end
    text  = " ".join(w.text for w in words)
    # Use midpoint as start (consistent with import_mavils sentence timestamp)
    mid = (start + end) / 2.0
    return Window(window_id=window_id, start=mid, end=mid, text=text)


# ---------------------------------------------------------------------------
# Score processing
# ---------------------------------------------------------------------------

def normalize_max(sim: np.ndarray) -> np.ndarray:
    """Stretch each row to [0,1]: (sim - min) / (max - min + ε).

    Range normalization handles compressed cosines (e.g., E5 all in [0.75, 0.91])
    better than dividing by max, since it exposes the inter-page spread.
    """
    row_min = sim.min(axis=1, keepdims=True)
    row_max = sim.max(axis=1, keepdims=True)
    return (sim - row_min) / (row_max - row_min + _EPS)


def sharpen(norm: np.ndarray, exp: float) -> np.ndarray:
    """Raise to exp to concentrate probability mass on the best page."""
    return norm ** exp


# ---------------------------------------------------------------------------
# BM25 lexical channel
# ---------------------------------------------------------------------------

def bm25_matrix(windows: list[Window], pages: list[Page]) -> np.ndarray:
    """BM25 similarity, windows × pages, normalized to [0,1] per row.

    Returns zeros if rank_bm25 is not installed.
    """
    matrix = np.zeros((len(windows), len(pages)))
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        return matrix

    def tokenize(text: str) -> list[str]:
        import re
        return re.findall(r"[a-zA-Z0-9]+", text.lower())

    page_texts = [tokenize(page_text(p)) for p in pages]
    with_tokens = [i for i, t in enumerate(page_texts) if t]
    if not with_tokens:
        return matrix

    corpus = [page_texts[i] for i in with_tokens]
    bm25 = BM25Okapi(corpus)

    for wi, window in enumerate(windows):
        query = tokenize(window.text)
        if not query:
            continue
        scores = bm25.get_scores(query)
        # Normalize to [0,1]
        s_max = scores.max()
        if s_max > 0:
            scores = scores / s_max
        for local_i, page_i in enumerate(with_tokens):
            matrix[wi, page_i] = scores[local_i]
    return matrix


# ---------------------------------------------------------------------------
# Core alignment
# ---------------------------------------------------------------------------

def align_sentence_similarities(
    sim: np.ndarray, config: SentenceAlignConfig
) -> list[WindowDecision]:
    """Align a sentences × pages similarity matrix using the v2 algorithm.

    Off-topic sentences get page_index=None and do not enter the DP.
    """
    n_windows, n_pages = sim.shape
    if n_windows == 0 or n_pages == 0:
        return [WindowDecision(None, 0, 0.0) for _ in range(n_windows)]

    raw_argmax = sim.argmax(axis=1)
    off_topic = sim.max(axis=1) < config.off_topic_min_score
    on_topic_idx = np.flatnonzero(~off_topic)

    if on_topic_idx.size == 0:
        return [WindowDecision(None, int(raw_argmax[i]), 0.0) for i in range(n_windows)]

    on_sim = sim[on_topic_idx]

    # Z-score normalize (page bias + row centering), same as v1, to handle
    # compressed cosine ranges (e.g. E5 all in [0.75, 0.91]).
    z = normalize_rows(remove_page_bias(on_sim))

    # Shift z-scores to positive then range-normalize to [0,1].
    norm = normalize_max(z)
    sharp = sharpen(norm, config.sharpening_exp)

    if config.confidence_boost:
        # Scale by original max cosine: encodes overall signal strength.
        row_max = on_sim.max(axis=1, keepdims=True)
        boosted = sharp * row_max
    else:
        boosted = sharp

    dp_config = _to_align_config(config)
    path = viterbi(boosted, dp_config)

    page_map: dict[int, int] = dict(zip(on_topic_idx.tolist(), path))

    decisions: list[WindowDecision] = []
    for i in range(n_windows):
        raw = int(raw_argmax[i])
        if off_topic[i]:
            decisions.append(WindowDecision(None, raw, 0.0))
        else:
            chosen = page_map[i]
            # Confidence in boosted space
            row = boosted[np.searchsorted(on_topic_idx, i)]
            others = np.delete(row, chosen)
            gap = float(row[chosen] - (others.max() if others.size > 0 else 0.0))
            conf = float(np.clip(gap, 0.0, 1.0))
            decisions.append(WindowDecision(chosen, raw, conf))
    return decisions


def align_sentence_windows(
    windows: list[Window],
    extraction: PdfExtraction,
    embedder=None,
    bm25_weight: float = 0.0,
    config: SentenceAlignConfig | None = None,
) -> Alignment:
    """Full v2 alignment: embed → BM25 mix → sentence DP → Alignment."""
    from app.pipeline.page_align import get_embedder as _get_embedder

    embedder = embedder or _get_embedder()
    config = config or SentenceAlignConfig.from_env()
    pages = extraction.pages

    sem_sim = similarity_matrix(windows, pages, embedder)
    if bm25_weight > 0.0:
        lex_sim = bm25_matrix(windows, pages)
        sim = (1.0 - bm25_weight) * sem_sim + bm25_weight * lex_sim
    else:
        sim = sem_sim

    numbers = [page.page for page in pages]
    decisions = align_sentence_similarities(sim, config)

    aligned = [
        AlignedWindow(
            **window.model_dump(),
            page=None if d.page_index is None else numbers[d.page_index],
            confidence=d.confidence,
            raw_page=numbers[d.raw_page_index] if numbers else 0,
            similarities=sem_sim[i].tolist(),
        )
        for i, (window, d) in enumerate(zip(windows, decisions))
    ]
    return Alignment(windows=aligned, pages=numbers)
