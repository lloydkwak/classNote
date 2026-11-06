import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.evaluation.experiments import replay
from app.evaluation.metrics import alignment_metrics, notes_metrics
from app.evaluation.report import evaluate_job
from app.evaluation.schema import EvaluationDataset, Interval, MatchReview, MeaningUnit, Review, Transition
from app.evaluation.storage import import_legacy, load_dataset, save_dataset
from app.main import app
from app.models import AlignedWindow, Alignment, Annotations, PdfExtraction, Segment
from app.pipeline import run as pipeline
from app.pipeline.rich_notes import ContentItem, DetailedNotes
from app.samples import make_test_pdf


def human():
    return Review(status="verified", source="human", author="unit-test actor only",
                  reviewed_at="2026-11-06T00:00:00Z")


def label(identifier, start, end, pages, review=None):
    return Interval(id=identifier, start=start, end=end, pages=pages, review=review or human())


def alignment(rows):
    return Alignment(pages=[1, 2, 3], windows=[AlignedWindow(
        window_id=index, start=start, end=end, page=page, raw_page=page or 1,
        confidence=.5, similarities=[.8, .85, .81], text="fixture")
        for index, (start, end, page) in enumerate(rows)])


def dataset(**kwargs):
    return EvaluationDataset(dataset_id="synthetic", lecture_id="synthetic", **kwargs)


def test_verified_requires_explicit_human_metadata():
    for source in ("system", "synthetic", "legacy_inference"):
        with pytest.raises(ValidationError):
            Review(status="verified", source=source, author="agent", reviewed_at="2026-11-06T00:00:00Z")
    with pytest.raises(ValidationError):
        Review(status="verified", source="human")
    with pytest.raises(ValidationError):
        Review(status="verified", author="human", reviewed_at="not-a-date")


def test_dataset_rejects_overlapping_labels_and_unknown_unit_links():
    with pytest.raises(ValidationError):
        dataset(alignment_labels=[label("a", 0, 20, [1]), label("b", 10, 30, [2])])
    with pytest.raises(ValidationError):
        dataset(matches=[MatchReview(item_id="item", unit_ids=["missing"])])


def test_metrics_weight_only_labeled_intersection_not_whole_windows():
    data = dataset(alignment_labels=[label("a", 2, 4, [1]), label("b", 4, 10, [2, 3])])
    result = alignment_metrics(alignment([(0, 10, 1)]), data)
    assert result["exact_page_accuracy"] == .25
    assert result["within_one_page_accuracy"] == 1
    assert result["evaluated_seconds"] == 8
    assert result["first_page_accuracy"] is None


def test_provisional_and_uncertain_never_score_by_default():
    data = dataset(alignment_labels=[label("a", 0, 10, [1], Review(status="provisional")),
                                    label("b", 10, 20, [2], Review(status="uncertain"))])
    prediction = alignment([(0, 10, 1), (10, 20, 2)])
    assert alignment_metrics(prediction, data)["exact_page_accuracy"] is None
    assert alignment_metrics(prediction, data, True)["evaluated_seconds"] == 10


def test_transition_boundary_revisit_and_off_topic_metrics():
    data = dataset(alignment_labels=[label("a", 0, 10, [1]), label("b", 10, 20, [2]),
                                    label("c", 20, 30, [1]), label("d", 30, 40, [])],
                   transitions=[Transition(id="back", time=20, from_pages=[2], to_pages=[1],
                                           revisit=True, review=human())])
    result = alignment_metrics(alignment([(0, 11, 1), (11, 22, 2), (22, 30, 1), (30, 40, None)]), data)
    assert result["transition_accuracy"] == 1
    assert result["boundary_mae_seconds"] == 2
    assert result["revisit_accuracy"] == 1
    assert result["off_topic_precision"] == result["off_topic_recall"] == 1
    assert result["first_page_accuracy"] == 1
    assert result["confidence_bins"][2]["error_rate"] == pytest.approx(3 / 40)


