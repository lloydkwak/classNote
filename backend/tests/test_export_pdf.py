import pymupdf
import pytest

from app.models import Annotation, Annotations, PageAnnotations
from app.pipeline.export_pdf import COLORS, export_annotated_pdf
from app.samples import make_test_pdf

KOREAN = "이 base case는 시험에 꼭 나옵니다."


def annotation(index, bboxes, kind="exam_hint", text=KOREAN):
    return Annotation(
        id=f"n{index}",
        block_ids=[],
        bboxes=bboxes,
        type=kind,
        note=text,
        language="ko",
        start=0,
        end=1,
    )


def highlighted_area(highlight):
    """The exact area from the quad's vertices. `.rect` also includes drawing padding."""
    xs, ys = zip(*highlight.vertices)
    return pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))


@pytest.fixture
def exported(tmp_path):
    source = tmp_path / "slides.pdf"
    make_test_pdf(source)
    annotations = Annotations(
        job_id="job",
        pages=[
            PageAnnotations(
                page=2,
                notes=[
                    annotation(0, [(0.1, 0.2, 0.5, 0.3)]),
                    annotation(1, [], kind="question", text="A page-level question"),
                ],
            )
        ],
    )
    output = tmp_path / "annotated.pdf"
    export_annotated_pdf(source, annotations, output)
    return output


def test_annotations_go_on_the_right_page_only(exported):
    with pymupdf.open(exported) as document:
        assert len(document) == 5
        assert list(document[0].annots()) == []
        kinds = [annot.type[1] for annot in document[1].annots()]
    assert sorted(kinds) == ["Highlight", "Text", "Text"]


def test_highlight_uses_the_denormalized_bbox(exported):
    with pymupdf.open(exported) as document:
        page = document[1]
        highlight = next(annot for annot in page.annots() if annot.type[1] == "Highlight")
        rect = highlighted_area(highlight)
        assert rect.x0 == pytest.approx(0.1 * page.rect.width, abs=3)
        assert rect.y0 == pytest.approx(0.2 * page.rect.height, abs=3)
        assert rect.x1 == pytest.approx(0.5 * page.rect.width, abs=3)


def test_color_depends_on_the_note_type(exported):
    with pymupdf.open(exported) as document:
        highlight = next(a for a in document[1].annots() if a.type[1] == "Highlight")
        assert highlight.colors["stroke"] == pytest.approx(COLORS["exam_hint"], abs=0.01)


def test_korean_text_survives_saving(exported):
    with pymupdf.open(exported) as document:
        contents = [annot.info["content"] for annot in document[1].annots()]
    assert KOREAN in contents
    assert "A page-level question" in contents


def test_rotated_page_highlights_the_same_visual_spot(tmp_path):
    source = tmp_path / "rotated.pdf"
    document = pymupdf.open()
    page = document.new_page(width=800, height=600)
    page.set_rotation(90)
    document.save(source)
    document.close()
    annotations = Annotations(
        job_id="job",
        pages=[PageAnnotations(page=1, notes=[annotation(0, [(0.1, 0.1, 0.3, 0.2)])])],
    )
    output = tmp_path / "annotated.pdf"
    export_annotated_pdf(source, annotations, output)
    with pymupdf.open(output) as result:
        page = result[0]
        highlight = next(a for a in page.annots() if a.type[1] == "Highlight")
        visual = highlighted_area(highlight) * page.rotation_matrix
        assert visual.x0 == pytest.approx(0.1 * page.rect.width, abs=3)
        assert visual.y0 == pytest.approx(0.1 * page.rect.height, abs=3)
