"""Extract per-page text blocks, titles, and key terms from a PDF."""

import hashlib
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pymupdf

from app.cache import read_cache, write_cache
from app.models import BBox, Block, Line, Page, PdfExtraction, Word

log = logging.getLogger(__name__)

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
BULLET_CHARS = frozenset("•◦▪▫●○■□▶►‣∙·-–—")
OCR_ZOOM = 2.0
OCR_MIN_SCORE = 0.6
OCR_CACHE_VERSION = "korean-PP-OCRv5-mobile"
# Free space, measured on a low-resolution render.
FREE_SPACE_WIDTH = 240  # Cells across the page.
FREE_SPACE_DILATE = 2  # Cells of clearance around ink.
INK_TOLERANCE = 16  # Of 255, per color channel, from the page background.
MIN_FREE_WIDTH = 0.1  # Of page width. Narrower than this can't hold a short note.
MIN_FREE_HEIGHT = 0.05
MAX_FREE_OVERLAP = 0.6  # Of the smaller rectangle. They may overlap: each is a choice.
MAX_FREE_RECTS = 12
# The marked page image.
MARKED_ZOOM = 2.0
MARK_FONT_SIZE = 7.0  # Points.
MARK_LINE_COLOR = (0.75, 0.1, 0.1)
MARK_FIGURE_COLOR = (0.1, 0.3, 0.75)

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
    # A short assignment such as 'alpha = 1' is not a footer/page number.
    return "=" not in block.text and bool(PAGE_NUMBER.match(block.text))


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


# Everything below is for ANNOTATION_ENGINE=v2: the real things on a slide that an annotation
# can point at, and the empty space a note can be written in.


def _normalized(rect, width: float, height: float) -> BBox:
    x0, y0, x1, y1 = rect
    return (
        max(0.0, x0 / width),
        max(0.0, y0 / height),
        min(1.0, x1 / width),
        min(1.0, y1 / height),
    )


def _union(boxes: list[BBox]) -> BBox:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _is_bullet(text: str) -> bool:
    return all(char in BULLET_CHARS or 0xE000 <= ord(char) <= 0xF8FF for char in text)


def _enclosing_block(box: BBox, blocks: list[Block]) -> Block | None:
    """The smallest block holding the box's center."""
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    holding = [
        block
        for block in blocks
        if block.bbox[0] <= x <= block.bbox[2] and block.bbox[1] <= y <= block.bbox[3]
    ]
    return min(
        holding,
        key=lambda block: (block.bbox[2] - block.bbox[0]) * (block.bbox[3] - block.bbox[1]),
        default=None,
    )


def _append_line(page: Page, texts: list[str], boxes: list[BBox], block_id, source: str) -> None:
    number = page.page
    line_id = f"p{number}_l{len(page.lines)}"
    start = len(page.words)
    words = [
        Word(
            word_id=f"p{number}_w{start + index}",
            text=text,
            bbox=box,
            line_id=line_id,
            block_id=block_id,
            source=source,
        )
        for index, (text, box) in enumerate(zip(texts, boxes))
    ]
    page.words += words
    page.lines.append(
        Line(
            line_id=line_id,
            word_ids=[word.word_id for word in words],
            text=" ".join(texts),
            bbox=_union(boxes),
            block_id=block_id,
            source=source,
        )
    )


def _read_words(source: pymupdf.Page, page: Page) -> None:
    """Add the PDF's words, skipping headers and footers, which extraction already dropped."""
    text_blocks = [block for block in page.blocks if block.kind == "text"]
    lines: dict[tuple[int, int], list[tuple[str, BBox, str]]] = {}
    for x0, y0, x1, y1, text, block_number, line_number, word_number in sorted(
        source.get_text("words"), key=lambda word: (word[5], word[6], word[7])
    ):
        box = _normalized((x0, y0, x1, y1), page.width, page.height)
        block = _enclosing_block(box, text_blocks)
        # A standalone '-' inside an equation is an operator, not a list bullet.
        if block is not None and not (_is_bullet(text) and word_number == 0):
            lines.setdefault((block_number, line_number), []).append((text, box, block.block_id))
    for words in lines.values():
        _append_line(page, [w[0] for w in words], [w[1] for w in words], words[0][2], "pdf")


@lru_cache(maxsize=1)
def _load_ocr():
    """RapidOCR if it's installed (`uv sync --extra ocr`), otherwise None."""
    try:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
    except ImportError:
        log.info("RapidOCR isn't installed, so text inside images is skipped.")
        return None
    # The Korean model reads Latin letters too, which code-switched slides need. PP-OCRv6, the
    # default, has no Korean model.
    return RapidOCR(
        params={
            "Rec.lang_type": LangRec.KOREAN,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE,
            "Global.log_level": "error",
        }
    )


