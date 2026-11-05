"""Write annotated.pdf: the original slides with notes written on them by hand.

Each page is placed on a wider sheet, leaving gutters to write in. Marks and arrows are ink
annotations, which viewers can select or hide. Margin notes are page content, written with
a handwriting font embedded in the file, so they look right in any viewer.
"""

from pathlib import Path

import pymupdf

from app.models import Annotation, Annotations
from app.pipeline.layout import (
    FONT_PATH,
    fallback_font,
    gutter_width,
    line_needs_fallback,
)

EXPORT_NAME = "annotated.pdf"
HANDWRITING = "pen"
FALLBACK = "cjk"
MARK_WIDTH = 1.6
ARROW_WIDTH = 1.0

# RGB from 0 to 1, per note type: strong enough to read as ink. The frontend matches these.
COLORS: dict[str, tuple[float, float, float]] = {
    "exam_hint": (0.78, 0.16, 0.16),
    "emphasis": (0.70, 0.36, 0.0),
    "explanation": (0.11, 0.30, 0.65),
    "example": (0.12, 0.48, 0.12),
    "question": (0.45, 0.20, 0.65),
}


def _add_ink(page: pymupdf.Page, strokes: list, offset: float, width: float, note: Annotation) -> None:
    if not strokes:
        return
    shifted = [[(x + offset, y) for x, y in stroke] for stroke in strokes]
    ink = page.add_ink_annot(shifted)
    ink.set_colors(stroke=COLORS[note.type])
    ink.set_border(width=width)
    ink.set_info(title=note.type.replace("_", " "), content=note.note)
    ink.update()


def _write_note(page: pymupdf.Page, note: Annotation, offset: float) -> None:
    sketch = note.sketch
    for index, line in enumerate(sketch.lines):
        baseline = sketch.y + index * sketch.line_height + sketch.font_size
        page.insert_text(
            (sketch.x + offset, baseline),
            line,
            fontname=FALLBACK if line_needs_fallback(line) else HANDWRITING,
            fontsize=sketch.font_size,
            color=COLORS[note.type],
        )


def export_annotated_pdf(source: Path, annotations: Annotations, output: Path) -> None:
    notes_by_page = {page.page: page.notes for page in annotations.pages}
    font_bytes = FONT_PATH.read_bytes()
    with pymupdf.open(source) as original, pymupdf.open() as document:
        for index, source_page in enumerate(original):
            width, height = source_page.rect.width, source_page.rect.height
            gutter = gutter_width(width)
            page = document.new_page(width=width + 2 * gutter, height=height)
            page.show_pdf_page(pymupdf.Rect(gutter, 0, gutter + width, height), original, index)
            notes = notes_by_page.get(index + 1, [])
            if not notes:
                continue
            page.insert_font(fontname=HANDWRITING, fontbuffer=font_bytes)
            page.insert_font(fontname=FALLBACK, fontbuffer=fallback_font().buffer)
            for note in notes:
                _write_note(page, note, gutter)
                _add_ink(page, note.sketch.strokes, gutter, MARK_WIDTH, note)
                _add_ink(page, note.sketch.arrow, gutter, ARROW_WIDTH, note)
        document.set_toc(original.get_toc())
        document.subset_fonts()  # Otherwise every export carries the whole font.
        document.save(output, garbage=3, deflate=True)
