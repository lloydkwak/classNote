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
# Figures, as fractions of the page.
MIN_FIGURE_AREA = 0.01
MIN_FIGURE_SIDE = 0.03
MAX_FIGURE_AREA = 0.85  # Larger than this is a background or a scanned page.
MAX_TEXT_COVERAGE = 0.6  # A vector shape mostly filled by text is a text box or a table.
FRAME_MAX_DRAWINGS = 2  # One or two shapes with some text inside are a frame around that text.
FRAME_MIN_TEXT_COVERAGE = 0.15
MAX_FIGURES_PER_PAGE = 4
DRAWING_CLUSTER_TOLERANCE = 24  # Points. Bars in a chart are separated by gaps.
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
        if block["type"] != 0:  # 0 is text. Images are handled by _read_figures.
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


def _is_figure_sized(rect: pymupdf.Rect, page_rect: pymupdf.Rect) -> bool:
    area = rect.get_area() / page_rect.get_area()
    return (
        MIN_FIGURE_AREA <= area <= MAX_FIGURE_AREA
        and rect.width / page_rect.width >= MIN_FIGURE_SIDE
        and rect.height / page_rect.height >= MIN_FIGURE_SIDE
    )


def _text_coverage(rect: pymupdf.Rect, text_rects: list[pymupdf.Rect]) -> float:
    covered = sum((rect & text).get_area() for text in text_rects if rect.intersects(text))
    return covered / rect.get_area()


def _is_text_container(
    cluster: pymupdf.Rect, drawings: list[dict], text_rects: list[pymupdf.Rect]
) -> bool:
    coverage = _text_coverage(cluster, text_rects)
    if coverage > MAX_TEXT_COVERAGE:
        return True
    shapes = sum(1 for drawing in drawings if cluster.intersects(drawing["rect"]))
    return shapes <= FRAME_MAX_DRAWINGS and coverage > FRAME_MIN_TEXT_COVERAGE


def _merge_overlapping(rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    merged: list[pymupdf.Rect] = []
    for rect in rects:
        current = pymupdf.Rect(rect)
        for other in [item for item in merged if item.intersects(current)]:
            current |= other
            merged.remove(other)
        merged.append(current)
    return merged


def _read_figures(page: pymupdf.Page) -> tuple[list[pymupdf.Rect], list[pymupdf.Rect]]:
    """Find images, and vector drawings clustered into regions such as charts.

    Returns the figures, and every region that was considered. A template's title bar is
    only sometimes filtered out as a text box, depending on how long the title is, so
    repeats have to be counted before that filter.
    """
    page_rect = page.rect
    images = [pymupdf.Rect(info["bbox"]) & page_rect for info in page.get_image_info()]
    # Backgrounds and frames would otherwise join everything into one huge cluster.
    drawings = [
        drawing
        for drawing in page.get_drawings()
        if drawing["rect"].get_area() / page_rect.get_area() <= MAX_FIGURE_AREA
    ]
    clusters = page.cluster_drawings(
        drawings=drawings,
        x_tolerance=DRAWING_CLUSTER_TOLERANCE,
        y_tolerance=DRAWING_CLUSTER_TOLERANCE,
    ) if drawings else []
    text_rects = [
        pymupdf.Rect(block[:4]) for block in page.get_text("blocks") if block[6] == 0
    ]
    sized_clusters = [
        rect
        for rect in (pymupdf.Rect(cluster) & page_rect for cluster in clusters)
        if _is_figure_sized(rect, page_rect)
    ]
    sized_images = [rect for rect in images if _is_figure_sized(rect, page_rect)]
    vectors = [
        rect for rect in sized_clusters if not _is_text_container(rect, drawings, text_rects)
    ]
    # Decorations must not join a real figure, or the merged region won't look repeated.
    return _merge_overlapping(sized_images + vectors), sized_images + sized_clusters


def _figure_key(rect: pymupdf.Rect) -> tuple:
    return tuple(round(value, -1) for value in (rect.x0, rect.y0, rect.x1, rect.y1))


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
        found = [_read_figures(page) for page in document]
        sizes = [(page.rect.width, page.rect.height) for page in document]

    repeated = _find_repeated(raw_pages)
    # A figure in the same place on many pages is a logo or a template decoration.
    raw_figures = [figures for figures, _ in found]
    figure_counts = Counter(
        key for _, regions in found for key in {_figure_key(rect) for rect in regions}
    )
    threshold = max(
        HEADER_FOOTER_MIN_PAGES, math.ceil(len(raw_pages) * HEADER_FOOTER_PAGE_FRACTION)
    )
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
        width, height = sizes[number - 1]
        figures = [
            rect
            for rect in raw_figures[number - 1]
            if figure_counts[_figure_key(rect)] < threshold
        ]
        figures = sorted(figures, key=lambda rect: -rect.get_area())[:MAX_FIGURES_PER_PAGE]
        blocks += [
            Block(
                block_id=f"p{number}_b{len(kept) + index}",
                page=number,
                text="",  # Captioned in a later step.
                bbox=(rect.x0 / width, rect.y0 / height, rect.x1 / width, rect.y1 / height),
                kind="figure",
            )
            for index, rect in enumerate(figures)
        ]
        pages.append(
            Page(
                page=number,
                title=title_block.text if title_block else "",
                blocks=blocks,
                low_text=sum(len(block.text) for block in kept) < LOW_TEXT_CHARS,
                width=width,
                height=height,
            )
        )
    return PdfExtraction(pages=pages, key_terms=_extract_key_terms(pages))
