"""Decide which page each window explains, using an order-constrained DP (Viterbi)."""

import hashlib
import os
import re
from dataclasses import dataclass, replace

import numpy as np

from app.config import get_settings
from app.models import AlignedWindow, Alignment, Page, PdfExtraction, Window

# A z-score gap this large between the best and second-best page is full confidence.
FULL_CONFIDENCE_GAP = 1.0
# Stops a near-constant best similarity from making tiny differences look like outliers.
MIN_BEST_SCORE_SCALE = 0.005
MAD_TO_STD = 1.4826
MIN_WINDOWS_FOR_PAGE_BIAS = 10


@dataclass(frozen=True)
class AlignConfig:
    """Scores for the DP are per-window z-scores, because e5 cosines are compressed.

    Penalties are in those z-score units. `off_topic_threshold` is how many robust
    standard deviations a window's best similarity may fall below the lecture's
    typical best similarity (see `off_topic_mask`).
    """

    next_page_penalty: float = 0.2
    skip_penalty_per_page: float = 0.4
    back_penalty: float = 1.0
    off_topic_threshold: float = -2.5

    @classmethod
    def from_env(cls) -> "AlignConfig":
        config = cls()
        names = {
            "next_page_penalty": "ALIGN_NEXT_PAGE_PENALTY",
            "skip_penalty_per_page": "ALIGN_SKIP_PENALTY_PER_PAGE",
            "back_penalty": "ALIGN_BACK_PENALTY",
            "off_topic_threshold": "ALIGN_OFF_TOPIC_THRESHOLD",
        }
        overrides = {
            field: float(os.environ[name]) for field, name in names.items() if os.environ.get(name)
        }
        return replace(config, **overrides)


def transition_penalties(page_count: int, config: AlignConfig) -> np.ndarray:
    """Return a matrix where [from_index, to_index] is the cost of that page move."""
    penalties = np.zeros((page_count, page_count))
    for source in range(page_count):
        for target in range(page_count):
            distance = target - source
            if distance < 0:
                penalties[source, target] = config.back_penalty
            elif distance >= 1:
                penalties[source, target] = (
                    config.next_page_penalty + (distance - 1) * config.skip_penalty_per_page
                )
    return penalties


def normalize_rows(similarities: np.ndarray) -> np.ndarray:
    """Z-score each window's row. A row with no variation becomes all zeros."""
    means = similarities.mean(axis=1, keepdims=True)
    stds = similarities.std(axis=1, keepdims=True)
    return np.divide(
        similarities - means, stds, out=np.zeros_like(similarities), where=stds > 1e-9
    )


def remove_page_bias(similarities: np.ndarray) -> np.ndarray:
    """Subtract each page's mean over the lecture, when there are enough windows to tell.

    Some pages score high for every window. For example, the only Korean text on an
    English deck attracts all Korean speech because of the shared language, not the
    topic. Per-window z-scores can't remove that, because it affects every row alike.
    """
    if similarities.shape[0] < MIN_WINDOWS_FOR_PAGE_BIAS:
        return similarities
    return similarities - similarities.mean(axis=0, keepdims=True)


def off_topic_mask(similarities: np.ndarray, config: AlignConfig) -> np.ndarray:
    """Flag windows whose best page scores unusually low for this lecture.

    This compares each window's best similarity with the other windows' best
    similarities, using the median and MAD so the chatter itself doesn't shift the
    baseline. A row's own z-score can't do this: its peak depends on the page count,
    and measured chatter and lecture speech overlap on it. It assumes that most of the
    lecture is on topic. A window that matches no page differently at all is off-topic.
    """
    best = similarities.max(axis=1)
    median = np.median(best)
    scale = max(MAD_TO_STD * np.median(np.abs(best - median)), MIN_BEST_SCORE_SCALE)
    flat = similarities.std(axis=1) <= 1e-9
    return flat | ((best - median) / scale < config.off_topic_threshold)


def viterbi(scores_by_window: np.ndarray, config: AlignConfig) -> list[int]:
    """Return the page index per row that maximizes score minus move penalties."""
    window_count, page_count = scores_by_window.shape
    if window_count == 0:
        return []
    penalties = transition_penalties(page_count, config)
    scores = scores_by_window[0].copy()
    previous = np.zeros((window_count, page_count), dtype=int)
    for row in range(1, window_count):
        candidates = scores[:, None] - penalties
        previous[row] = candidates.argmax(axis=0)
        scores = candidates.max(axis=0) + scores_by_window[row]
    path = [int(scores.argmax())]
    for row in range(window_count - 1, 0, -1):
        path.append(int(previous[row, path[-1]]))
    path.reverse()
    return path


