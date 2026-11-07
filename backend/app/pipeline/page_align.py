"""Decide which page each window explains, using an order-constrained DP (Viterbi)."""

import hashlib
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache

import numpy as np

from app.config import get_settings
from app.models import AlignedWindow, Alignment, Page, PdfExtraction, Window

# A z-score gap this large between the best and second-best page is full confidence.
FULL_CONFIDENCE_GAP = 1.0
# Stops a near-constant best similarity from making tiny differences look like outliers.
MIN_BEST_SCORE_SCALE = 0.01
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
    back_penalty_per_page: float = 0.2
    off_topic_threshold: float = -2.5

    @classmethod
    def from_env(cls) -> "AlignConfig":
        config = cls()
        names = {
            "next_page_penalty": "ALIGN_NEXT_PAGE_PENALTY",
            "skip_penalty_per_page": "ALIGN_SKIP_PENALTY_PER_PAGE",
            "back_penalty": "ALIGN_BACK_PENALTY",
            "back_penalty_per_page": "ALIGN_BACK_PENALTY_PER_PAGE",
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
                penalties[source, target] = (
                    config.back_penalty + (abs(distance) - 1) * config.back_penalty_per_page
                )
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


def viterbi(
    scores_by_window: np.ndarray, config: AlignConfig, start_index: int | None = None
) -> list[int]:
    """Return the page index per row that maximizes score minus move penalties.

    With `start_index`, moving to the first row's page costs what a move from there would.
    """
    window_count, page_count = scores_by_window.shape
    if window_count == 0:
        return []
    penalties = transition_penalties(page_count, config)
    scores = scores_by_window[0].copy()
    if start_index is not None:
        scores -= penalties[start_index]
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


def align_similarities(
    similarities: np.ndarray, config: AlignConfig, frozen: Sequence[int | None] = ()
) -> list[WindowDecision]:
    """Align a windows x pages matrix.

    Off-topic windows are left out of the DP, so chatter neither moves the current
    page nor costs anything: the next on-topic window continues from where it left off.

    `frozen` holds decisions already made for the first windows. They stay as they are,
    and the rest continue from the last page among them.
    """
    if similarities.shape[0] == 0 or similarities.shape[1] == 0:
        return [WindowDecision(None, 0, 0.0) for _ in range(similarities.shape[0])]
    normalized = normalize_rows(remove_page_bias(similarities))
    on_topic = np.flatnonzero(~off_topic_mask(similarities, config))
    on_topic = on_topic[on_topic >= len(frozen)]
    start_index = next((page for page in reversed(frozen) if page is not None), None)
    path = viterbi(normalized[on_topic], config, start_index)
    chosen = dict(enumerate(frozen)) | dict(zip(on_topic.tolist(), path))
    decisions = []
    for index, row in enumerate(normalized):
        page_index = chosen.get(index)
        confidence = 0.0 if page_index is None else _confidence(row, page_index)
        raw_page_index = int(similarities[index].argmax())
        decisions.append(WindowDecision(page_index, raw_page_index, confidence))
    return decisions


class E5Embedder:
    """Local multilingual embeddings. e5 models need the query/passage prefixes.

    Results are remembered, because a live talk embeds the same pages and windows
    again after every chunk.
    """

    def __init__(self, model: str) -> None:
        from sentence_transformers import SentenceTransformer  # Slow import.

        self._model = SentenceTransformer(model, device="cpu")
        self._remembered: dict[str, np.ndarray] = {}

    def _encode(self, texts: list[str]) -> np.ndarray:
        new = [text for text in dict.fromkeys(texts) if text not in self._remembered]
        if new:
            vectors = self._model.encode(new, normalize_embeddings=True, batch_size=16)
            self._remembered.update(zip(new, np.asarray(vectors)))
        return np.array([self._remembered[text] for text in texts])

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


class BgeM3Embedder:
    """BAAI/bge-m3 — MIT license. Uses same query:/passage: prefixes as E5."""

    def __init__(self, model: str = "BAAI/bge-m3") -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model, device="cpu")

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        return self._model.encode(
            [f"query: {t}" for t in texts], normalize_embeddings=True, batch_size=16
        )

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return self._model.encode(
            [f"passage: {t}" for t in texts], normalize_embeddings=True, batch_size=16
        )


class Qwen3Embedder:
    """Qwen/Qwen3-Embedding-0.6B — Apache-2.0. Uses task instruction prefix for queries."""

    _QUERY_PREFIX = (
        "Instruct: Retrieve relevant lecture slide for this speech excerpt.\nQuery: "
    )

    def __init__(self, model: str = "Qwen/Qwen3-Embedding-0.6B") -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model, device="cpu")

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        return self._model.encode(
            [self._QUERY_PREFIX + t for t in texts], normalize_embeddings=True, batch_size=16
        )

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return self._model.encode(texts, normalize_embeddings=True, batch_size=16)


@lru_cache(maxsize=1)
def _load_e5(model: str) -> E5Embedder:
    return E5Embedder(model)


@lru_cache(maxsize=1)
def _load_bge(model: str) -> BgeM3Embedder:
    return BgeM3Embedder(model)


@lru_cache(maxsize=1)
def _load_qwen3(model: str) -> Qwen3Embedder:
    return Qwen3Embedder(model)


def get_embedder(model: str | None = None):
    settings = get_settings()
    effective_model = model or settings.embedding_model
    if settings.embedding_provider == "mock":
        return MockEmbedder()
    if "bge-m3" in effective_model:
        return _load_bge(effective_model)
    if "Qwen3-Embedding" in effective_model or "Qwen/Qwen3-Embed" in effective_model:
        return _load_qwen3(effective_model)
    return _load_e5(effective_model)


def page_text(page: Page) -> str:
    """Title, body, and figure captions. The title block is one of the blocks."""
    return " ".join(block.text for block in page.blocks if block.text)


def similarity_matrix(windows: list[Window], pages: list[Page], embedder) -> np.ndarray:
    """Cosine similarity, windows x pages. Pages with no text score 0 for every window."""
    matrix = np.zeros((len(windows), len(pages)))
    with_text = [index for index, page in enumerate(pages) if page_text(page)]
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
    frozen_pages: Sequence[int | None] = (),
) -> Alignment:
    embedder = embedder or get_embedder()
    config = config or AlignConfig.from_env()
    pages = extraction.pages
    similarities = similarity_matrix(windows, pages, embedder)
    numbers = [page.page for page in pages]
    frozen = [None if page is None else numbers.index(page) for page in frozen_pages]
    decisions = align_similarities(similarities, config, frozen)
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
