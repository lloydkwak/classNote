import json

import pytest

from app.pipeline import run as pipeline
from app.samples import GROUND_TRUTH, make_test_pdf
from eval_alignment import evaluate


@pytest.fixture
def job_id(tmp_path):
    pdf = tmp_path / "slides.pdf"
    make_test_pdf(pdf)
    return pipeline.create_job(pdf, audio=None)


def test_runs_from_pdf_and_mock_transcript_to_alignment(job_id):
    statuses = []
    pipeline.run_pipeline(job_id, on_status=statuses.append)
    assert list(dict.fromkeys(statuses))[:2] == ["extracting_pdf", "transcribing"]

    folder = pipeline.job_dir(job_id)
    for name in ["extraction.json", "segments.json", "windows.json", "alignment.json"]:
        assert (folder / name).exists()

    alignment = pipeline.read_alignment(job_id)
    # The mock embedder is crude, but the transcript's page order must still come through.
    result = evaluate(alignment, GROUND_TRUTH)
    assert result["on_topic_dp_accuracy"] >= 0.7


def test_korean_is_stored_as_readable_json(job_id):
    pipeline.run_pipeline(job_id, to_step="transcribe")
    text = (pipeline.job_dir(job_id) / "segments.json").read_text(encoding="utf-8")
    assert "base case가" in text


def test_annotations_are_replaced_whole_even_if_the_file_is_busy(tmp_path, monkeypatch):
    path = tmp_path / "annotations.json"
    path.write_text("old", encoding="utf-8")
    real_replace = type(path).replace
    calls = []

    def busy_once(self, target):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("in use")
        return real_replace(self, target)

    monkeypatch.setattr(type(path), "replace", busy_once)
    monkeypatch.setattr(pipeline, "REPLACE_RETRY_SECONDS", 0)
    pipeline._write_atomically(path, "new")
    assert path.read_text(encoding="utf-8") == "new"
    assert len(calls) == 2
    assert not path.with_suffix(".tmp").exists()


def test_to_step_stops_early(job_id):
    pipeline.run_pipeline(job_id, to_step="extract")
    folder = pipeline.job_dir(job_id)
    assert (folder / "extraction.json").exists()
    assert not (folder / "segments.json").exists()


def test_from_step_reuses_earlier_outputs(job_id, monkeypatch):
    pipeline.run_pipeline(job_id)
    folder = pipeline.job_dir(job_id)

    # Edit a saved earlier step: a resumed run must read it instead of redoing the work.
    windows = json.loads((folder / "windows.json").read_text(encoding="utf-8"))
    (folder / "windows.json").write_text(json.dumps(windows[:1]), encoding="utf-8")

    def fail(*args, **kwargs):
        raise AssertionError("Earlier steps must not rerun")

    monkeypatch.setattr(pipeline, "extract_pdf", fail)
    monkeypatch.setattr(pipeline, "transcribe", fail)
    statuses = []
    pipeline.run_pipeline(job_id, from_step="align", to_step="align", on_status=statuses.append)
    assert statuses == ["aligning"]
    assert len(pipeline.read_alignment(job_id).windows) == 1
