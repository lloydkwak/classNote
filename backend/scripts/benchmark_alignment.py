"""Compare alignment against hand labels, with no LLM or re-embedding.

Positional (simple) mode — evaluates one job against a simple truth JSON:
    uv run python scripts/benchmark_alignment.py data/jobs/<id>/alignment.json truth.json

Job mode — uses the job's EvaluationDataset (from evaluation/labels.json):
    uv run python scripts/benchmark_alignment.py --job <id> [--allow-provisional]

MaViLS mode — runs against every lecture that has been imported under a directory:
    uv run python scripts/benchmark_alignment.py --mavils data/mavils/

Simple truth format: JSON list of {"start", "end", "pages": [...]} where [] means off-topic.
MaViLS format: produced by  scripts/import_mavils.py  — see that script for details.

The job and MaViLS modes use the full metrics from app.evaluation.metrics (interval
intersection instead of window midpoints, transition accuracy, etc.).  The simple mode
uses the original midpoint-based accuracy for backwards compatibility.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Alignment  # noqa: E402
from app.pipeline.page_align import AlignConfig, align_similarities  # noqa: E402


# ---------------------------------------------------------------------------
# Simple (legacy) mode — midpoint accuracy, no EvaluationDataset
# ---------------------------------------------------------------------------

def _label_at(truth: list[dict], time: float) -> list[int] | None:
    for span in truth:
        if span["start"] <= time < span["end"]:
            return span["pages"]
    return None


def _score_simple(pages: list[int | None], alignment: Alignment, truth: list[dict]) -> dict:
    """Duration-weighted accuracy against a simple list of labelled intervals."""
    exact = near = total = 0.0
    backward = 0
    previous = None
    for page, window in zip(pages, alignment.windows):
        if page is not None:
            backward += previous is not None and page < previous
            previous = page
        label = _label_at(truth, (window.start + window.end) / 2)
        if label is None:
            continue
        length = window.end - window.start
        total += length
        if page in label:
            exact += length
        if page is not None and label and min(abs(page - item) for item in label) <= 1:
            near += length
    return {
        "exact": exact / total if total else 0.0,
        "within_one": near / total if total else 0.0,
        "backward_moves": backward,
    }


def _evaluate_simple(alignment: Alignment, truth: list[dict], config: AlignConfig) -> dict:
    sims = np.array([window.similarities for window in alignment.windows])
    decisions = align_similarities(sims, config)
    pages = [None if d.page_index is None else alignment.pages[d.page_index] for d in decisions]
    return {**_score_simple(pages, alignment, truth), "pages": pages}


def _run_simple(alignment_path: Path, truth_path: Path) -> int:
    alignment = Alignment.model_validate_json(alignment_path.read_text(encoding="utf-8"))
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    saved = _score_simple([w.page for w in alignment.windows], alignment, truth)
    raw   = _score_simple([w.raw_page for w in alignment.windows], alignment, truth)
    print(f"saved alignment: {saved}")
    print(f"raw argmax:      {raw}")
    result = _evaluate_simple(alignment, truth, AlignConfig.from_env())
    print(f"current config:  { {k: v for k, v in result.items() if k != 'pages'} }")
    print(f"pages: {result['pages']}")
    return 0


# ---------------------------------------------------------------------------
# Job mode — full EvaluationDataset metrics
# ---------------------------------------------------------------------------

def _run_job(job_id: str, allow_provisional: bool) -> int:
    from app.evaluation.experiments import replay
    from app.evaluation.metrics import alignment_metrics
    from app.evaluation.storage import load_dataset
    from app.models import PdfExtraction, Segment
    from app.pipeline import run as pipeline

    folder = pipeline.job_dir(job_id)
    if not folder.is_dir():
        sys.exit(f"Job folder not found: {folder}")

    dataset = load_dataset(folder)
    if not dataset.alignment_labels:
        sys.exit(
            "No alignment labels found.  Run  scripts/init_gold.py --job <id>  first,\n"
            "then verify labels in the inspector (EVALUATION_MODE=1 → #/jobs/<id>/inspect)."
        )

    alignment  = Alignment.model_validate_json((folder / "alignment.json").read_text(encoding="utf-8"))
    extraction = PdfExtraction.model_validate_json((folder / "extraction.json").read_text(encoding="utf-8"))
    segments   = [Segment.model_validate(r) for r in
                  json.loads((folder / "segments.json").read_text(encoding="utf-8"))]

    eligible = [l for l in dataset.alignment_labels
                if l.review.status == "verified"
                or (allow_provisional and l.review.status in {"verified", "provisional"})]
    if not eligible:
        status = "provisional or verified" if allow_provisional else "verified"
        sys.exit(
            f"No {status} labels found in the dataset (revision {dataset.revision}).\n"
            "Verify labels in the inspector or add --allow-provisional."
        )

    print(f"Dataset {dataset.dataset_id!r} revision {dataset.revision}, "
          f"{len(eligible)} eligible labels (allow_provisional={allow_provisional})")
    print()

    rows = {}
    for variant in ("saved", "current", "hybrid", "hybrid_refined"):
        pred = replay(alignment, extraction, segments, variant)
        m = alignment_metrics(pred, dataset, allow_provisional)
        rows[variant] = m
        print(f"  {variant:16s}: "
              f"exact={m['exact_page_accuracy']:.1%}  "
              f"±1={m['within_one_page_accuracy']:.1%}  "
              f"transitions={m['transition_accuracy']}  "
              f"verified_labels={m['verified_label_count']}")

    print()
    print("Full metrics for 'saved' (the stored DP result):")
    print(json.dumps(rows["saved"], indent=2))
    return 0


# ---------------------------------------------------------------------------
# MaViLS mode
# ---------------------------------------------------------------------------

def _run_mavils(mavils_dir: Path, allow_provisional: bool) -> int:
    """Run alignment metrics on every imported MaViLS lecture.

    Each subdirectory of mavils_dir must contain:
      alignment.json      — produced by running the alignment pipeline
      evaluation/labels.json  — produced by import_mavils.py

    See scripts/import_mavils.py for how to produce these files.
    """
    from app.evaluation.experiments import replay
    from app.evaluation.metrics import alignment_metrics
    from app.evaluation.storage import load_dataset
    from app.models import PdfExtraction, Segment

    if not mavils_dir.is_dir():
        sys.exit(f"MaViLS directory not found: {mavils_dir}")

    lecture_dirs = sorted(d for d in mavils_dir.iterdir()
                          if d.is_dir() and (d / "alignment.json").exists()
                          and (d / "evaluation" / "labels.json").exists())
    if not lecture_dirs:
        sys.exit(
            "No imported MaViLS lectures found.  Run  scripts/import_mavils.py  first.\n"
            f"Looked in: {mavils_dir}"
        )

    print(f"Found {len(lecture_dirs)} MaViLS lectures in {mavils_dir}\n")

    aggregate_exact = aggregate_near = 0.0
    n = 0

    for lecture_dir in lecture_dirs:
        dataset = load_dataset(lecture_dir)
        alignment = Alignment.model_validate_json(
            (lecture_dir / "alignment.json").read_text(encoding="utf-8"))
        extraction_path = lecture_dir / "extraction.json"
        segments_path   = lecture_dir / "segments.json"
        if not extraction_path.exists() or not segments_path.exists():
            print(f"  {lecture_dir.name}: SKIP — extraction.json or segments.json missing")
            continue
        extraction = PdfExtraction.model_validate_json(extraction_path.read_text(encoding="utf-8"))
        segments   = [Segment.model_validate(r)
                      for r in json.loads(segments_path.read_text(encoding="utf-8"))]
        pred = replay(alignment, extraction, segments, "saved")
        m = alignment_metrics(pred, dataset, allow_provisional)
        exact = m["exact_page_accuracy"]
        near  = m["within_one_page_accuracy"]
        if exact is not None:
            aggregate_exact += exact
            aggregate_near  += near if near is not None else 0.0
            n += 1
        print(f"  {lecture_dir.name:30s}: exact={exact:.1%}  ±1={near:.1%}  "
              f"labels={m['label_count']}  verified={m['verified_label_count']}")

    if n:
        print(f"\nMaViLS aggregate ({n} lectures):  "
              f"mean exact={aggregate_exact/n:.1%}  mean ±1={aggregate_near/n:.1%}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("alignment", nargs="?", type=Path, help="alignment.json (simple mode)")
    parser.add_argument("truth", nargs="?", type=Path, help="simple truth.json (simple mode)")
    parser.add_argument("--job", help="Job ID for EvaluationDataset mode")
    parser.add_argument("--mavils", type=Path, metavar="DIR", help="Directory of imported MaViLS lectures")
    parser.add_argument("--allow-provisional", action="store_true",
                        help="Include provisional labels alongside verified ones")
    args = parser.parse_args()

    if args.mavils:
        return _run_mavils(args.mavils, args.allow_provisional)
    if args.job:
        return _run_job(args.job, args.allow_provisional)
    if args.alignment and args.truth:
        return _run_simple(args.alignment, args.truth)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
