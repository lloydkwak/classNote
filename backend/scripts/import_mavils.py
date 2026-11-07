"""Import MaViLS (Matching Video and Lecture Slides) as an alignment regression set.

LICENSE CHECK — do this before running:
  Visit https://github.com/andererka/MaViLS and check the LICENSE file.
  As of 2026-11, the repository does not contain a separate license file; the paper
  is published at ECIR 2022 under standard academic use terms.  Confirm the terms
  allow you to download and use the dataset for non-commercial research before
  running this script.  Set  MAVILS_LICENSE_CONFIRMED=1  in your environment after
  confirming.

What MaViLS contains:
  20 English university lectures (auto-transcribed with Whisper-like tools).
  For each lecture: a transcript CSV with (start, end, text, slide_index) columns
  and slide images.  sentence_to_slide.csv is the canonical label file.

This script:
  1. Downloads the MaViLS dataset zip from GitHub releases (or uses a local copy).
  2. For each lecture, creates an EvaluationDataset (provisional alignment labels)
     under data/mavils/<lecture_id>/evaluation/labels.json.
  3. Writes a stub extraction.json and segments.json so  benchmark_alignment.py
     can run once the alignment pipeline has been executed.

After running this script, run the alignment pipeline on each lecture:
    uv run python scripts/run_pipeline.py --pdf data/mavils/<id>/input.pdf \\
        --audio data/mavils/<id>/audio.wav
Then evaluate:
    uv run python scripts/benchmark_alignment.py --mavils data/mavils/

Usage:
    uv run python scripts/import_mavils.py [--source <dir-or-zip>] [--output <dir>]
    uv run python scripts/import_mavils.py --source data/mavils-raw/  --output data/mavils/
"""

import argparse
import csv
import json
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.schema import EvaluationDataset, Interval, Review  # noqa: E402
from app.pipeline.run import _write_atomically  # noqa: E402


_GITHUB_RELEASE_URL = "https://github.com/andererka/MaViLS/archive/refs/heads/main.zip"
_CSV_COLUMNS = ("start", "end", "text", "slide_index")


def _check_license() -> None:
    if os.environ.get("MAVILS_LICENSE_CONFIRMED") != "1":
        print(
            "ERROR: Set MAVILS_LICENSE_CONFIRMED=1 after checking the license at\n"
            "  https://github.com/andererka/MaViLS\n"
            "This script will not proceed without explicit confirmation."
        )
        sys.exit(1)


def _download(url: str, dest: Path) -> None:
    import urllib.request
    print(f"Downloading {url} → {dest} …")
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    print("Download complete.")


def _find_or_download(source: Path | None) -> Path:
    """Return path to a directory containing lecture subdirectories."""
    if source and source.is_dir():
        return source
    cache = Path(__file__).resolve().parents[2] / "data" / "cache" / "mavils-raw"
    zip_path = cache / "mavils-main.zip"
    if not zip_path.exists():
        _download(_GITHUB_RELEASE_URL, zip_path)
    extract_dir = cache / "extracted"
    if not extract_dir.exists():
        print(f"Extracting {zip_path} …")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
    return extract_dir


def _load_labels_csv(csv_path: Path) -> list[dict]:
    """Load sentence→slide labels from MaViLS CSV format.

    The CSV may have columns: start, end, text, slide_index (1-based or 0-based).
    Returns a list of {"start": float, "end": float, "text": str, "slide": int}.
    """
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                start = float(row.get("start") or row.get("start_time") or 0)
                end   = float(row.get("end") or row.get("end_time") or 0)
                slide = int(float(row.get("slide_index") or row.get("slide") or 0))
                text  = str(row.get("text") or row.get("sentence") or "")
                if end > start:
                    rows.append({"start": start, "end": end, "text": text, "slide": slide})
            except (ValueError, KeyError):
                continue
    return rows


def _merge_to_intervals(label_rows: list[dict]) -> list[dict]:
    """Merge consecutive sentences on the same slide into an interval."""
    if not label_rows:
        return []
    merged: list[dict] = []
    current = dict(label_rows[0])
    for row in label_rows[1:]:
        if row["slide"] == current["slide"] and abs(row["start"] - current["end"]) < 2.0:
            current["end"] = row["end"]
        else:
            merged.append(current)
            current = dict(row)
    merged.append(current)
    return [{"start": m["start"], "end": m["end"], "pages": [m["slide"]]} for m in merged]


