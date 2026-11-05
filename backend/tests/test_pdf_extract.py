import pymupdf
import pytest

from app.pipeline.pdf_extract import extract_pdf
from app.samples import FOOTER, SLIDES, make_test_pdf


@pytest.fixture
def extraction(tmp_path):
    path = tmp_path / "slides.pdf"
    make_test_pdf(path)
    return extract_pdf(path)


def all_text(page):
    return " ".join(block.text for block in page.blocks)


def test_extracts_each_page_with_stable_block_ids(extraction):
    assert [page.page for page in extraction.pages] == list(range(1, len(SLIDES) + 1))
    first = extraction.pages[0]
    assert first.blocks[0].block_id == "p1_b0"
    assert all(block.page == 1 for block in first.blocks)


def test_bboxes_are_normalized(extraction):
    for page in extraction.pages:
        for block in page.blocks:
            x0, y0, x1, y1 = block.bbox
            assert 0 <= x0 < x1 <= 1
            assert 0 <= y0 < y1 <= 1


def test_bbox_matches_position_on_page(extraction):
    title = next(block for block in extraction.pages[0].blocks if block.is_title)
    # The title is drawn near the top left of the 960x540 page.
    assert title.bbox[0] == pytest.approx(60 / 960, abs=0.01)
    assert title.bbox[1] < 0.2


def test_removes_repeated_footer_and_page_numbers(extraction):
    for page in extraction.pages:
        assert FOOTER not in all_text(page)
        assert str(page.page) not in [block.text for block in page.blocks]


def test_removes_footer_merged_with_changing_page_number(tmp_path):
    document = pymupdf.open()
    for number in range(8, 13):  # Crosses from one-digit to two-digit page numbers.
        page = document.new_page(width=960, height=540)
        page.insert_text((60, 90), f"Slide about topic {chr(55 + number)}", fontsize=36)
        page.insert_text((60, 515), f"Lecture 5 {number}", fontsize=12)
    path = tmp_path / "slides.pdf"
    document.save(path)
    for page in extract_pdf(path).pages:
        assert "Lecture" not in all_text(page)
        assert "Slide about topic" in all_text(page)


def test_marks_largest_font_block_as_title(extraction):
    for page, (title, _) in zip(extraction.pages, SLIDES):
        assert page.title == title
        assert [block.text for block in page.blocks if block.is_title] == [title]


def test_keeps_slide_body_text(extraction):
    assert "Base case: the condition that stops the recursion" in all_text(extraction.pages[0])


def test_marks_low_text_pages(tmp_path):
    path = tmp_path / "slides.pdf"
    body = ["A long enough line of slide text"]
    make_test_pdf(path, [("Questions?", []), ("Recursion", body), ("Stacks", body)])
    pages = extract_pdf(path).pages
    assert pages[0].low_text is True
    assert pages[1].low_text is False


def test_key_terms_start_with_titles_and_include_frequent_terms(extraction):
    titles = [title for title, _ in SLIDES]
    assert extraction.key_terms[: len(titles)] == titles
    assert "case" in extraction.key_terms or "recursion" in extraction.key_terms
    assert "the" not in extraction.key_terms


def test_extracts_korean_text(tmp_path):
    path = tmp_path / "slides.pdf"
    make_test_pdf(path, [("재귀 함수", ["base case에서 재귀가 멈춥니다"])])
    page = extract_pdf(path).pages[0]
    assert page.title == "재귀 함수"
    assert "base case에서 재귀가 멈춥니다" in all_text(page)


def test_page_with_no_text_is_handled(tmp_path):
    document = pymupdf.open()
    document.new_page()
    path = tmp_path / "blank.pdf"
    document.save(path)
    page = extract_pdf(path).pages[0]
    assert page.blocks == []
    assert page.title == ""
    assert page.low_text is True
