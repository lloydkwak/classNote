import builtins

import pymupdf
import pytest

from app.models import Segment, SpokenWord
from app.pipeline import pdf_extract
from app.pipeline.pdf_extract import add_geometry, extract_pdf, render_marked_page


_REAL_LOAD_OCR = pdf_extract._load_ocr


@pytest.fixture(autouse=True)
def no_ocr(monkeypatch):
    """Most tests are about the PDF's own text, and OCR is slow and optional."""
    monkeypatch.setattr(pdf_extract, "_load_ocr", lambda: None)


def _make_pdf(path, fill=None):
    document = pymupdf.open()
    for title, topic in (
        ("Slide title here", "things"),
        ("Another heading now", "cats"),
        ("Third heading appears", "dogs"),
        ("Last heading shown", "birds"),
    ):
        page = document.new_page(width=720, height=540)
        if fill:
            page.draw_rect(page.rect, color=None, fill=fill)
        page.insert_text((50, 60), title, fontsize=24)
        page.insert_text((50, 110), f"• first point about {topic}", fontsize=16)
        page.insert_text((50, 140), f"second point about {topic}", fontsize=16)
        page.insert_text((300, 530), "Footer repeated", fontsize=8)
    document.save(path)
    return path


def _extracted(path):
    extraction = extract_pdf(path)
    add_geometry(path, extraction)
    return extraction


def test_words_have_ids_lines_and_normalized_boxes(tmp_path):
    page = _extracted(_make_pdf(tmp_path / "a.pdf")).pages[0]
    assert page.words[0].word_id == "p1_w0"
    assert page.words[0].line_id == "p1_l0"
    assert [word.text for word in page.words[:3]] == ["Slide", "title", "here"]
    assert page.lines[0].text == "Slide title here"
    for word in page.words:
        assert all(0 <= value <= 1 for value in word.bbox)
        assert word.source == "pdf"
    # Each word belongs to the line that lists it.
    lines = {line.line_id: line for line in page.lines}
    assert all(word.word_id in lines[word.line_id].word_ids for word in page.words)


def test_skips_bullets_and_repeated_footers(tmp_path):
    page = _extracted(_make_pdf(tmp_path / "a.pdf")).pages[0]
    texts = [word.text for word in page.words]
    assert "•" not in texts
    assert "first" in texts
    assert "Footer" not in texts


def test_word_boxes_match_the_page(tmp_path):
    page = _extracted(_make_pdf(tmp_path / "a.pdf")).pages[0]
    title = page.words[0]
    assert title.bbox[0] == pytest.approx(50 / 720, abs=0.01)
    assert title.bbox[3] < 0.15


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


@pytest.mark.parametrize("fill", [None, (0.9, 0.95, 1.0)])
def test_free_rects_avoid_all_ink(tmp_path, fill):
    """Coloured backgrounds count as empty, and text, including footers, does not."""
    page = _extracted(_make_pdf(tmp_path / "a.pdf", fill)).pages[0]
    assert page.free_rects
    biggest = page.free_rects[0]
    assert (biggest[2] - biggest[0]) * (biggest[3] - biggest[1]) > 0.3
    footer = (300 / 720, 520 / 540, 400 / 720, 532 / 540)
    for rect in page.free_rects:
        assert not any(_overlaps(rect, word.bbox) for word in page.words)
        assert not _overlaps(rect, footer)
        assert all(0 <= value <= 1 for value in rect)


def test_free_rects_avoid_figures(tmp_path):
    path = tmp_path / "a.pdf"
    document = pymupdf.open()
    page = document.new_page(width=720, height=540)
    page.insert_text((50, 60), "A slide with a white image", fontsize=20)
    page.draw_rect(pymupdf.Rect(300, 200, 600, 450), color=(0, 0, 0))
    document.save(path)
    extraction = extract_pdf(path)
    add_geometry(path, extraction)
    extraction.pages[0].blocks.append(
        extraction.pages[0].blocks[0].model_copy(
            update={"block_id": "p1_b9", "kind": "figure", "bbox": (0.1, 0.5, 0.3, 0.8), "text": ""}
        )
    )
    add_geometry(path, extraction)
    for rect in extraction.pages[0].free_rects:
        assert not _overlaps(rect, (0.1, 0.5, 0.3, 0.8))


def test_running_again_starts_over(tmp_path):
    path = _make_pdf(tmp_path / "a.pdf")
    extraction = _extracted(path)
    count = len(extraction.pages[0].words)
    add_geometry(path, extraction)
    assert len(extraction.pages[0].words) == count


def test_marked_page_is_a_png_and_leaves_the_file_alone(tmp_path):
    path = _make_pdf(tmp_path / "a.pdf")
    before = path.read_bytes()
    extraction = _extracted(path)
    image = render_marked_page(path, extraction.pages[0], zoom=1)
    assert image.startswith(b"\x89PNG")
    assert path.read_bytes() == before


class _FakeOcr:
    def __call__(self, png):
        class Result:
            boxes = [[[20, 20], [220, 20], [220, 60], [20, 60]]]
            txts = ["이미지 안의 글자"]
            scores = [0.95]

        return Result()


def test_ocr_words_are_marked_and_come_after_pdf_words(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_extract, "_load_ocr", lambda: _FakeOcr())
    path = tmp_path / "scan.pdf"
    document = pymupdf.open()
    document.new_page(width=720, height=540).insert_text((50, 60), "Hi", fontsize=20)
    document.save(path)
    extraction = extract_pdf(path)
    assert extraction.pages[0].low_text
    add_geometry(path, extraction)
    words = extraction.pages[0].words
    assert [word.source for word in words] == ["pdf", "ocr", "ocr", "ocr"]
    assert [word.text for word in words[1:]] == ["이미지", "안의", "글자"]
    # Boxes come back in page coordinates and don't overlap.
    assert words[1].bbox[0] == pytest.approx(10 / 720)
    assert words[1].bbox[2] <= words[2].bbox[0]
    assert words[3].line_id == words[1].line_id


def test_low_confidence_ocr_is_dropped(tmp_path, monkeypatch):
    class Unsure(_FakeOcr):
        def __call__(self, png):
            result = super().__call__(png)
            result.scores = [0.2]
            return result

    monkeypatch.setattr(pdf_extract, "_load_ocr", lambda: Unsure())
    path = tmp_path / "scan.pdf"
    document = pymupdf.open()
    document.new_page(width=720, height=540).insert_text((50, 60), "Hi", fontsize=20)
    document.save(path)
    extraction = extract_pdf(path)
    add_geometry(path, extraction)
    assert [word.source for word in extraction.pages[0].words] == ["pdf"]


def test_ocr_is_optional(monkeypatch):
    _REAL_LOAD_OCR.cache_clear()
    real_import = builtins.__import__

    def without_rapidocr(name, *args, **kwargs):
        if name == "rapidocr":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_rapidocr)
    assert _REAL_LOAD_OCR() is None
    _REAL_LOAD_OCR.cache_clear()


def test_shifting_a_segment_moves_its_words():
    segment = Segment(start=1, end=2, text="a", words=[SpokenWord(start=1, end=2, text="a")])
    shifted = segment.shifted(10)
    assert (shifted.start, shifted.end) == (11, 12)
    assert (shifted.words[0].start, shifted.words[0].end) == (11, 12)
    assert segment.words[0].start == 1
