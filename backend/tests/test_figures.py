import json

import pymupdf
import pytest

from app.llm.base import LLMProvider, QuotaExhausted
from app.llm.mock import MOCK_CHART_CAPTION, MOCK_PHOTO_CAPTION
from app.models import Annotations
from app.pipeline import figure_caption
from app.pipeline import run as pipeline
from app.pipeline.page_align import page_text
from app.pipeline.pdf_extract import extract_pdf
from app.samples import FIGURE_SLIDE_TITLE, SLIDES, make_test_pdf

FIGURE_PAGE = next(i for i, (title, _) in enumerate(SLIDES, 1) if title == FIGURE_SLIDE_TITLE)


def figures_on(page):
    return [block for block in page.blocks if block.kind == "figure"]


@pytest.fixture
def sample_pdf(tmp_path):
    path = tmp_path / "slides.pdf"
    make_test_pdf(path)
    return path


def blank_pdf(tmp_path, draw, pages=1):
    document = pymupdf.open()
    for number in range(pages):
        page = document.new_page(width=960, height=540)
        draw(page, number)
    path = tmp_path / "custom.pdf"
    document.save(path)
    return path


def test_finds_the_vector_chart_and_the_image(sample_pdf):
    page = extract_pdf(sample_pdf).pages[FIGURE_PAGE - 1]
    boxes = {tuple(round(value, 2) for value in block.bbox) for block in figures_on(page)}
    assert len(boxes) == 2
    chart = max(figures_on(page), key=lambda block: block.bbox[0])
    photo = min(figures_on(page), key=lambda block: block.bbox[0])
    assert chart.bbox == pytest.approx((540 / 960, 180 / 540, 900 / 960, 460 / 540), abs=0.03)
    assert photo.bbox == pytest.approx((80 / 960, 270 / 540, 280 / 960, 470 / 540), abs=0.01)


def test_pages_without_figures_have_none(sample_pdf):
    assert figures_on(extract_pdf(sample_pdf).pages[0]) == []


def test_figures_do_not_become_titles_or_key_terms(sample_pdf):
    extraction = extract_pdf(sample_pdf)
    page = extraction.pages[FIGURE_PAGE - 1]
    assert page.title == FIGURE_SLIDE_TITLE
    assert all(not block.is_title for block in figures_on(page))
    assert all(block.block_id.startswith(f"p{FIGURE_PAGE}_b") for block in page.blocks)
    assert len({block.block_id for block in page.blocks}) == len(page.blocks)


def test_ignores_tiny_decorations_and_full_page_backgrounds(tmp_path):
    def draw(page, _):
        page.draw_rect(page.rect, fill=(0.95, 0.95, 1), color=None)  # Background.
        page.draw_circle((100, 100), 4, fill=(0, 0, 0))  # Bullet.
        page.draw_line((60, 500), (900, 500))  # Thin rule.

    page = extract_pdf(blank_pdf(tmp_path, draw)).pages[0]
    assert figures_on(page) == []


def test_ignores_a_logo_repeated_across_pages(tmp_path):
    def draw(page, number):
        page.draw_rect(pymupdf.Rect(800, 20, 930, 110), fill=(1, 0, 0))
        page.insert_text((60, 300), f"Different text on slide {number}")

    pages = extract_pdf(blank_pdf(tmp_path, draw, pages=5)).pages
    assert all(figures_on(page) == [] for page in pages)


def test_ignores_a_title_bar_whatever_the_titles_length(tmp_path):
    """A short title makes the bar look empty, and a long one makes it look like a text box."""
    titles = ["GMM", "A rather long title that fills most of the title bar on this slide"] * 3

    def draw(page, number):
        page.draw_rect(pymupdf.Rect(0, 0, 960, 70), fill=(0, 0.2, 0.5), color=None)
        page.insert_text((20, 45), titles[number], fontsize=20, color=(1, 1, 1))

    pages = extract_pdf(blank_pdf(tmp_path, draw, pages=6)).pages
    assert all(figures_on(page) == [] for page in pages)


def test_a_box_full_of_text_is_not_a_figure(tmp_path):
    def draw(page, _):
        rect = pymupdf.Rect(100, 100, 500, 220)
        page.draw_rect(rect, color=(0, 0, 0))
        page.insert_textbox(rect, "Definition text " * 12, fontsize=14)

    assert figures_on(extract_pdf(blank_pdf(tmp_path, draw)).pages[0]) == []


class Recording(LLMProvider):
    name = "recording"
    model = "test"

    def __init__(self, failure=None):
        self.requests = []
        self.failure = failure

    def complete_json(self, prompt):
        raise AssertionError

    def describe_images(self, prompt, images):
        self.requests.append((prompt, images))
        if self.failure:
            raise self.failure
        return json.dumps([f"caption {index}" for index in range(len(images))])


@pytest.fixture
def recording(monkeypatch):
    provider = Recording()
    monkeypatch.setattr(figure_caption, "get_provider", lambda: provider)
    return provider


def extraction_with_captions(pdf):
    extraction = extract_pdf(pdf)
    figure_caption.caption_figures(pdf, extraction)
    return extraction