def test_prediction_gaps_are_not_transitions_and_empty_denominators_are_null():
    data = dataset(transitions=[Transition(id="t", time=10, from_pages=[1], to_pages=[2], review=human())])
    result = alignment_metrics(alignment([(0, 9, 1), (11, 20, 2)]), data)
    assert result["transition_accuracy"] == 0
    assert result["boundary_mae_seconds"] is None
    assert result["exact_page_accuracy"] is None


def test_one_predicted_transition_cannot_match_two_labels():
    data = dataset(transitions=[Transition(id=str(index), time=time, from_pages=[1], to_pages=[2], review=human())
                                for index, time in enumerate([9, 11])])
    assert alignment_metrics(alignment([(0, 10, 1), (10, 20, 2)]), data)["transition_accuracy"] == .5


def test_missing_first_topic_does_not_make_first_page_correct():
    data = dataset(alignment_labels=[label("a", 0, 10, [1]), label("b", 10, 20, [2])])
    assert alignment_metrics(alignment([(0, 10, None), (10, 20, 2)]), data)["first_page_accuracy"] == 0


def test_notes_metrics_require_human_matches_and_exhaustive_review_scope():
    unit = MeaningUnit(id="u", start=0, end=10, pages=[1], content="A fact", source_quote="A fact", review=human(), importance=4)
    item = ContentItem(id="i", page=1, type="definition", title="Fact", content="A fact", source_quote="A fact",
                       short_note="fact", start=0, end=10, segment_ids=[0], confidence=.9)
    content = DetailedNotes(items=[item])
    data = dataset(meaning_units=[unit], meaning_review_ranges=[label("range", 0, 10, [])])
    segments = [Segment(start=0, end=10, text="A fact")]
    pending = notes_metrics(content, None, segments, data)
    assert pending["extraction_recall"] is None
    assert pending["extraction_precision_reviewed"] is None
    assert pending["matching_suggestions"][0]["automatic_match_is_verified"] is False
    data.matches = [MatchReview(item_id="i", unit_ids=["u"], relevant=True, entailed=False, review=human())]
    result = notes_metrics(content, None, segments, data)
    assert result["extraction_recall"] == 1
    assert result["extraction_precision_reviewed"] == 1
    assert result["semantic_factuality_reviewed"] == 0
    assert result["quote_match_rate"] == 1
    assert result["important_pdf_recall"] == 0


@pytest.fixture
def job(tmp_path):
    pdf = tmp_path / "input.pdf"
    make_test_pdf(pdf)
    job_id = pipeline.create_job(pdf, None)
    pipeline.run_pipeline(job_id)
    return pipeline.job_dir(job_id)


def test_storage_reloads_archives_and_rejects_stale_revisions(job):
    data = load_dataset(job)
    saved = save_dataset(job, data)
    assert saved.revision == load_dataset(job).revision == 1
    assert (job / "evaluation" / "labels-r000001.json").exists()
    with pytest.raises(ValueError, match="Revision conflict"):
        save_dataset(job, data)
    saved.inputs = {}
    with pytest.raises(ValueError, match="Input identities"):
        save_dataset(job, saved)


def test_legacy_import_preserves_unverified_provenance(job):
    data = import_legacy([{"start": 0, "end": 10, "pages": [1]}], job)
    assert data.alignment_labels[0].review.status == "provisional"
    assert data.alignment_labels[0].review.source == "legacy_inference"
    assert not alignment_metrics(pipeline._read_alignment(job), data)["verified_label_count"]


def test_developer_api_gated_and_requires_confirmation(job, monkeypatch):
    client = TestClient(app)
    url = f"/api/jobs/{job.name}/evaluation"
    monkeypatch.delenv("EVALUATION_MODE", raising=False)
    assert client.get(url).status_code == 404
    monkeypatch.setenv("EVALUATION_MODE", "1")
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()["dataset"]
    data["alignment_labels"] = [label("a", 0, 10, [1]).model_dump()]
    assert client.put(url, json={"dataset": data}).status_code == 422
    saved = client.put(url, json={"dataset": data, "confirm_human_verification": True})
    assert saved.status_code == 200
    assert client.put(url, json={"dataset": data, "confirm_human_verification": True}).status_code == 409
    assert client.get(url).json()["dataset"] == saved.json()


