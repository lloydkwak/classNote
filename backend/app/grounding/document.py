"""Reuse PDF extraction on an unrotated in-memory working copy; preserve the original file."""

import hashlib
import json
import math
import re
import tempfile
from pathlib import Path

import pymupdf

from app.cache import hash_file
from app.grounding.schema import Element, ElementRegistry, Viewport
from app.models import Page
from app.pipeline import pdf_extract

FORMULA = re.compile(r"[=∑∫∇√∂≤≥≈]|[α-ωΑ-Ω].*[-+*/^]")
SYMBOL = re.compile(r"^[α-ωΑ-Ω∑∫∇√∂≤≥≈=+*/^−-]$")


def registry_fingerprint(registry):
    return hashlib.sha256(json.dumps(registry.model_dump(mode="json"), sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()


def display_box(registry,box):
    rect=pymupdf.Rect(box)*pymupdf.Matrix(registry.width,registry.height)*pymupdf.Matrix(registry.rotation_matrix)
    width,height=(registry.height,registry.width) if registry.rotation in {90,270} else (registry.width,registry.height)
    return (rect.x0/width,rect.y0/height,rect.x1/width,rect.y1/height)


def extract_display_pdf(path, *, geometry=False):
    """Existing extractor on an unrotated copy, converted to the viewer/export coordinate frame."""
    with tempfile.TemporaryDirectory(prefix="classnote-display-") as tmp,pymupdf.open(path) as doc:
        source=Path(tmp)/"unrotated.pdf"
        rotations=[tuple(page.rotation_matrix) for page in doc]
        sizes=[(page.rect.width,page.rect.height) for page in doc]
        for page in doc:
            page.set_rotation(0)
        doc.save(source)
        extraction=pdf_extract.extract_pdf(source)
        if geometry:
            pdf_extract.add_geometry(source,extraction)
        for page,matrix,(width,height) in zip(extraction.pages,rotations,sizes):
            transform=pymupdf.Matrix(page.width,page.height)*pymupdf.Matrix(matrix)
            def convert(box):
                rect=pymupdf.Rect(box)*transform
                return (rect.x0/width,rect.y0/height,rect.x1/width,rect.y1/height)
            for element in [*page.blocks,*page.words,*page.lines]:
                element.bbox=convert(element.bbox)
            page.free_rects=[convert(box) for box in page.free_rects]
            page.width,page.height=width,height
        return extraction


def build_registries(path: Path, *, ocr=False) -> list[ElementRegistry]:
    """IDs match the existing extractor on an equivalent unrotated document.

    OCR is explicitly opt-in, never replaces native words, and its source remains 'ocr'.
    The original source bytes are never saved or annotated in place.
    """
    pdf_hash = hash_file(path)
    with pymupdf.open(path) as original, tempfile.TemporaryDirectory(prefix="classnote-som-") as tmp:
        working_path = Path(tmp) / "unrotated.pdf"
        with pymupdf.open(path) as working:
            for page in working:
                page.set_rotation(0)
            working.save(working_path)
        extraction = pdf_extract.extract_pdf(working_path)
        engine = pdf_extract._load_ocr() if ocr else None
        with pymupdf.open(working_path) as working:
            result = []
            for page, source, native in zip(extraction.pages, working, original):
                # Same existing word/line/OCR routines; free-space is not needed for ID selection.
                pdf_extract._read_words(source, page)
                if engine is not None:
                    pdf_extract._read_ocr_words(source, page, engine)
                elements = []
                diagnostics = []
                if page.low_text:
                    diagnostics.append("low_native_text")
                if ocr and engine is None:
                    diagnostics.append("ocr_unavailable")
                for block in page.blocks:
                    elements.append(Element(id=block.block_id, page=page.page,
                                            kind="figure" if block.kind == "figure" else "block",
                                            bbox=block.bbox, text=block.text, source="pdf"))
                for line in page.lines:
                    elements.append(Element(id=line.line_id, page=page.page,
                                            kind="formula" if FORMULA.search(line.text) else "line",
                                            bbox=line.bbox, text=line.text, source=line.source,
                                            parent_ids=[line.block_id] if line.block_id else [],
                                            recognition="formula_text_heuristic" if FORMULA.search(line.text) else "extracted_line"))
                for word in page.words:
                    elements.append(Element(id=word.word_id, page=page.page,
                                            kind="symbol" if SYMBOL.fullmatch(word.text) else "word",
                                            bbox=word.bbox, text=word.text, source=word.source,
                                            parent_ids=[word.line_id] + ([word.block_id] if word.block_id else []),
                                            recognition="ocr_estimated_word_box" if word.source == "ocr" else "native_pdf"))
                # Native character boxes allow ∇ inside the compound token '∇L'. Never split
                # an OCR word proportionally and pretend its symbol position is PDF geometry.
                native_words = [e for e in elements if e.kind == "word" and e.source == "pdf"]
                char_indices = {}
                for block in source.get_text("rawdict")["blocks"]:
                    if block["type"] != 0:
                        continue
                    for line in block["lines"]:
                        for span in line["spans"]:
                            for char in span["chars"]:
                                rect = pymupdf.Rect(char["bbox"]) & source.rect
                                if rect.is_empty or char["c"].isspace():
                                    continue
                                center = pymupdf.Point((rect.x0+rect.x1)/2/page.width,
                                                       (rect.y0+rect.y1)/2/page.height)
                                parents = [e for e in native_words if pymupdf.Rect(e.bbox).contains(center)]
                                if not parents:
                                    continue
                                parent = min(parents,key=lambda e:pymupdf.Rect(e.bbox).get_area())
                                index = char_indices.get(parent.id,0)
                                char_indices[parent.id] = index+1
                                if not SYMBOL.fullmatch(char["c"]):
                                    continue
                                elements.append(Element(id=f"{parent.id}_c{index}",page=page.page,kind="symbol",
                                                        bbox=(rect.x0/page.width,rect.y0/page.height,
                                                              rect.x1/page.width,rect.y1/page.height),
                                                        text=char["c"],source="pdf",
                                                        parent_ids=[parent.id,*parent.parent_ids],
                                                        recognition="native_pdf_character"))
                # The old extractor deliberately ignores page-sized image backgrounds. Keep real
                # scanned image regions as figures, without inventing OCR words or formula symbols.
                for index, info in enumerate(source.get_image_info()):
                    rect = pymupdf.Rect(info["bbox"]) & source.rect
                    if rect.is_empty or rect.get_area() < source.rect.get_area() * 0.01:
                        continue
                    box = tuple([rect.x0/page.width, rect.y0/page.height, rect.x1/page.width, rect.y1/page.height])
                    if any(e.kind == "figure" and (pymupdf.Rect(e.bbox) & pymupdf.Rect(box)).get_area()
                           >= 0.9 * pymupdf.Rect(box).get_area() for e in elements):
                        continue
                    elements.append(Element(id=f"p{page.page}_image{index}", page=page.page,
                                            kind="figure", bbox=box, text="", source="pdf",
                                            recognition="embedded_image"))
                marked = sorted((e for e in elements if e.kind in {"line", "formula", "figure"}),
                                key=lambda e: (e.bbox[1], e.bbox[0], e.id))
                for number, element in enumerate(marked, 1):
                    element.marker = str(number)
                result.append(ElementRegistry(pdf_sha256=pdf_hash, page=page.page,
                                              width=page.width, height=page.height,
                                              rotation=native.rotation, cropbox=tuple(native.cropbox),
                                              rotation_matrix=tuple(native.rotation_matrix),
                                              elements=elements, diagnostics=diagnostics))
        return result


def render_page(path: Path, registry: ElementRegistry, output: Path, *, zoom=1.5,
                crop=(0, 0, 1, 1)) -> tuple[Path, Path, Viewport]:
    if hash_file(path) != registry.pdf_sha256:
        raise ValueError("PDF changed after registry extraction")
    if not math.isfinite(zoom) or zoom <= 0:
        raise ValueError("zoom must be finite and positive")
    # Validate crop through the same strict nonempty rectangle contract.
    crop = Element(id="crop", page=registry.page, kind="block", bbox=crop, text="", source="pdf").bbox
    output.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(path) as document:
        source = document[registry.page - 1]
        pdf_crop = pymupdf.Rect(crop[0]*registry.width, crop[1]*registry.height,
                               crop[2]*registry.width, crop[3]*registry.height)
        display_clip = pdf_crop * source.rotation_matrix
        pix = source.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=display_clip, alpha=False)
        original_path, marked_path = output / "original.png", output / "som.png"
        pix.save(original_path)
        visible = [e for e in registry.elements if pdf_crop.contains(pymupdf.Rect(
            e.bbox[0]*registry.width, e.bbox[1]*registry.height,
            e.bbox[2]*registry.width, e.bbox[3]*registry.height))]
        # Render using the existing code. Larger numeric marks remain legible after VLM resizing.
        page = Page(page=registry.page, title="", blocks=[], width=registry.width, height=registry.height)
        marks = [(e.marker, e.bbox, e.kind == "figure") for e in visible if e.marker is not None]
        marked_path.write_bytes(pdf_extract.render_marked_page(
            path, page, zoom=zoom, marks=marks, font_size=12, clip=display_clip))
        transform = (pymupdf.Matrix(registry.width, registry.height) * source.rotation_matrix
                     * pymupdf.Matrix(zoom, zoom) * pymupdf.Matrix(1, 0, 0, 1, -pix.x, -pix.y))
        viewport = Viewport(pdf_normalized_to_pixels=tuple(transform), width=pix.width, height=pix.height,
                            crop=crop, visible_element_ids=[e.id for e in visible])
        return original_path, marked_path, viewport


def box_in_pixels(box, viewport):
    return tuple(pymupdf.Rect(box) * pymupdf.Matrix(viewport.pdf_normalized_to_pixels))


def pixels_to_pdf_box(box, viewport, registry):
    """Image/crop pixels → original unrotated crop-local PDF points, never model coordinates."""
    x0,y0,x1,y1 = box
    if not all(math.isfinite(v) for v in box) or not (
        -0.001 <= x0 < x1 <= viewport.width + 0.001 and
        -0.001 <= y0 < y1 <= viewport.height + 0.001
    ):
        raise ValueError("pixel rectangle outside image")
    normalized = pymupdf.Rect(box) * ~pymupdf.Matrix(viewport.pdf_normalized_to_pixels)
    return tuple(normalized * pymupdf.Matrix(registry.width, registry.height))
