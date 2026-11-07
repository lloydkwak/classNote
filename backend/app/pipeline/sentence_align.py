"""Sentence-level page alignment (v2 aligner).

Algorithm:
  1. Build sentence windows from SpokenWord timestamps.
  2. Embed sentences (queries) and pages (passages).
  3. Optionally mix in a BM25 lexical channel.
  4. Detect off-topic sentences (max raw cosine below threshold).
  5. max-normalize each row (best score = 1 per sentence).
  6. Sharpen with exponent k (norm^k concentrates mass on the best page).
  7. If confidence_boost: scale each sharpened row by the original max cosine.
  8. Apply slide-turn cue boosts (optional): add a direction ramp at cue positions.
  9. Run Viterbi DP with page-ordered costs.
  10. Confidence = gap between best and 2nd-best in the boosted space.

Reuses viterbi() and transition_penalties() from page_align.
"""

import logging
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

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
    cue_boost: float = 0.3  # magnitude of page-direction ramp added at slide-turn cue sentences
    logistics_detection: bool = True  # mark scheduling/announcement sentences as off-topic
    arbitration_threshold: float = 0.0  # 0 = disabled; >0 triggers LLM for low-conf stretches
    arbitration_max_calls: int = 5      # maximum LLM calls per alignment run

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
            "cue_boost":             "SENT_CUE_BOOST",
            "arbitration_threshold": "SENT_ARBITRATION_THRESHOLD",
            "arbitration_max_calls": "SENT_ARBITRATION_MAX_CALLS",
        }
        bool_map = {"confidence_boost": "SENT_CONFIDENCE_BOOST", "logistics_detection": "SENT_LOGISTICS_DETECTION"}
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

    If segments have no word-level timestamps (mock STT or OpenAI provider),
    each segment becomes one window using its own start/end. This is a coarser
    fallback; re-transcribe with faster-whisper to get true sentence windows.
    """
    # Flatten words from all segments
    all_words: list[SpokenWord] = []
    for seg in segments:
        all_words.extend(seg.words)

    if not all_words:
        log.warning(
            "v2 aligner: no word-level timestamps found in %d segment(s). "
            "Falling back to one window per segment (coarser alignment). "
            "Re-run the transcribe step with faster-whisper to get word timestamps.",
            len(segments),
        )
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
    return Window(window_id=window_id, start=start, end=end, text=text)


# ---------------------------------------------------------------------------
# Slide-turn cue detection
# ---------------------------------------------------------------------------

# Phrases that signal the professor is commanding the next slide to appear.
# Keep these conservative — conversational phrases like "going back to", "as we saw",
# "if you recall" are NOT slide commands and produce false positives.
_NEXT_SLIDE_CUES: list[str] = [
    # Korean — explicit advance commands
    "다음 장으로", "다음 슬라이드로", "다음 페이지로",
    "다음 장을 보", "다음 슬라이드를 보",
    "넘어가겠습니다", "넘어가 보겠습니다", "넘어갑니다",
    # English — imperative slide advance ("next slide please", "okay next slide")
    "next slide",
    "go to the next slide", "advance the slide", "click to the next slide",
]

# Phrases that signal a command to go back to an earlier slide.
_BACK_CUES: list[str] = [
    # Korean
    "앞 슬라이드로", "이전 슬라이드로", "앞 장으로", "한 장 앞으로",
    # English — imperative back navigation
    "go back to the slide", "back to the previous slide",
    "can you go back", "can we go back", "previous slide",
]


def detect_slide_cues(windows: list["Window"]) -> tuple[set[int], set[int]]:
    """Return (next_indices, back_indices) of windows containing slide-turn phrases."""
    next_set: set[int] = set()
    back_set: set[int] = set()
    for i, w in enumerate(windows):
        lower = w.text.lower()
        if any(cue in lower for cue in _NEXT_SLIDE_CUES):
            next_set.add(i)
        elif any(cue in lower for cue in _BACK_CUES):
            back_set.add(i)
    return next_set, back_set


def _apply_cue_boosts(
    boosted: np.ndarray,
    on_topic_idx: np.ndarray,
    next_cue_idxs: set[int],
    back_cue_idxs: set[int],
    boost: float,
) -> np.ndarray:
    """Add a linear page-direction ramp at cue rows in the on-topic boosted matrix.

    'Next slide' cues get a ramp that increases toward later pages; 'back' cues
    get the reverse.  The ramp biases the DP to advance or retreat at that sentence
    without overriding the semantic signal — it only tips close decisions.
    """
    n_pages = boosted.shape[1]
    if n_pages < 2 or boost <= 0 or (not next_cue_idxs and not back_cue_idxs):
        return boosted
    idx_to_row = {int(orig): row for row, orig in enumerate(on_topic_idx)}
    ramp_fwd = np.linspace(0.0, boost, n_pages)
    ramp_bck = ramp_fwd[::-1]
    result = boosted.copy()
    for orig_i in next_cue_idxs:
        row = idx_to_row.get(orig_i)
        if row is not None:
            result[row] += ramp_fwd
    for orig_i in back_cue_idxs:
        row = idx_to_row.get(orig_i)
        if row is not None:
            result[row] += ramp_bck
    return result


# ---------------------------------------------------------------------------
# Off-topic detection: logistics / announcements
# ---------------------------------------------------------------------------

# Patterns for administrative announcements that should not be aligned to any slide.
# Kept intentionally narrow to avoid classifying lecture content as logistics.
_LOGISTICS_PATTERNS = [
    # Korean: scheduling announcements
    re.compile(
        r"(다음\s*주|이번\s*주|이번\s*달|오늘\s*까지).{0,30}(과제|숙제|시험|퀴즈|제출|마감|기한)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(과제|숙제|시험|퀴즈).{0,30}(마감|제출|기한|다음\s*주|이번\s*주|오늘\s*까지)",
        re.IGNORECASE,
    ),
    re.compile(r"(출석\s*체크|출석\s*확인|조교님?\s*(이메일|연락|질문))", re.IGNORECASE),
    # English: administrative scheduling
    re.compile(
        r"\b(homework|assignment|quiz)\b.{0,40}\b(due|submit|deadline|next\s+week|this\s+week)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(due|deadline|office\s+hours)\b.{0,40}\b(tomorrow|today|next\s+week|this\s+week)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(please\s+submit|don't\s+forget\s+to\s+submit|reminder\s*:)\b", re.IGNORECASE),
]


def _is_logistics(text: str) -> bool:
    return any(p.search(text) for p in _LOGISTICS_PATTERNS)


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
    sim: np.ndarray,
    config: SentenceAlignConfig,
    windows: list | None = None,
) -> list[WindowDecision]:
    """Align a sentences × pages similarity matrix using the v2 algorithm.

    Off-topic sentences get page_index=None and do not enter the DP.
    Pass `windows` to enable slide-turn cue detection (cue_boost > 0).
    """
    n_windows, n_pages = sim.shape
    if n_windows == 0 or n_pages == 0:
        return [WindowDecision(None, 0, 0.0) for _ in range(n_windows)]

    raw_argmax = sim.argmax(axis=1)
    off_topic = sim.max(axis=1) < config.off_topic_min_score
    if config.logistics_detection and windows is not None:
        for i, w in enumerate(windows):
            if _is_logistics(w.text):
                off_topic[i] = True
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

    # Slide-turn cue boosts: bias DP toward advancement at "next slide" phrases.
    if config.cue_boost > 0 and windows is not None:
        next_cues, back_cues = detect_slide_cues(windows)
        if next_cues or back_cues:
            boosted = _apply_cue_boosts(boosted, on_topic_idx, next_cues, back_cues, config.cue_boost)

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
    *,
    llm=None,
    pdf_path: Path | None = None,
) -> Alignment:
    """Full v2 alignment: embed → BM25 mix → sentence DP → LLM arbitration → Alignment."""
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
    decisions = align_sentence_similarities(sim, config, windows)

    if config.arbitration_threshold > 0 and llm is not None:
        from app.pipeline.llm_arbitration import arbitrate
        decisions, n_calls = arbitrate(
            windows, pages, numbers, decisions,
            pdf_path=pdf_path, llm=llm,
            threshold=config.arbitration_threshold,
            max_calls=config.arbitration_max_calls,
        )
        if n_calls:
            log.info("LLM arbitration: %d call(s) made", n_calls)

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
