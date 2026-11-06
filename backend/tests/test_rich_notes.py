import json

from fastapi.testclient import TestClient

from app.main import app
from app.models import AlignedWindow, Alignment, Block, Page, PdfExtraction, Segment, SpokenWord
from app.pipeline.rich_notes import extract_content, plan_annotations
from app.pipeline import run as pipeline
from app.samples import make_test_pdf


class Provider:
    name = "fixture"
    model = "fixture-v1"

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def complete_json(self, prompt):
        self.calls += 1
        return json.dumps({"items": self.rows})


def fixture():
    text = "Recursion stops at the base case. Without it recursion never stops."
    segments = [Segment(start=0, end=10, text=text)]
    alignment = Alignment(pages=[9], windows=[AlignedWindow(
        window_id=0, start=0, end=10, text=text, page=9, raw_page=9,
        confidence=0.8, similarities=[0.9])])
    extraction = PdfExtraction(pages=[Page(page=9, title="Recursion", blocks=[
        Block(block_id="p9_b0", page=9, text="Recursion", bbox=(0.1, 0.1, 0.5, 0.2))
    ])], key_terms=[])
    row = dict(type="explanation", title="Base case", content=text,
               source_quote=text, short_note="Base case stops recursion",
               block_ids=["p9_b0"], importance=4, language="en")
    return alignment, extraction, segments, row


def test_detailed_content_is_not_truncated_and_density_reuses_it():
    alignment, extraction, segments, row = fixture()
    provider = Provider([row, {**row, "importance": 2, "title": "Reason"}])
    content = extract_content(alignment, extraction, segments, provider)
    assert len(content.items) == 2
    assert content.items[0].content == segments[0].text
    assert content.items[0].segment_ids == [0]
    assert content.coverage["lecture_semantic_coverage"] is None
    minimal = plan_annotations(content, extraction, "minimal")
    detailed = plan_annotations(content, extraction, "detailed")
    assert len(minimal) == 1 and len(detailed) == 2
    assert minimal[0].content_id == content.items[0].id
    cached = extract_content(alignment, extraction, segments, provider)
    assert provider.calls == 1 and cached.items == content.items


def test_rejection_reasons_preserve_strict_grounding_checks():
    alignment, extraction, segments, row = fixture()
    content = extract_content(alignment, extraction, segments, Provider([
        {**row, "source_quote": "unspoken"},
        {**row, "content": "It takes 999 steps"},
        {**row, "block_ids": ["wrong-page"]}, row,
    ]))
    assert len(content.items) == 1
    assert [record.reason for record in content.validation[:3]] == [
        "source_quote_not_found", "unsupported_number", "unknown_block_id"]


def test_word_times_are_used_when_the_quote_matches():
    alignment, extraction, segments, row = fixture()
    segments[0].words = [SpokenWord(start=1, end=2, text="Recursion"),
                         SpokenWord(start=2, end=4, text="stops at the base case.")]
    content = extract_content(alignment, extraction, segments, Provider([
        {**row, "source_quote": "Recursion stops at the base case.",
         "content": "Recursion stops at the base case."}]))
    assert (content.items[0].start, content.items[0].end) == (1, 4)


def test_unavailable_provider_is_reported_without_invented_content():
    alignment, extraction, segments, row = fixture()

    class Broken(Provider):
        def complete_json(self, prompt):
            raise RuntimeError("offline")

    content = extract_content(alignment, extraction, segments, Broken([row]))
    assert not content.complete and not content.items
    assert content.validation[0].status == "unavailable"


def test_density_api_export_and_stale_alignment(tmp_path, monkeypatch):
    pdf = tmp_path / "slides.pdf"
    make_test_pdf(pdf)
    job_id = pipeline.create_job(pdf, audio=None)
    pipeline.run_pipeline(job_id)
    folder = pipeline.job_dir(job_id)
    saved = {name: (folder / name).read_bytes() for name in [
        pipeline.CONTENT_NAME, pipeline.ALIGNMENT_NAME, pipeline.SEGMENTS_NAME]}

    def fail(*args, **kwargs):
        raise AssertionError("Density must not call providers")

    monkeypatch.setattr("app.pipeline.rich_notes.get_provider", fail)
    with TestClient(app) as client:
        content = client.get(f"/api/jobs/{job_id}/detailed-notes").json()
        assert content["items"]
        for density in ("minimal", "balanced", "detailed"):
            annotations = client.get(f"/api/jobs/{job_id}/annotations?density={density}")
            assert annotations.status_code == 200
            assert len(annotations.json()["planning"]) == len(content["items"])
            assert all(note["content_id"] for page in annotations.json()["pages"] for note in page["notes"])
            exported = client.get(f"/api/jobs/{job_id}/annotated.pdf?density={density}")
            assert exported.status_code == 200 and exported.content.startswith(b"%PDF")
        assert client.get(f"/api/jobs/{job_id}/annotations?density=invalid").status_code == 422
        assert all((folder / name).read_bytes() == data for name, data in saved.items())
        alignment = pipeline._read_alignment(folder)
        alignment.windows[0].page = None
        (folder / pipeline.ALIGNMENT_NAME).write_text(alignment.model_dump_json(), encoding="utf-8")
        assert client.get(f"/api/jobs/{job_id}/detailed-notes").status_code == 409
        assert client.get(f"/api/jobs/{job_id}/annotations?density=balanced").status_code == 409
        assert client.get(f"/api/jobs/{job_id}/page-audio").json() == pipeline.build_page_audio(alignment).model_dump()