def _confidence(row: np.ndarray, chosen: int) -> float:
    """Scale the chosen page's lead over the best other page to 0-1."""
    if len(row) == 1:
        return 1.0
    others = np.delete(row, chosen)
    return float(np.clip((row[chosen] - others.max()) / FULL_CONFIDENCE_GAP, 0.0, 1.0))


@dataclass(frozen=True)
class WindowDecision:
    page_index: int | None  # None means off-topic
    raw_page_index: int
    confidence: float


def align_similarities(similarities: np.ndarray, config: AlignConfig) -> list[WindowDecision]:
    """Align a windows x pages matrix.

    Off-topic windows are left out of the DP, so chatter neither moves the current
    page nor costs anything: the next on-topic window continues from where it left off.
    """
    if similarities.shape[0] == 0 or similarities.shape[1] == 0:
        return [WindowDecision(None, 0, 0.0) for _ in range(similarities.shape[0])]
    normalized = normalize_rows(remove_page_bias(similarities))
    on_topic = np.flatnonzero(~off_topic_mask(similarities, config))
    path = viterbi(normalized[on_topic], config)
    chosen = dict(zip(on_topic.tolist(), path))
    decisions = []
    for index, row in enumerate(normalized):
        page_index = chosen.get(index)
        confidence = 0.0 if page_index is None else _confidence(row, page_index)
        raw_page_index = int(similarities[index].argmax())
        decisions.append(WindowDecision(page_index, raw_page_index, confidence))
    return decisions


class E5Embedder:
    """Local multilingual embeddings. e5 models need the query/passage prefixes."""

    def __init__(self, model: str) -> None:
        from sentence_transformers import SentenceTransformer  # Slow import.

        self._model = SentenceTransformer(model, device="cpu")

    def _encode(self, texts: list[str]) -> np.ndarray:
        return np.asarray(self._model.encode(texts, normalize_embeddings=True, batch_size=16))

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        return self._encode([f"query: {text}" for text in texts])

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return self._encode([f"passage: {text}" for text in texts])


class MockEmbedder:
    """Hashed bag of words and character pairs, for tests with no model download."""

    dimensions = 2048

    def _embed(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimensions)
        lowered = text.lower()
        features = re.findall(r"[a-z0-9]{2,}", lowered)
        for word in re.findall(r"[^\W\da-z_]{2,}", lowered):
            features += [word[index : index + 2] for index in range(len(word) - 1)]
        for feature in features:
            digest = hashlib.md5(feature.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:4], "little") % self.dimensions] += 1
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        return np.array([self._embed(text) for text in texts])

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return np.array([self._embed(text) for text in texts])


def get_embedder():
    settings = get_settings()
    if settings.embedding_provider == "e5":
        return E5Embedder(settings.embedding_model)
    if settings.embedding_provider == "mock":
        return MockEmbedder()
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {settings.embedding_provider!r}")


def page_text(page: Page) -> str:
    """Title plus body. The title block is also a body block, so it appears only once."""
    return " ".join(block.text for block in page.blocks)


def similarity_matrix(windows: list[Window], pages: list[Page], embedder) -> np.ndarray:
    """Cosine similarity, windows x pages. Pages with no text score 0 for every window."""
    matrix = np.zeros((len(windows), len(pages)))
    with_text = [index for index, page in enumerate(pages) if page.blocks]
    if windows and with_text:
        queries = embedder.embed_queries([window.text for window in windows])
        passages = embedder.embed_passages([page_text(pages[index]) for index in with_text])
        matrix[:, with_text] = queries @ passages.T
    return matrix


def align_windows(
    windows: list[Window],
    extraction: PdfExtraction,
    embedder=None,
    config: AlignConfig | None = None,
) -> Alignment:
    embedder = embedder or get_embedder()
    config = config or AlignConfig.from_env()
    pages = extraction.pages
    similarities = similarity_matrix(windows, pages, embedder)
    decisions = align_similarities(similarities, config)
    numbers = [page.page for page in pages]
    aligned = [
        AlignedWindow(
            **window.model_dump(),
            page=None if decision.page_index is None else numbers[decision.page_index],
            confidence=decision.confidence,
            raw_page=numbers[decision.raw_page_index] if numbers else 0,
            similarities=row.tolist(),
        )
        for window, decision, row in zip(windows, decisions, similarities)
    ]
    return Alignment(windows=aligned, pages=numbers)