def _split_ocr_line(text: str, box: BBox) -> list[tuple[str, BBox]]:
    """OCR gives a box per line, so divide it between the words by their length."""
    words = text.split()
    total = sum(len(word) for word in words) + len(words) - 1
    x0, y0, x1, y1 = box
    unit = (x1 - x0) / max(total, 1)
    pieces = []
    cursor = x0
    for word in words:
        end = cursor + unit * len(word)
        pieces.append((word, (cursor, y0, end, y1)))
        cursor = end + unit
    return pieces


def _ocr_lines(engine, png: bytes) -> list[tuple[list, str, float]]:
    """Read one image, cached because a lecture takes minutes and reruns repeat the same images."""
    key = [hashlib.sha256(png).hexdigest(), OCR_CACHE_VERSION]
    cached = read_cache("ocr", key)
    if cached is not None:
        return cached
    result = engine(png)
    lines = []
    if result is not None and result.boxes is not None:
        lines = [
            ([[float(x), float(y)] for x, y in corners], str(text), float(score))
            for corners, text, score in zip(result.boxes, result.txts, result.scores)
        ]
    write_cache("ocr", key, lines)
    return lines


def _ocr_regions(source: pymupdf.Page, page: Page) -> list[pymupdf.Rect]:
    if page.low_text:
        return [source.rect]
    images = [pymupdf.Rect(info["bbox"]) & source.rect for info in source.get_image_info()]
    figures = [
        pymupdf.Rect(
            block.bbox[0] * page.width,
            block.bbox[1] * page.height,
            block.bbox[2] * page.width,
            block.bbox[3] * page.height,
        )
        for block in page.blocks
        if block.kind == "figure"
    ]
    # Logos and decorations were already filtered out of the figures.
    return [
        image
        for image in images
        if not image.is_empty and any(image.intersects(figure) for figure in figures)
    ]


def _read_ocr_words(source: pymupdf.Page, page: Page, engine) -> None:
    figures = [block for block in page.blocks if block.kind == "figure"]
    pdf_words = list(page.words)
    for region in _ocr_regions(source, page):
        picture = source.get_pixmap(matrix=pymupdf.Matrix(OCR_ZOOM, OCR_ZOOM), clip=region)
        for corners, text, score in _ocr_lines(engine, picture.tobytes("png")):
            if score < OCR_MIN_SCORE or not any(char.isalnum() for char in text):
                continue
            xs, ys = [point[0] for point in corners], [point[1] for point in corners]
            box = _normalized(
                (
                    region.x0 + min(xs) / OCR_ZOOM,
                    region.y0 + min(ys) / OCR_ZOOM,
                    region.x0 + max(xs) / OCR_ZOOM,
                    region.y0 + max(ys) / OCR_ZOOM,
                ),
                page.width,
                page.height,
            )
            if _enclosing_word(box, pdf_words):  # A vector label on top of the image.
                continue
            pieces = _split_ocr_line(text, box)
            figure = _enclosing_block(box, figures)
            _append_line(
                page,
                [piece[0] for piece in pieces],
                [piece[1] for piece in pieces],
                figure.block_id if figure else None,
                "ocr",
            )


def _enclosing_word(box: BBox, words: list[Word]) -> bool:
    x, y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return any(
        word.bbox[0] <= x <= word.bbox[2] and word.bbox[1] <= y <= word.bbox[3] for word in words
    )