def test_benchmark_reports_provenance_without_touching_predictions(job, tmp_path):
    before = (job / "alignment.json").read_bytes()
    result = evaluate_job(job, load_dataset(job))
    assert set(result["alignment"]) == {"saved", "current", "hybrid", "hybrid_refined"}
    assert result["dataset_sha256"] and result["git"]["commit"]
    assert result["alignment"]["hybrid"]["metrics"]["exact_page_accuracy"] is None
    assert not result["generalization_claim"]
    assert (job / "alignment.json").read_bytes() == before
    assert result["notes"]["telemetry"]["provider_calls"] > 0
    import shutil

    notes_run = tmp_path / "notes-run"
    notes_run.mkdir()
    for name in ("detailed_notes.json", "annotations.json"):
        shutil.copyfile(job / name, notes_run / name)
    from app.cache import hash_file

    (notes_run / "smoke.json").write_text(json.dumps({"source_job": job.name, "source_hashes": {
        name: hash_file(job / name) for name in ("alignment.json", "extraction.json", "segments.json")
    }}), encoding="utf-8")
    assert evaluate_job(job, load_dataset(job), notes_folder=notes_run)["notes"]["source"] == str(notes_run)
    (notes_run / "smoke.json").write_text(json.dumps({"source_job": "different"}), encoding="utf-8")
    with pytest.raises(ValueError, match="do not match"):
        evaluate_job(job, load_dataset(job), notes_folder=notes_run)


def test_cache_hits_are_not_counted_as_api_calls(job):
    pipeline._step_notes(job)
    content = DetailedNotes.model_validate_json((job / "detailed_notes.json").read_text(encoding="utf-8"))
    assert content.telemetry["provider_calls"] == 0
    assert content.telemetry["cache_hits"] > 0


def test_experiments_are_opt_in_and_preserve_saved_artifact(job):
    original = pipeline._read_alignment(job)
    extraction = pipeline._read_extraction(job)
    segments = pipeline._read_list(job / "segments.json", Segment)
    before = original.model_dump_json()
    hybrid = replay(original, extraction, segments, "hybrid", lexical_weight=10,
                    glossary={"베이스 케이스": "base case"})
    assert hybrid.pages == original.pages
    assert original.model_dump_json() == before
    assert replay(original, extraction, segments, "saved").model_dump_json() == before


def test_selected_refinement_snaps_only_low_confidence_page_changes():
    from app.models import Page

    original = Alignment(pages=[1, 2, 3], windows=[
        AlignedWindow(window_id=0, start=0, end=10, page=1, raw_page=1, confidence=0,
                      similarities=[.81, .809, .78], text=""),
        AlignedWindow(window_id=1, start=10, end=20, page=2, raw_page=2, confidence=0,
                      similarities=[.809, .81, .78], text=""),
    ])
    extraction = PdfExtraction(pages=[Page(page=number, title="", blocks=[]) for number in (1, 2, 3)], key_terms=[])
    from app.pipeline.page_align import AlignConfig

    result = replay(original, extraction, [Segment(start=0, end=9, text=""), Segment(start=9, end=20, text="")],
                    "hybrid_refined", config=AlignConfig(next_page_penalty=0))
    assert result.windows[0].end == result.windows[1].start == 9
    assert original.windows[0].end == 10


def test_committed_schema_example_has_no_verified_claims():
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "samples" / "golden" / "evaluation_example.json"
    example = EvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))
    assert all(row.review.status != "verified" for row in example.alignment_labels)
    schema = json.loads((path.parent / "evaluation.schema.json").read_text(encoding="utf-8"))
    assert schema == EvaluationDataset.model_json_schema()
