"""Reproducible, hash-bound evaluation report."""

import hashlib
import json
import subprocess
import time
from pathlib import Path

from app.cache import hash_file
from app.evaluation.experiments import experiment_config, replay
from app.evaluation.metrics import alignment_metrics, notes_metrics
from app.evaluation.storage import check_inputs
from app.models import Alignment, Annotations, PdfExtraction, Segment
from app.pipeline.rich_notes import DetailedNotes


def git_provenance():
    root = Path(__file__).resolve().parents[3]
    def run(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    sources = sorted((root / "backend" / "app").rglob("*.py"))
    sources.append(root / "backend" / "scripts" / "evaluate_lecture.py")
    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain")),
            "evaluation_code_sha256": hashlib.sha256(b"".join(str(path.relative_to(root)).encode()
                + b"\0" + path.read_bytes() for path in sources)).hexdigest()}


def evaluate_job(folder, dataset, variants=("saved", "current", "hybrid", "hybrid_refined"),
                 allow_provisional=False, embedding_model=None, lexical_weight=.25, glossary=None,
                 notes_folder=None):
    started = time.perf_counter()
    if dataset.lecture_id != folder.name:
        raise ValueError("Dataset lecture ID does not match this job")
    check_inputs(dataset, folder)
    alignment = Alignment.model_validate_json((folder / "alignment.json").read_text(encoding="utf-8"))
    extraction = PdfExtraction.model_validate_json((folder / "extraction.json").read_text(encoding="utf-8"))
    segments = [Segment.model_validate(row) for row in json.loads((folder / "segments.json").read_text(encoding="utf-8"))]
    results = {}
    for variant in variants:
        before = time.perf_counter()
        prediction = replay(alignment, extraction, segments, variant, lexical_weight, glossary)
        results[variant] = {"config": experiment_config(variant, lexical_weight, glossary),
                            "metrics": alignment_metrics(prediction, dataset, allow_provisional),
                            "runtime_seconds": time.perf_counter() - before,
                            "prediction": prediction.model_dump(exclude={"windows": {"__all__": {"text", "similarities"}}})}
    notes_folder = notes_folder or folder
    if notes_folder != folder:
        manifest = json.loads((notes_folder / "smoke.json").read_text(encoding="utf-8"))
        source_hashes = manifest.get("source_hashes", {})
        if (manifest.get("source_job") != folder.name
                or not {"alignment.json", "extraction.json", "segments.json"} <= source_hashes.keys()) or any(
            Path(name).name != name or not (folder / name).is_file() or hash_file(folder / name) != digest
            for name, digest in source_hashes.items()
        ):
            raise ValueError("Smoke artifacts do not match this job")
    content_path = notes_folder / "detailed_notes.json"
    notes = None
    if content_path.exists():
        content = DetailedNotes.model_validate_json(content_path.read_text(encoding="utf-8"))
        annotation_path = notes_folder / "annotations.json"
        annotations = Annotations.model_validate_json(annotation_path.read_text(encoding="utf-8")) if annotation_path.exists() else None
        notes = notes_metrics(content, annotations, segments, dataset, allow_provisional)
        notes["artifact_alignment_matches"] = (content.alignment_hash == hash_file(folder / "alignment.json")) if content.alignment_hash else None
        notes["artifact_extraction_matches"] = (content.extraction_hash == hash_file(folder / "extraction.json")) if content.extraction_hash else None
        notes["source"] = str(notes_folder)
        notes["artifact_hashes"] = {path.name: hash_file(path) for path in (content_path, annotation_path) if path.exists()}
    artifacts = {path.name: hash_file(path) for path in folder.glob("*.json")
                 if path.name in {"alignment.json", "extraction.json", "segments.json", "detailed_notes.json", "annotations.json"}}
    return {"report_schema_version": 1, "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version, "dataset_revision": dataset.revision,
            "dataset_sha256": hashlib.sha256(dataset.model_dump_json().encode()).hexdigest(),
            "split": dataset.split, "provisional_labels_included": allow_provisional,
            "generalization_claim": False, "embedding_model_declared": embedding_model,
            "embedding_source": "saved similarities; original model unknown unless declared",
            "git": git_provenance(), "artifacts": artifacts, "alignment": results, "notes": notes,
            "runtime_seconds": time.perf_counter() - started}