def _ink_mask(source: pymupdf.Page, page: Page) -> np.ndarray:
    """True where a note must not go: anything unlike the page background, words, and figures."""
    zoom = FREE_SPACE_WIDTH / source.rect.width
    picture = source.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
    pixels = np.frombuffer(picture.samples, dtype=np.uint8).reshape(picture.h, picture.w, 3)
    quantized = (pixels // 8).astype(np.int32)
    keys = quantized[..., 0] * 1024 + quantized[..., 1] * 32 + quantized[..., 2]
    values, counts = np.unique(keys, return_counts=True)
    common = keys == values[counts.argmax()]
    background = np.median(pixels[common], axis=0)
    ink = (np.abs(pixels.astype(np.int32) - background) > INK_TOLERANCE).any(axis=2)
    height, width = ink.shape
    occupied = [word.bbox for word in page.words]
    occupied += [block.bbox for block in page.blocks if block.kind == "figure"]
    for x0, y0, x1, y1 in occupied:
        ink[int(y0 * height) : math.ceil(y1 * height), int(x0 * width) : math.ceil(x1 * width)] = True
    return _dilated(ink, FREE_SPACE_DILATE)


def _dilated(mask: np.ndarray, radius: int) -> np.ndarray:
    """Grow True cells by `radius` in every direction, and treat the page edge as filled."""
    padded = np.pad(mask, radius, constant_values=True).astype(np.int32)
    size = 2 * radius + 1
    table = np.pad(padded.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    counts = table[size:, size:] - table[:-size, size:] - table[size:, :-size] + table[:-size, :-size]
    return counts > 0


def _maximal_rectangles(ink: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Every empty rectangle that can't grow in any direction, as (x0, y0, x1, y1) cells."""
    height, width = ink.shape
    heights = np.zeros(width + 1, dtype=np.int32)  # The extra 0 flushes the stack on each row.
    found = []
    for y in range(height):
        heights[:width] = np.where(ink[y], 0, heights[:width] + 1)
        stack: list[tuple[int, int]] = []  # (left edge, height), with growing heights.
        for x, current in enumerate(heights.tolist()):
            left = x
            while stack and stack[-1][1] > current:
                left, tall = stack.pop()
                can_grow_down = y + 1 < height and not ink[y + 1, left:x].any()
                if not can_grow_down:
                    found.append((left, y + 1 - tall, x, y + 1))
            if current and (not stack or stack[-1][1] < current):
                stack.append((left, current))
    return found


def _overlap_ratio(a: BBox, b: BBox) -> float:
    """Intersection over the smaller rectangle's area."""
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return width * height / smaller


def _free_rects(source: pymupdf.Page, page: Page) -> list[BBox]:
    ink = _ink_mask(source, page)
    cells_high, cells_wide = ink.shape
    rects = [
        (x0 / cells_wide, y0 / cells_high, x1 / cells_wide, y1 / cells_high)
        for x0, y0, x1, y1 in _maximal_rectangles(ink)
    ]
    rects = [
        rect
        for rect in rects
        if rect[2] - rect[0] >= MIN_FREE_WIDTH and rect[3] - rect[1] >= MIN_FREE_HEIGHT
    ]
    rects.sort(key=lambda rect: -(rect[2] - rect[0]) * (rect[3] - rect[1]))
    kept: list[BBox] = []
    for rect in rects:
        if all(_overlap_ratio(rect, other) < MAX_FREE_OVERLAP for other in kept):
            kept.append(rect)
        if len(kept) == MAX_FREE_RECTS:
            break
    return kept


def add_geometry(path: Path, extraction: PdfExtraction) -> None:
    """Fill in each page's words, lines, and free space. Running it again starts over."""
    engine = _load_ocr()
    with pymupdf.open(path) as document:
        for page, source in zip(extraction.pages, document):
            page.words, page.lines = [], []
            _read_words(source, page)
            if engine is not None:
                _read_ocr_words(source, page, engine)
            page.free_rects = _free_rects(source, page)


def render_marked_page(
    path: Path, page: Page, zoom: float = MARKED_ZOOM, *,
    marks: list[tuple[str, BBox, bool]] | None = None,
    font_size: float = MARK_FONT_SIZE, clip: pymupdf.Rect | None = None,
) -> bytes:
    """A PNG of the page with a small tag on every line and figure, for the model to refer to.

    Tags are the ids without the page prefix: "l4" is line p3_l4, and "b7" is figure p3_b7.
    Word ids stay out of the image, which they would make unreadable.
    """
    with pymupdf.open(path) as document:
        source = document[page.page - 1]
        tags = marks if marks is not None else [
            (line.line_id.split("_")[1], line.bbox, False)
            for line in page.lines
        ] + [
            (block.block_id.split("_")[1], block.bbox, True)
            for block in page.blocks
            if block.kind == "figure"
        ]
        for tag, box, is_figure in tags:
            x, y = box[0] * page.width, box[1] * page.height
            if is_figure:
                source.draw_rect(
                    pymupdf.Rect(x, y, box[2] * page.width, box[3] * page.height),
                    color=MARK_FIGURE_COLOR,
                    width=1,
                )
            width = pymupdf.get_text_length(tag, fontsize=font_size) + 3
            rect = pymupdf.Rect(x - width, y, x, y + font_size + 2)
            if is_figure:
                rect = pymupdf.Rect(x, y, x + width, y + font_size + 2)
            elif rect.x0 < 0:
                rect = pymupdf.Rect(0, y, width, y + font_size + 2)
            source.draw_rect(
                rect,
                color=None,
                fill=MARK_FIGURE_COLOR if is_figure else MARK_LINE_COLOR,
                overlay=True,
            )
            source.insert_text(
                (rect.x0 + 1.5, rect.y1 - 2.5), tag, fontsize=font_size, color=(1, 1, 1)
            )
        return source.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False).tobytes("png")
