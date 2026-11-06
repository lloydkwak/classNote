"""Opt-in replay baselines over saved semantic scores. No downloads or API calls."""

import re
from dataclasses import asdict

import numpy as np

from app.models import Alignment
from app.pipeline.page_align import AlignConfig, align_similarities


def terms(text, glossary):
    text = text.casefold()
    for spoken, spelling in glossary.items():
        text = text.replace(spoken.casefold(), spelling.casefold())
    return set(re.findall(r"[^\W_]{2,}", text))


def replay(alignment, extraction, segments, variant="current", lexical_weight=.25,
           glossary=None, config=None, refinement_radius=10.0):
    config = config or AlignConfig.from_env()
    if variant not in {"saved", "current", "hybrid", "hybrid_refined"}:
        raise ValueError("Unknown experimental variant")
    if variant == "saved":
        return alignment.model_copy(deep=True)
    matrix = np.asarray([window.similarities for window in alignment.windows], dtype=float)
    if not alignment.windows:
        return alignment.model_copy(deep=True)
    if matrix.shape != (len(alignment.windows), len(alignment.pages)):
        raise ValueError("Saved similarity matrix does not match pages")
    original = matrix.copy()
    if variant.startswith("hybrid"):
        pages = {page.page: page for page in extraction.pages}
        for column, number in enumerate(alignment.pages):
            page = pages[number]
            body = terms(" ".join(block.text for block in page.blocks), glossary or {})
            title = terms(page.title, glossary or {})
            for index, window in enumerate(alignment.windows):
                query = terms(window.text, glossary or {})
                lexical = (len(query & body) + len(query & title)) / max(1, len(query))
                matrix[index, column] += lexical_weight * lexical * max(float(original[index].std()), .01)
    decisions = align_similarities(matrix, config)
    result = Alignment(pages=alignment.pages, windows=[window.model_copy(update={
        "page": None if decision.page_index is None else alignment.pages[decision.page_index],
        "confidence": decision.confidence,
    }) for window, decision in zip(alignment.windows, decisions)])
    if variant == "hybrid_refined":
        boundaries = sorted({segment.end for segment in segments})
        for first, following in zip(result.windows, result.windows[1:]):
            if (first.page is None or following.page is None or first.page == following.page
                    or min(first.confidence, following.confidence) > .3
                    or abs(first.end - following.start) > 1e-6):
                continue
            candidates = [time for time in boundaries if first.start < time < following.end
                          and abs(time - following.start) <= refinement_radius]
            if candidates:
                boundary = min(candidates, key=lambda time: abs(time - following.start))
                first.end = following.start = boundary
    return result


def experiment_config(variant, lexical_weight=.25, glossary=None):
    return {"variant": variant, "alignment": asdict(AlignConfig.from_env()),
            "lexical_weight": lexical_weight, "glossary": glossary or {},
            "refinement": "low-confidence segment-boundary snapping; no re-embedding",
            "refinement_radius_seconds": 10.0, "refinement_confidence_threshold": .3}
