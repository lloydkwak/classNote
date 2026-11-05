"""Write annotated.pdf: the original PDF plus highlights and text annotations."""

from pathlib import Path

import pymupdf

from app.models import Annotations, BBox

# RGB from 0 to 1, per note type. The frontend uses matching colors.
COLORS: dict[str, tuple[float, float, float]] = {
    "exam_hint": (1.0, 0.45, 0.45),
    "emphasis": (1.0, 0.7, 0.35),
    "explanation": (1.0, 0.92, 0.4),
    "example": (0.55, 0.9, 0.55),
    "question": (0.6, 0.75, 1.0),
}
ICON_INSET = 22  # Points from the page's right edge, for the note's icon.
ICON_SPACING = 22
EXPORT_NAME = "annotated.pdf"


def _to_page_rect(bbox: BBox, page: pymupdf.Page) -> pymupdf.Rect:
    """Convert a 0-1 box to PDF points, in the page's unrotated coordinates."""
    x0, y0, x1, y1 = bbox
    width, height = page.rect.width, page.rect.height
    return pymupdf.Rect(x0 * width, y0 * height, x1 * width, y1 * height) * page.derotation_matrix


def export_annotated_pdf(source: Path, annotations: Annotations, output: Path) -> None:
    with pymupdf.open(source) as document:
        for page_notes in annotations.pages:
            page = document[page_notes.page - 1]
            for index, note in enumerate(page_notes.notes):
                color = COLORS[note.type]
                title = note.type.replace("_", " ")
                # PDF annotation text is stored as Unicode and drawn by the viewer, so
                # Korean needs no embedded font here.
                info = {"title": title, "content": note.note}
                for bbox in note.bboxes:
                    highlight = page.add_highlight_annot(_to_page_rect(bbox, page))
                    highlight.set_colors(stroke=color)
                    highlight.set_info(info)
                    highlight.update()
                # A note icon too, so a note can be read without hovering over a highlight.
                icon_y = ICON_INSET + index * ICON_SPACING
                point = pymupdf.Point(page.rect.width - ICON_INSET, icon_y) * page.derotation_matrix
                icon = page.add_text_annot(point, note.note, icon="Note")
                icon.set_colors(stroke=color)
                icon.set_info(info)
                icon.update()
        document.save(output, garbage=3, deflate=True)