def captions(extraction):
    return [block.text for page in extraction.pages for block in figures_on(page)]


def test_figures_are_sent_as_png_in_one_batched_request(sample_pdf, recording):
    extraction = extraction_with_captions(sample_pdf)
    assert captions(extraction) == ["caption 0", "caption 1"]
    assert len(recording.requests) == 1
    prompt, images = recording.requests[0]
    assert all(image.startswith(b"\x89PNG") for image in images)
    assert "Measure on real inputs" in prompt


def test_many_figures_use_few_requests(tmp_path, recording):
    def draw(page, number):
        for slot in range(4):
            page.insert_image(
                pymupdf.Rect(40 + slot * 220, 100, 240 + slot * 220, 300),
                pixmap=solid_pixmap((number * 20, slot * 50, 100)),
            )

    extraction_with_captions(blank_pdf(tmp_path, draw, pages=2))
    assert len(recording.requests) == 1


def solid_pixmap(color):
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    pixmap.set_rect(pixmap.irect, color)
    return pixmap


def test_captions_are_cached_by_image_hash(sample_pdf, recording):
    extraction_with_captions(sample_pdf)
    second = extraction_with_captions(sample_pdf)
    assert captions(second) == ["caption 0", "caption 1"]
    assert len(recording.requests) == 1


def test_demo_mode_uses_only_the_cache(sample_pdf, recording, monkeypatch):
    extraction_with_captions(sample_pdf)
    monkeypatch.setenv("DEMO_MODE", "1")
    assert captions(extraction_with_captions(sample_pdf)) == ["caption 0", "caption 1"]

    def draw(page, _):
        page.insert_image(pymupdf.Rect(100, 100, 400, 400), pixmap=solid_pixmap((1, 2, 3)))

    uncached = blank_pdf(sample_pdf.parent, draw)
    assert captions(extraction_with_captions(uncached)) == [""]
    assert len(recording.requests) == 1


@pytest.mark.parametrize("failure", [RuntimeError("boom"), QuotaExhausted("limit")])
def test_a_failed_call_leaves_empty_captions_and_does_not_fail(sample_pdf, monkeypatch, failure):
    provider = Recording(failure)
    monkeypatch.setattr(figure_caption, "get_provider", lambda: provider)
    assert captions(extraction_with_captions(sample_pdf)) == ["", ""]


def test_a_missing_key_leaves_empty_captions(sample_pdf, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert captions(extraction_with_captions(sample_pdf)) == ["", ""]


def test_invalid_caption_reply_is_retried_once_and_then_ignored(sample_pdf, monkeypatch):
    class WrongLength(Recording):
        def describe_images(self, prompt, images):
            self.requests.append(1)
            return json.dumps(["only one"])

    provider = WrongLength()
    monkeypatch.setattr(figure_caption, "get_provider", lambda: provider)
    assert captions(extraction_with_captions(sample_pdf)) == ["", ""]
    assert len(provider.requests) == 2


def test_failure_stops_early_to_protect_the_quota(tmp_path, monkeypatch):
    def draw(page, number):
        for slot in range(4):
            page.insert_image(
                pymupdf.Rect(40 + slot * 220, 100 + number * 20, 240 + slot * 220, 300 + number * 20),
                pixmap=solid_pixmap((number * 20, slot * 50, 100)),
            )

    provider = Recording(QuotaExhausted("limit"))
    monkeypatch.setattr(figure_caption, "get_provider", lambda: provider)
    extraction_with_captions(blank_pdf(tmp_path, draw, pages=6))
    assert len(provider.requests) == 1


def test_captions_feed_page_alignment(sample_pdf, recording):
    page = extraction_with_captions(sample_pdf).pages[FIGURE_PAGE - 1]
    assert "caption 0" in page_text(page)


def test_mock_chart_note_attaches_to_the_figure_block(sample_pdf):
    """The whole pipeline: the transcript line about the chart lands on the chart."""
    job_id = pipeline.create_job(sample_pdf, None)
    pipeline.run_pipeline(job_id)
    folder = pipeline.job_dir(job_id)

    extraction = pipeline._read_extraction(folder)
    figures = figures_on(extraction.pages[FIGURE_PAGE - 1])
    by_caption = {block.text: block for block in figures}
    chart = by_caption[MOCK_CHART_CAPTION]
    assert MOCK_PHOTO_CAPTION in by_caption

    annotations = Annotations.model_validate_json(
        (folder / pipeline.ANNOTATIONS_NAME).read_text(encoding="utf-8")
    )
    page = next(item for item in annotations.pages if item.page == FIGURE_PAGE)
    note = next(item for item in page.notes if "bar chart" in item.note)
    assert note.block_ids == [chart.block_id]
    assert note.bboxes == [chart.bbox]
    assert note.sketch.mark == "circle"
    assert note.sketch.arrow
    # Keeps the lecture's language and its terms, and the speaker's own words are kept too.
    assert note.language == "ko"
    assert "bar chart" in note.note
    assert "running time" in note.quote
    # Timed by its own transcript line, not the whole span.
    assert (note.start, note.end) == (372, 388)
