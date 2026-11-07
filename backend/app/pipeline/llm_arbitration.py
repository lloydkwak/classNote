"""LLM arbitration for low-confidence sentence-to-page assignments.

After the sentence DP runs, stretches of consecutive on-topic sentences whose
confidence is below a threshold are sent to the LLM with:
  - the sentence texts
  - candidate slide texts (DP choice ±2)
  - optional page thumbnails rendered from the PDF

The LLM returns page assignments for those sentences, which override the DP.
Call count is returned so callers can budget and log it.
"""

import json
import logging
from pathlib import Path

import numpy as np

from app.pipeline.page_align import page_text
from app.pipeline.page_align import WindowDecision
from app.models import Page, Window

log = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """\
You are aligning lecture speech to slides.
Assign each spoken sentence to its best matching slide number.

SENTENCES:
{sentences}

CANDIDATE SLIDES (each is one slide):
{slides}

Each sentence must be assigned to one of these slide numbers: {page_nums}
Respond with JSON only: {{"assignments": [slide_number_for_sent_0, slide_number_for_sent_1, ...]}}
The list must have exactly {n_sentences} elements."""


def _render_thumbnails(pdf_path: Path, pages: list[Page], zoom: float = 0.5) -> list[bytes]:
    """Return one PNG thumbnail per page; returns [] if pymupdf is unavailable."""
    try:
        import pymupdf
    except ImportError:
        return []
    thumbnails = []
    try:
        with pymupdf.open(str(pdf_path)) as doc:
            for page in pages:
                pix = doc[page.page - 1].get_pixmap(
                    matrix=pymupdf.Matrix(zoom, zoom), alpha=False
                )
                thumbnails.append(pix.tobytes("png"))
    except Exception as exc:
        log.debug("Thumbnail rendering failed: %s", exc)
        return []
    return thumbnails


def _find_low_conf_stretches(
    decisions: list[WindowDecision],
    threshold: float,
) -> list[tuple[int, int]]:
    """Return (start, end) index pairs of consecutive on-topic low-confidence runs."""
    stretches: list[tuple[int, int]] = []
    start: int | None = None
    for i, d in enumerate(decisions):
        if d.page_index is not None and d.confidence < threshold:
            if start is None:
                start = i
        else:
            if start is not None:
                stretches.append((start, i))
            start = None
    if start is not None:
        stretches.append((start, len(decisions)))
    # Longest stretches first so we address the most impactful ones within the call budget.
    return sorted(stretches, key=lambda x: -(x[1] - x[0]))


def arbitrate(
    windows: list[Window],
    pages: list[Page],
    page_numbers: list[int],
    decisions: list[WindowDecision],
    *,
    pdf_path: Path | None = None,
    llm=None,
    threshold: float = 0.15,
    max_calls: int = 5,
    candidate_radius: int = 2,
) -> tuple[list[WindowDecision], int]:
    """Override low-confidence DP decisions using the LLM.

    Returns (updated_decisions, llm_call_count).
    Does nothing if llm is None or threshold == 0.
    """
    if llm is None or threshold <= 0:
        return decisions, 0

    stretches = _find_low_conf_stretches(decisions, threshold)
    if not stretches:
        return decisions, 0

    decisions = list(decisions)
    page_num_to_idx = {pn: i for i, pn in enumerate(page_numbers)}
    llm_calls = 0

    for s_start, s_end in stretches[:max_calls]:
        # Determine candidate page range from DP choices in this stretch.
        assigned_indices = [
            d.page_index for d in decisions[s_start:s_end]
            if d.page_index is not None
        ]
        if not assigned_indices:
            continue
        lo = max(0, min(assigned_indices) - candidate_radius)
        hi = min(len(pages) - 1, max(assigned_indices) + candidate_radius)
        candidate_pages = pages[lo : hi + 1]

        sentences = [windows[i].text for i in range(s_start, s_end)]
        slide_lines = [
            f"Slide {p.page}: {page_text(p)[:250]}"
            for p in candidate_pages
        ]
        pnums = [p.page for p in candidate_pages]

        prompt = _PROMPT_TEMPLATE.format(
            sentences="\n".join(f"[{i}] {t}" for i, t in enumerate(sentences)),
            slides="\n\n".join(slide_lines),
            page_nums=pnums,
            n_sentences=len(sentences),
        )

        thumbnails: list[bytes] = []
        if pdf_path is not None and pdf_path.exists():
            thumbnails = _render_thumbnails(pdf_path, candidate_pages)

        try:
            if thumbnails:
                raw = llm.describe_images(prompt, thumbnails)
            else:
                raw = llm.complete_json(prompt)
            data = json.loads(raw)
            assigned_pnums: list[int] = data["assignments"]
        except Exception as exc:
            log.warning("LLM arbitration call failed: %s", exc)
            llm_calls += 1
            continue

        llm_calls += 1
        for rel_i, pnum in enumerate(assigned_pnums[: s_end - s_start]):
            abs_i = s_start + rel_i
            new_idx = page_num_to_idx.get(pnum)
            if new_idx is not None:
                d = decisions[abs_i]
                decisions[abs_i] = WindowDecision(new_idx, d.raw_page_index, d.confidence)
            else:
                log.debug("LLM returned unknown page number %d, keeping DP decision", pnum)

    return decisions, llm_calls
