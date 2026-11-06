"""Merge duplicate notes, cap notes per page, and build the final annotations."""

from difflib import SequenceMatcher

from app.config import get_settings
from app.models import Annotation, Annotations, Block, Note, PageAnnotations, PdfExtraction
from app.pipeline.layout import LayoutNote, gutter_width, layout_page

DEFAULT_MAX_NOTES_PER_PAGE = 5
DUPLICATE_SIMILARITY = 0.7


def _is_duplicate(first: Note, second: Note) -> bool:
    if first.agent_action or second.agent_action:
        return bool(first.provenance and second.provenance and first.provenance.action_id==second.provenance.action_id)
    if (first.provenance or second.provenance) and first.content_id != second.content_id:
        return False
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
    layout_engine: str = "v1",
    ink_style: str = "natural",
) -> Annotations:
    merged = merge_duplicates(notes)
    font_name = "nanum-pen" if layout_engine=="v2" and ink_style=="natural" else get_settings().note_font
    pages = []
    for page in extraction.pages:
        blocks = {block.block_id: block for block in page.blocks}
        on_page = [note for note in merged if note.page == page.page]
        on_page.sort(key=lambda note: -note.importance)  # Stable, so ties keep their order.
        chosen = sorted(on_page[:max_notes_per_page], key=lambda note: note.start)
        if not chosen:
            continue
        ids = [note.provenance.action_id if note.provenance else f"p{page.page}_n{index}"
               for index,note in enumerate(chosen)]
        def targets(note):
            if note.target_bboxes is not None:
                return [Block(block_id=note.provenance.target_element_id or "unanchored",page=note.page,
                              text="",bbox=box,kind="figure" if note.target_mark in {"circle","bracket"} else "text")
                        for box in note.target_bboxes]
            return [blocks[bid] for bid in note.block_ids if bid in blocks]
        layout_notes = [
                LayoutNote(
                    id=note_id,
                    text=note.note,
                    double=note.type == "exam_hint",
                    blocks=targets(note),
                    mark=note.target_mark if layout_engine=="v2" or note.target_mark in {None,"underline","circle","bracket","none"} else "underline",
                    target_kind=note.provenance.target_kind if note.provenance else None,
                    rotation=page.rotation,
                    agent_action=note.agent_action,
                )
                for note_id, note in zip(ids, chosen)
            ]
        result=None
        if layout_engine=="v2":
            from app.pipeline.layout_v2 import layout_page_v2
            result=layout_page_v2(layout_notes,page,font_name,ink_style)
            sketches=result.sketches
        else:
            sketches=layout_page(layout_notes,page.width,page.height,font_name)
        pages.append(
            PageAnnotations(
                page=page.page,
                width=page.width,
                height=page.height,
                gutter=result.gutter if result else gutter_width(page.width),
                output_height=result.output_height if result else None,
                layout_metrics=result.metrics if result else {},
                notes=[
                    Annotation(
                        content_id=note.content_id,
                        id=note_id,
                        block_ids=note.block_ids,
                        bboxes=[block.bbox for block in targets(note)],
                        type=note.type,
                        note=note.note,
                        quote=note.quote,
                        language=note.language,
                        start=note.start,
                        end=note.end,
                        sketch=sketch,
                        provenance=note.provenance,
                        action_reason=note.action_reason,
                        agent_action=note.agent_action,
                    )
                    for note_id, note, sketch in zip(ids, chosen, sketches)
                ],
            )
        )
    return Annotations(job_id=job_id, pages=pages, note_font=font_name,layout_engine=layout_engine,ink_style=ink_style,
                       title=extraction.pages[0].title if extraction.pages else "")
