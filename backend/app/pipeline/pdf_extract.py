"""Extract per-page text blocks, titles, and key terms from a PDF."""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.models import Block, Page, PdfExtraction

MIN_BLOCK_AREA = 0.0001  # fraction of the page
LOW_TEXT_CHARS = 30
HEADER_FOOTER_MIN_PAGES = 3
HEADER_FOOTER_PAGE_FRACTION = 0.5
MAX_KEY_TERMS = 60
MIN_TERM_COUNT = 2

PAGE_NUMBER = re.compile(r"^\W*(?:\w{1,5}\W*)?\d{1,4}(?:\W+\d{1,4})?\W*$")
TERM = re.compile(r"[^\W\d_][\w+#-]+")
STOPWORDS = frozenset(
    "the and for are but not you all can was one our out has have had his her she him "
    "this that with from they will would there their what about which when make like "
    "time just know take into your some could them than then only its also how each".split()
)


@dataclass
class _RawBlock:
    text: str
    bbox: tuple[float, float, float, float]
    font_size: float


def _read_page(page: pymupdf.Page) -> list[_RawBlock]:
    width, height = page.rect.width, page.rect.height
    blocks = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:  # 0 is text; images have no text to align against
            continue
        lines = []
        font_size = 0.0
        for line in block["lines"]:
            lines.append("".join(span["text"] for span in line["spans"]))
            font_size = max([font_size, *(span["size"] for span in line["spans"])])
        text = " ".join(" ".join(lines).split())
        x0, y0, x1, y1 = block["bbox"]
        bbox = (
            max(0.0, x0 / width),
            max(0.0, y0 / height),
            min(1.0, x1 / width),
            min(1.0, y1 / height),
        )
        blocks.append(_RawBlock(text, bbox, font_size))
    return blocks


def _is_meaningless(block: _RawBlock) -> bool:
    x0, y0, x1, y1 = block.bbox
    if (x1 - x0) * (y1 - y0) < MIN_BLOCK_AREA:
        return True
    if not any(char.isalnum() for char in block.text):
        return True
    return bool(PAGE_NUMBER.match(block.text))


def _repeat_key(block: _RawBlock) -> tuple:
    """Identify a block by vertical position and text, ignoring digits.

    PDF text extraction can merge a footer and its page number into one block, so
    "Lecture 5 9" and "Lecture 5 10" must match despite having different widths.
    """
    _, y0, _, y1 = block.bbox
    return round(y0, 2), round(y1, 2), re.sub(r"\d+", "#", block.text)


def _find_repeated(pages: list[list[_RawBlock]]) -> set[tuple]:
    counts = Counter(key for page in pages for key in {_repeat_key(block) for block in page})
    threshold = max(HEADER_FOOTER_MIN_PAGES, math.ceil(len(pages) * HEADER_FOOTER_PAGE_FRACTION))
    return {key for key, count in counts.items() if count >= threshold}


def _extract_key_terms(pages: list[Page]) -> list[str]:
    """Return slide titles first, then the most frequent terms across all pages."""
    terms = list(dict.fromkeys(page.title for page in pages if page.title))
    counts: Counter[str] = Counter()
    for page in pages:
        for block in page.blocks:
            for word in TERM.findall(block.text):
                is_short_ascii = word.isascii() and len(word) < 3
                if not is_short_ascii and word.lower() not in STOPWORDS:
                    counts[word] += 1
    frequent = [word for word, count in counts.most_common() if count >= MIN_TERM_COUNT]
    return list(dict.fromkeys(terms + frequent))[:MAX_KEY_TERMS]


def extract_pdf(path: Path) -> PdfExtraction:
    with pymupdf.open(path) as document:
        raw_pages = [_read_page(page) for page in document]

    repeated = _find_repeated(raw_pages)
    pages = []
    for number, raw_blocks in enumerate(raw_pages, start=1):
        kept = [
            block
            for block in raw_blocks
            if not _is_meaningless(block) and _repeat_key(block) not in repeated
        ]
        # max() returns the first of equal sizes, so a uniform page uses its first block.
        title_block = max(kept, key=lambda block: block.font_size, default=None)
        blocks = [
            Block(
                block_id=f"p{number}_b{index}",
                page=number,
                text=block.text,
                bbox=block.bbox,
                is_title=block is title_block,
            )
            for index, block in enumerate(kept)
        ]
        pages.append(
            Page(
                page=number,
                title=title_block.text if title_block else "",
                blocks=blocks,
                low_text=sum(len(block.text) for block in kept) < LOW_TEXT_CHARS,
            )
        )
    return PdfExtraction(pages=pages, key_terms=_extract_key_terms(pages))
