import pymupdf
import pytest

from app.models import Block, Note, Page, PdfExtraction
from app.pipeline.consolidate import consolidate
from app.pipeline.export_pdf import COLORS, export_annotated_pdf
from app.pipeline.layout import gutter_width
from app.samples import SLIDES, make_test_pdf

KOREAN = "이 base case는 시험에 꼭 나옵니다."
WIDTH, HEIGHT = 960, 540


def note(text, kind="exam_hint", block_ids=("p2_b0",)):
    return Note(
        page=2,
        block_ids=list(block_ids),
        type=kind,
        note=text,
        quote=text,
        language="ko",
        importance=3,
        start=0,
        end=1,
    )


EXTRACTION = PdfExtraction(
    pages=[
        Page(
            page=2,
            title="Title",
            width=WIDTH,
            height=HEIGHT,
            blocks=[
                Block(block_id="p2_b0", page=2, text="Text", bbox=(0.1, 0.2, 0.5, 0.3)),
                Block(
                    block_id="p2_b1", page=2, text="Chart", bbox=(0.55, 0.3, 0.9, 0.85), kind="figure"
                ),
            ],
        )
    ],
    key_terms=[],
)


@pytest.fixture
def exported(tmp_path):
    source = tmp_path / "slides.pdf"
    make_test_pdf(source)
    annotations = consolidate(
        [
            note(KOREAN),
            note("A note about the chart", kind="example", block_ids=["p2_b1"]),
            note("A page-level question", kind="question", block_ids=[]),
        ],
        EXTRACTION,
        "job",
    )
    output = tmp_path / "annotated.pdf"
    export_annotated_pdf(source, annotations, output)
    return output


def inks(page):
    return [annot for annot in page.annots() if annot.type[1] == "Ink"]


def test_every_page_is_kept_with_gutters_added(exported):
    with pymupdf.open(exported) as document:
        assert len(document) == len(SLIDES)
        assert document[1].rect.width == pytest.approx(WIDTH + 2 * gutter_width(WIDTH))
        assert document[1].rect.height == pytest.approx(HEIGHT)


def test_original_slide_text_stays_in_place(exported):
    with pymupdf.open(exported) as document:
        found = document[1].search_for("Factorial and the Call Stack")
        assert found
        assert found[0].x0 == pytest.approx(gutter_width(WIDTH) + 60, abs=3)


def test_marks_and_arrows_are_ink_only_on_annotated_pages(exported):
    with pymupdf.open(exported) as document:
        assert list(document[0].annots()) == []
        kinds = {annot.type[1] for annot in document[1].annots()}
        assert kinds == {"Ink"}
        # Two notes have a mark and an arrow. The page-level note has neither.
        assert len(inks(document[1])) == 4


def test_ink_uses_the_note_types_color(exported):
    with pymupdf.open(exported) as document:
        colors = [annot.colors["stroke"] for annot in inks(document[1])]
    assert any(color == pytest.approx(COLORS["exam_hint"], abs=0.01) for color in colors)
    assert any(color == pytest.approx(COLORS["example"], abs=0.01) for color in colors)


def test_korean_margin_notes_are_real_text_in_the_gutters(exported):
    with pymupdf.open(exported) as document:
        page = document[1]
        words = page.get_text("words")
        margin = [word for word in words if word[2] <= gutter_width(WIDTH)]
        text = " ".join(word[4] for word in margin)
        assert "시험에" in text
        contents = " ".join(annot.info["content"] for annot in inks(page))
        assert KOREAN in contents


def test_note_font_is_embedded_as_a_subset(exported):
    with pymupdf.open(exported) as document:
        fonts = document.get_page_fonts(1)
        assert any("Pretendard" in font[3] for font in fonts)
    assert exported.stat().st_size < 3_000_000


def test_symbols_come_out_as_real_text_in_the_margin(tmp_path):
    source = tmp_path / "slides.pdf"
    make_test_pdf(source)
    text = "stack ≪ heap → 빠름 ≈ O(1) ∴ 사용"
    annotations = consolidate(
        [note(text, kind="explanation"), note("★ 시험: 나옴", kind="exam_hint", block_ids=[])],
        EXTRACTION,
        "job",
    )
    output = tmp_path / "annotated.pdf"
    export_annotated_pdf(source, annotations, output)
    with pymupdf.open(output) as document:
        page_text = " ".join(document[1].get_text().split())
    for symbol in ["≪", "→", "≈", "∴", "★", "빠름", "stack"]:
        assert symbol in page_text


def test_the_font_setting_is_honored(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTE_FONT", "nanum-pen")
    source = tmp_path / "slides.pdf"
    make_test_pdf(source)
    annotations = consolidate([note("글자 확인", kind="explanation")], EXTRACTION, "job")
    assert annotations.note_font == "nanum-pen"
    output = tmp_path / "annotated.pdf"
    export_annotated_pdf(source, annotations, output)
    with pymupdf.open(output) as document:
        assert any("Nanum" in font[3] for font in document.get_page_fonts(1))


def test_figure_gets_a_circle_near_its_position(exported):
    with pymupdf.open(exported) as document:
        page = document[1]
        chart = next(
            annot
            for annot in inks(page)
            if annot.info["content"] == "A note about the chart" and len(annot.vertices) == 1
        )
        xs = [x for x, _ in chart.vertices[0]]
        ys = [y for _, y in chart.vertices[0]]
    gutter = gutter_width(WIDTH)
    assert min(xs) <= gutter + 0.55 * WIDTH
    assert max(xs) >= gutter + 0.9 * WIDTH
    assert min(ys) <= 0.3 * HEIGHT
    assert max(ys) >= 0.85 * HEIGHT


def test_rotated_page_keeps_its_visual_orientation(tmp_path):
    source = tmp_path / "rotated.pdf"
    document = pymupdf.open()
    page = document.new_page(width=800, height=600)
    page.insert_text((50, 50), "Rotated slide")
    page.set_rotation(90)
    document.save(source)
    document.close()

    extraction = PdfExtraction(pages=[], key_terms=[])
    output = tmp_path / "annotated.pdf"
    export_annotated_pdf(source, consolidate([], extraction, "job"), output)
    with pymupdf.open(output) as result:
        assert result[0].rect.height == pytest.approx(800)
        assert result[0].rect.width == pytest.approx(600 + 2 * gutter_width(600))
