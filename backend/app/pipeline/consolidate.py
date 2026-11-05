"""Merge duplicate notes, cap notes per page, and build the final annotations."""

from difflib import SequenceMatcher

from app.models import Annotation, Annotations, Note, PageAnnotations, PdfExtraction
from app.pipeline.layout import LayoutNote, gutter_width, layout_page

DEFAULT_MAX_NOTES_PER_PAGE = 5
DUPLICATE_SIMILARITY = 0.7


def _is_duplicate(first: Note, second: Note) -> bool:
    if first.page != second.page or set(first.block_ids) != set(second.block_ids):
        return False
    ratio = SequenceMatcher(None, first.note.casefold(), second.note.casefold()).ratio()
    return ratio >= DUPLICATE_SIMILARITY


def merge_duplicates(notes: list[Note]) -> list[Note]:
    """For notes saying the same thing on the same blocks, keep the most important one."""
    kept: list[Note] = []
    for note in sorted(notes, key=lambda item: -item.importance):
        if not any(_is_duplicate(note, existing) for existing in kept):
            kept.append(note)
    return kept


def consolidate(
    notes: list[Note],
    extraction: PdfExtraction,
    job_id: str,
    max_notes_per_page: int = DEFAULT_MAX_NOTES_PER_PAGE,
) -> Annotations:
    merged = merge_duplicates(notes)
    pages = []
    for page in extraction.pages:
        blocks = {block.block_id: block for block in page.blocks}
        on_page = [note for note in merged if note.page == page.page]
        on_page.sort(key=lambda note: -note.importance)  # Stable, so ties keep their order.
        chosen = sorted(on_page[:max_notes_per_page], key=lambda note: note.start)
        if not chosen:
            continue
        ids = [f"p{page.page}_n{index}" for index in range(len(chosen))]
        sketches = layout_page(
            [
                LayoutNote(
                    id=note_id,
                    text=note.note,
                    double=note.type == "exam_hint",
                    blocks=[blocks[block_id] for block_id in note.block_ids],
                )
                for note_id, note in zip(ids, chosen)
            ],
            page.width,
            page.height,
        )
        pages.append(
            PageAnnotations(
                page=page.page,
                width=page.width,
                height=page.height,
                gutter=gutter_width(page.width),
                notes=[
                    Annotation(
                        id=note_id,
                        block_ids=note.block_ids,
                        bboxes=[blocks[block_id].bbox for block_id in note.block_ids],
                        type=note.type,
                        note=note.note,
                        quote=note.quote,
                        language=note.language,
                        start=note.start,
                        end=note.end,
                        sketch=sketch,
                    )
                    for note_id, note, sketch in zip(ids, chosen, sketches)
                ],
            )
        )
    return Annotations(job_id=job_id, pages=pages)
