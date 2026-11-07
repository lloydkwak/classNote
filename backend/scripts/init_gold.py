"""Initialize or reset the alignment gold for a job.

Converts saved DP alignment pages into provisional EvaluationDataset labels.
These need human verification in the inspector:
  EVALUATION_MODE=1  →  http://localhost:8000  →  #/jobs/<id>/inspect

Options:
  --job <id>        required, identifies the job under data/jobs/
  --legacy <file>   optional JSON list of {"start","end","pages","note"} to import
                    instead of inferring from alignment.json. Use for curated labels.
  --force           overwrite existing labels (use only after the existing labels
                    have been archived in evaluation/labels-r*.json)

Workflow:
  1. Run once per lecture to seed provisional labels.
  2. Open the inspector, listen to the recording, correct page assignments,
     set review.status to "verified" for each span, and save.
  3. Run  evaluate_lecture.py benchmark --job <id> --allow-provisional  to report metrics.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.schema import EvaluationDataset, Interval, Review
from app.evaluation.storage import import_legacy, load_dataset, save_dataset
from app.models import Alignment
from app.pipeline import run as pipeline


def windows_to_intervals(alignment: Alignment) -> list[dict]:
    rows = []
    for w in sorted(alignment.windows, key=lambda w: w.start):
        pages: list[int] = []
        if w.page is not None:
            pages.append(w.page)
        if w.raw_page is not None and w.confidence < 0.1 and w.raw_page not in pages:
            pages.append(w.raw_page)
        note = f"provisional from saved DP (confidence={w.confidence:.3f})"
        if w.confidence < 0.1:
            note += " — LOW CONFIDENCE: check carefully"
        rows.append({"start": w.start, "end": w.end, "pages": pages, "note": note})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job", required=True)
    parser.add_argument("--legacy", type=Path, help="JSON list of labelled intervals to import")
    parser.add_argument("--force", action="store_true", help="Overwrite existing labels")
    args = parser.parse_args()

    folder = pipeline.job_dir(args.job)
    if not folder.is_dir():
        sys.exit(f"Job folder not found: {folder}")

    alignment_path = folder / "alignment.json"
    if not alignment_path.exists():
        sys.exit("alignment.json not found; run the pipeline through the align step first")

    alignment = Alignment.model_validate_json(alignment_path.read_text(encoding="utf-8"))
    rows = json.loads(args.legacy.read_text(encoding="utf-8")) if args.legacy else windows_to_intervals(alignment)

    existing = load_dataset(folder)

    if existing.alignment_labels and not args.force:
        label_dir = folder / "evaluation"
        archived = sorted(label_dir.glob("labels-r*.json")) if label_dir.exists() else []
        sys.exit(
            f"Gold already initialized ({len(existing.alignment_labels)} labels, "
            f"revision {existing.revision}).\n"
            f"  Archives: {[p.name for p in archived]}\n"
            f"  Use --force to overwrite, but verify archives are intact first."
        )

    if args.force and existing.alignment_labels:
        fresh = EvaluationDataset(
            schema_version=existing.schema_version,
            dataset_id=existing.dataset_id,
            dataset_version=existing.dataset_version,
            revision=existing.revision,
            lecture_id=existing.lecture_id,
            inputs=existing.inputs,
            split=existing.split,
        )
        (folder / "evaluation").mkdir(exist_ok=True)
        (folder / "evaluation" / "labels.json").write_text(fresh.model_dump_json(indent=2), encoding="utf-8")
        existing = fresh

    dataset = import_legacy(rows, folder)
    saved = save_dataset(folder, dataset)
    print(json.dumps({
        "dataset_id": saved.dataset_id,
        "revision": saved.revision,
        "label_count": len(saved.alignment_labels),
        "source": str(args.legacy) if args.legacy else "alignment.json saved pages",
        "next_step": f"EVALUATION_MODE=1  →  #/jobs/{args.job}/inspect",
    }, indent=2))


if __name__ == "__main__":
    main()
