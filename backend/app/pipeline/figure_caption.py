"""Describe each figure with the LLM, so speech can be matched to it.

Captions are best-effort: on any failure a figure keeps an empty caption and the job goes
on. Requests carry several images each, because free-tier limits count requests.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.cache import read_cache, write_cache
from app.config import get_settings
from app.llm import get_provider
from app.llm.base import DEFAULT_RETRY, LLMProvider, QuotaExhausted, RetryPolicy
from app.models import Block, PdfExtraction

log = logging.getLogger(__name__)

BATCH_SIZE = 8
RENDER_DPI = 110
MAX_CONSECUTIVE_FAILURES = 2
SLIDE_TEXT_CHARS = 200

PROMPT = """These images are figures cropped from university lecture slides, in order.
For each image, write one sentence describing what it shows. If it's a chart, say what it plots.

Rules:
- Write each sentence in the language of that image's slide, shown below.
- Never translate technical terms. Keep them exactly as they appear in the image or on the slide.
- Describe only what you can see.

Return a JSON array of strings, with exactly one string per image, in the same order.

Slide text for each image:
"""


@dataclass
class _Figure:
    block: Block
    png: bytes
    slide_text: str

    @property
    def key(self) -> list[str]:
        return [hashlib.sha256(self.png).hexdigest()]


def _render_figures(pdf: Path, extraction: PdfExtraction) -> list[_Figure]:
    figures = []
    with pymupdf.open(pdf) as document:
        for page in extraction.pages:
            blocks = [block for block in page.blocks if block.kind == "figure"]
            slide_text = " ".join(b.text for b in page.blocks if b.kind == "text")
            for block in blocks:
                pdf_page = document[page.page - 1]
                x0, y0, x1, y1 = block.bbox
                clip = pymupdf.Rect(
                    x0 * page.width, y0 * page.height, x1 * page.width, y1 * page.height
                )
                png = pdf_page.get_pixmap(clip=clip, dpi=RENDER_DPI, annots=False).tobytes("png")
                figures.append(_Figure(block, png, slide_text[:SLIDE_TEXT_CHARS]))
    return figures


def _ask_for_captions(provider: LLMProvider, batch: list[_Figure]) -> list[str]:
    """Validate the reply and retry once if it's invalid, like note generation does."""
    slides = "\n".join(f"{index + 1}. {figure.slide_text}" for index, figure in enumerate(batch))
    prompt = PROMPT + slides
    images = [figure.png for figure in batch]
    for attempt in (1, 2):
        try:
            captions = json.loads(provider.describe_images(prompt, images))
            if (
                isinstance(captions, list)
                and len(captions) == len(batch)
                and all(isinstance(caption, str) for caption in captions)
            ):
                return captions
        except ValueError:  # Not JSON.
            pass
        log.warning("Invalid caption reply on attempt %d.", attempt)
    raise ValueError("The LLM returned invalid captions twice.")


def caption_figures(
    pdf: Path, extraction: PdfExtraction, retry: RetryPolicy = DEFAULT_RETRY
) -> None:
    """Fill in each figure block's text, using cached captions where possible."""
    figures = _render_figures(pdf, extraction)
    pending = []
    # The provider is part of the key so that a better model's captions aren't reused
    # silently, but only the image decides whether two figures are the same.
    provider_key = _provider_key()
    for figure in figures:
        cached = read_cache("caption", [*figure.key, provider_key])
        if cached is not None:
            figure.block.text = cached
        else:
            pending.append(figure)
    if not pending or get_settings().demo_mode:
        return

    try:
        provider = get_provider()
    except Exception as error:  # For example, no API key. Captions are optional.
        log.warning("Skipping figure captions: %s", error)
        return

    provider.retry_policy = retry
    failures = 0
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        try:
            captions = _ask_for_captions(provider, batch)
        except QuotaExhausted as error:
            log.warning("Stopping figure captions: %s", error)
            return
        except Exception as error:
            log.warning("Figure captions failed for a batch: %s", error)
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                return
            continue
        failures = 0
        for figure, caption in zip(batch, captions):
            figure.block.text = caption.strip()
            write_cache("caption", [*figure.key, provider_key], figure.block.text)


def _provider_key() -> str:
    settings = get_settings()
    return f"{settings.llm_provider}:{settings.llm_model or 'default'}"
