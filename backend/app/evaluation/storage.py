"""Evaluation revisions live under ignored job data, never in golden automatically."""

import json
import threading
from pathlib import Path

from app.cache import hash_file
from app.evaluation.schema import EvaluationDataset, InputIdentity
from app.pipeline.run import _write_atomically

_lock = threading.Lock()


def identities(folder):
    return {name: InputIdentity(identifier=name, sha256=hash_file(folder / name))
            for name in ("input.pdf", "audio.mp3", "segments.json", "extraction.json")
            if (folder / name).exists()}


def load_dataset(folder):
    path = folder / "evaluation" / "labels.json"
    if path.exists():
        return EvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))
    return EvaluationDataset(dataset_id=f"{folder.name}-evaluation", lecture_id=folder.name,
                             inputs=identities(folder))


def check_inputs(dataset, folder):
    for name, identity in dataset.inputs.items():
        path = folder / name
        if Path(name).name != name or not path.is_file() or hash_file(path) != identity.sha256:
            raise ValueError(f"Evaluation input mismatch: {name}")


def save_dataset(folder, dataset):
    with _lock:
        existing = load_dataset(folder)
        if dataset.revision != existing.revision:
            raise ValueError("Revision conflict; reload labels before saving")
        if dataset.lecture_id != folder.name:
            raise ValueError("Lecture ID does not match this job")
        if dataset.inputs != identities(folder):
            raise ValueError("Input identities must match the complete current job snapshot")
        check_inputs(dataset, folder)
        extraction_path = folder / "extraction.json"
        if extraction_path.exists():
            known_pages = {page["page"] for page in json.loads(extraction_path.read_text(encoding="utf-8"))["pages"]}
            referenced = {page for rows in (dataset.alignment_labels, dataset.meaning_units)
                          for row in rows for page in row.pages}
            referenced |= {page for row in dataset.transitions for page in row.from_pages + row.to_pages}
            if not referenced <= known_pages:
                raise ValueError("Label references a page that does not exist in the PDF")
        saved = dataset.model_copy(update={"revision": dataset.revision + 1})
        directory = folder / "evaluation"
        directory.mkdir(exist_ok=True)
        archive = directory / f"labels-r{saved.revision:06d}.json"
        if archive.exists():
            raise ValueError("Revision archive already exists; inspect it before recovering")
        text = saved.model_dump_json(indent=2)
        _write_atomically(archive, text)
        _write_atomically(directory / "labels.json", text)
        return saved


def import_legacy(rows, folder):
    from app.evaluation.schema import Interval, Review

    dataset = load_dataset(folder)
    if dataset.alignment_labels:
        raise ValueError("Refusing to replace existing labels")
    dataset.alignment_labels = [Interval(
        id=f"legacy-{index}", start=row["start"], end=row["end"],
        pages=row["pages"] if "pages" in row else ([] if row.get("page") is None else [row["page"]]),
        review=Review(status="provisional", source="legacy_inference", note=row.get("note", "")),
    ) for index, row in enumerate(rows)]
    dataset.provenance = "Imported legacy inference; no human verification asserted"
    return EvaluationDataset.model_validate(dataset.model_dump())
