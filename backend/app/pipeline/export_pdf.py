"""Write annotated.pdf: the original slides with notes written on them by hand.

Each page is placed on a wider sheet, leaving gutters to write in. Marks and arrows are ink
annotations, which viewers can select or hide. Margin notes are page content, written with
a font embedded in the file, so they look right in any viewer.
"""

from pathlib import Path
import json

import pymupdf

from app.models import Annotation, Annotations
from app.pipeline.layout import (
    fallback_font,
    gutter_width,
    load_note_font,
    run_width,
    text_runs,
)

EXPORT_NAME = "annotated.pdf"
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
    content=note.note if note.provenance is None else json.dumps(
        {"annotation_id":note.id,"quote":note.quote,"note":note.note,
         "provenance":note.provenance.model_dump(mode="json")},ensure_ascii=False)
    ink.set_info(title=note.type.replace("_", " "), content=content,
                 subject=note.provenance.action_id if note.provenance else "")
    ink.update()


def _write_note(page: pymupdf.Page, note: Annotation, offset: float, font_name: str) -> None:
    """Write with TextWriter: insert_text printed "∴" as "B" when a run held only that symbol."""
    sketch = note.sketch
    writer = pymupdf.TextWriter(page.rect)
    for index, line in enumerate(sketch.lines):
        baseline = sketch.y + index * sketch.line_height + sketch.font_size
        x = sketch.x + offset
        for run, fallback in text_runs(line, font_name):
            font = fallback_font() if fallback else load_note_font(font_name)
            writer.append((x, baseline), run, font=font, fontsize=sketch.font_size)
            x += run_width(run, fallback, font_name, sketch.font_size)
    writer.write_text(page, color=COLORS[note.type])


def export_annotated_pdf(source: Path, annotations: Annotations, output: Path) -> None:
    notes_by_page = {page.page: page.notes for page in annotations.pages}
    font_name = annotations.note_font
    with pymupdf.open(source) as original, pymupdf.open() as document:
        for index, source_page in enumerate(original):
            width, height = source_page.rect.width, source_page.rect.height
            rotation=source_page.rotation
            source_page.set_rotation(0)  # In-memory only: show_pdf_page otherwise ignores /Rotate.
            gutter = gutter_width(width)
            page = document.new_page(width=width + 2 * gutter, height=height)
            page.show_pdf_page(pymupdf.Rect(gutter, 0, gutter + width, height), original, index,rotate=-rotation)
            notes = notes_by_page.get(index + 1, [])
            if not notes:
                continue
            for note in notes:
                _write_note(page, note, gutter, font_name)
                _add_ink(page, note.sketch.strokes, gutter, MARK_WIDTH, note)
                _add_ink(page, note.sketch.arrow, gutter, ARROW_WIDTH, note)
        document.set_toc(original.get_toc())
        provenance=[{"annotation_id":note.id,"quote":note.quote,"note":note.note,
                     "provenance":note.provenance.model_dump(mode="json")}
                    for page in annotations.pages for note in page.notes if note.provenance]
        if provenance:
            document.embfile_add("classnote-evidence.json",json.dumps(
                {"schema_version":1,"job_id":annotations.job_id,"evidence":annotations.grounding,
                 "annotations":provenance},ensure_ascii=False).encode())
        document.subset_fonts()  # Otherwise every export carries the whole font.
        document.save(output, garbage=3, deflate=True)