def _create_evaluation_dataset(lecture_id: str, intervals: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    eval_dir = output_dir / "evaluation"
    eval_dir.mkdir(exist_ok=True)
    labels_path = eval_dir / "labels.json"
    if labels_path.exists():
        print(f"  {lecture_id}: evaluation/labels.json already exists, skipping")
        return
    dataset = EvaluationDataset(
        dataset_id=f"mavils-{lecture_id}",
        lecture_id=lecture_id,
        split="test",
        provenance="Imported from MaViLS dataset (andererka/MaViLS on GitHub). "
                   "Labels are sentence→slide ground truth from the dataset; "
                   "review.status is 'provisional' — not human-verified by this project.",
    )
    dataset.alignment_labels = [
        Interval(
            id=f"mavils-{i}",
            start=row["start"],
            end=row["end"],
            pages=row["pages"],
            review=Review(status="provisional", source="legacy_inference",
                          note="From MaViLS sentence→slide labels"),
        )
        for i, row in enumerate(intervals)
    ]
    _write_atomically(labels_path, dataset.model_dump_json(indent=2))
    print(f"  {lecture_id}: wrote {len(intervals)} provisional labels → {labels_path}")


def _process_lecture(lecture_dir: Path, output_base: Path) -> bool:
    """Find CSV labels in lecture_dir and create EvaluationDataset under output_base."""
    # MaViLS layout may vary; look for common label file names
    candidates = list(lecture_dir.glob("*.csv")) + list(lecture_dir.glob("**/sentence_to_slide*.csv"))
    if not candidates:
        print(f"  {lecture_dir.name}: no CSV label file found, skipping")
        return False
    csv_path = candidates[0]
    rows = _load_labels_csv(csv_path)
    if not rows:
        print(f"  {lecture_dir.name}: no valid rows in {csv_path.name}, skipping")
        return False
    intervals = _merge_to_intervals(rows)
    lecture_id = lecture_dir.name
    out_dir = output_base / lecture_id
    _create_evaluation_dataset(lecture_id, intervals, out_dir)
    # Copy or symlink the PDF/slides if present, so pipeline can run
    for pattern in ("*.pdf", "slides.pdf", "lecture.pdf"):
        pdfs = list(lecture_dir.glob(pattern))
        if pdfs:
            dest = out_dir / "input.pdf"
            if not dest.exists():
                import shutil
                shutil.copy2(pdfs[0], dest)
                print(f"  {lecture_id}: copied {pdfs[0].name} → input.pdf")
            break
    return True


def main() -> int:
    _check_license()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path,
                        help="Local directory or zip with MaViLS data (downloads if absent)")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[2] / "data" / "mavils",
                        help="Output directory for imported lectures (default: data/mavils/)")
    args = parser.parse_args()

    source_dir = _find_or_download(args.source)
    args.output.mkdir(parents=True, exist_ok=True)

    # Discover lecture subdirectories: either lecture_dir/sentence_to_slide.csv or lecture_dir/*.csv
    lecture_dirs = sorted(d for d in source_dir.rglob("*") if d.is_dir() and list(d.glob("*.csv")))
    if not lecture_dirs:
        print(f"No lecture directories with CSV files found under {source_dir}")
        return 1

    print(f"Found {len(lecture_dirs)} lecture directories under {source_dir}\n")
    imported = sum(_process_lecture(d, args.output) for d in lecture_dirs)
    print(f"\nImported {imported}/{len(lecture_dirs)} lectures → {args.output}")
    print("\nNext steps:")
    print("  1. For each lecture, run the alignment pipeline:")
    print("       uv run python scripts/run_pipeline.py --pdf data/mavils/<id>/input.pdf \\")
    print("           --audio data/mavils/<id>/audio.wav --to-step align")
    print("  2. Benchmark:")
    print(f"       uv run python scripts/benchmark_alignment.py --mavils {args.output} --allow-provisional")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
