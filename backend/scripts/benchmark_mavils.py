"""Benchmark alignment accuracy on the MaViLS dataset.

Requires: data imported with import_mavils.py first.

Usage:
    # Baseline (v1 aligner + e5 embedder)
    uv run python scripts/benchmark_mavils.py --mavils data/mavils/ --aligner v1 --embedder e5

    # Sentence aligner
    uv run python scripts/benchmark_mavils.py --mavils data/mavils/ --aligner v2 --embedder e5

    # Different embedder (downloads ~570 MB on first run)
    uv run python scripts/benchmark_mavils.py --mavils data/mavils/ --aligner v2 --embedder bge-m3

    # Tune sharpening_exp and bm25_weight on train lectures; report on test
    uv run python scripts/benchmark_mavils.py --mavils data/mavils/ --aligner v2 \\
        --embedder bge-m3 --tune --output data/benchmark-results/phase1/

    # Restrict to train or test split only
    uv run python scripts/benchmark_mavils.py --mavils data/mavils/ --train-only
    uv run python scripts/benchmark_mavils.py --mavils data/mavils/ --test-only
"""

import argparse
import json
import sys
from itertools import product
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.schema import EvaluationDataset, eligible
from app.models import Alignment, PdfExtraction, Segment
from app.pipeline.page_align import (
    AlignConfig,
    get_embedder,
    similarity_matrix,
)
from app.pipeline.pdf_extract import extract_pdf
from app.pipeline.sentence_align import (
    SentenceAlignConfig,
    align_sentence_similarities,
    build_sentence_windows,
    bm25_matrix,
    normalize_max,
)
from app.pipeline.windowing import build_windows
from app.pipeline.page_align import align_similarities


# ---------------------------------------------------------------------------
# Train / test split (deterministic: alphabetical SRT stem, first 10 = train)
# ---------------------------------------------------------------------------

_TRAIN_LECTURES = frozenset([
    "cities_and_decarbonization",
    "climate_and_cities",
    "climate_science_policy_MIT",
    "cognitive_robotics_MIT",
    "computer_vision_2_2_high_res",
    "creating_breakthrough_products_MIT",
    "cryptocurrency",
    "deeplearning_goodfellow",
    "image_processing_high_res",
    "ML_for_health_MIT",
])

_TEST_LECTURES = frozenset([
    "numerics_hennig",
    "phonetics",
    "physics_intro_02",
    "psychology_MIT",
    "reinforcement_learning_silver",
    "short_range_mit",
    "solar_resource",
    "team_dynamics_game_design_MIT",
    "theory_of_computation_MIT",
    "visual_system_MIT",
])


def lecture_split(lecture_id: str) -> str:
    if lecture_id in _TRAIN_LECTURES:
        return "train"
    if lecture_id in _TEST_LECTURES:
        return "test"
    # Fallback: alphabetically sort and assign
    return "unassigned"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(cache_dir: Path, lecture_id: str, embedder_name: str, aligner: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"sim_{lecture_id}_{embedder_name}_{aligner}.npy"


def _load_or_compute_sim(
    cache_path: Path,
    compute_fn,
) -> np.ndarray:
    if cache_path.exists():
        return np.load(cache_path)
    sim = compute_fn()
    np.save(cache_path, sim)
    return sim


# ---------------------------------------------------------------------------
# Sentence-level accuracy
# ---------------------------------------------------------------------------

def _sentence_accuracy(
    predictions: list[int | None],
    gold: EvaluationDataset,
    allow_provisional: bool,
) -> dict:
    """Compare per-sentence predictions against gold intervals.

    Gold intervals are point-labelled: each sentence timestamp (start==end) is
    matched to the interval that contains it, then the gold slide is compared.
    Returns {"exact": float, "n_scored": int, "n_total": int}.
    """
    labels = [
        lbl for lbl in gold.alignment_labels
        if eligible(lbl.review, allow_provisional) and lbl.pages  # skip off-topic
    ]
    if not labels:
        return {"exact": 0.0, "n_scored": 0, "n_total": len(predictions)}

    # Build sorted list of (start, end, page) for interval lookup
    intervals_sorted = sorted(labels, key=lambda lbl: lbl.start)

    correct = 0
    n_scored = 0
    for pred_page, pred in zip(predictions, range(len(predictions))):
        # This function is called with explicit (timestamp, pred_page) tuples below
        pass

    return {"exact": 0.0, "n_scored": 0, "n_total": len(predictions)}


def compute_accuracy(
    timestamps: list[float],
    pred_pages: list[int | None],
    gold: EvaluationDataset,
    allow_provisional: bool,
) -> dict:
    """Exact accuracy: fraction of sentences correctly assigned.

    'Correctly assigned' = predicted page equals gold slide at that timestamp.
    Sentences where gold is off-topic (pages=[]) are excluded from denominator.
    Timestamps outside all gold intervals are also excluded.
    """
    labels = [
        lbl for lbl in gold.alignment_labels
        if eligible(lbl.review, allow_provisional)
    ]
    if not labels:
        return {"exact": 0.0, "n_scored": 0, "n_total": len(timestamps)}

    intervals_sorted = sorted(labels, key=lambda lbl: lbl.start)

    def find_gold(t: float) -> list[int] | None:
        """Return gold pages at time t, or None if no interval contains t."""
        for lbl in intervals_sorted:
            if lbl.start <= t <= lbl.end:
                return lbl.pages
            if lbl.start > t:
                break
        return None

    correct = 0
    n_scored = 0
    for t, pred in zip(timestamps, pred_pages):
        gold_pages = find_gold(t)
        if gold_pages is None:
            continue  # outside all labeled regions
        if not gold_pages:
            continue  # off-topic in gold
        n_scored += 1
        if pred is not None and pred in gold_pages:
            correct += 1

    exact = correct / n_scored if n_scored > 0 else 0.0
    return {"exact": exact, "n_scored": n_scored, "n_total": len(timestamps)}


# ---------------------------------------------------------------------------
# Per-lecture processing
# ---------------------------------------------------------------------------

def _get_embedder_obj(embedder_name: str):
    if embedder_name == "e5":
        return get_embedder()
    if embedder_name == "bge-m3":
        return get_embedder("BAAI/bge-m3")
    if embedder_name == "qwen3":
        return get_embedder("Qwen/Qwen3-Embedding-0.6B")
    raise ValueError(f"Unknown embedder: {embedder_name!r}")


def run_lecture(
    lecture_dir: Path,
    embedder_name: str,
    aligner: str,
    bm25_weight: float,
    sa_config: SentenceAlignConfig,
    cache_dir: Path,
    allow_provisional: bool,
) -> dict | None:
    """Process one lecture. Returns accuracy dict or None if skipped."""
    lecture_id = lecture_dir.name

    seg_path    = lecture_dir / "segments.json"
    labels_path = lecture_dir / "evaluation" / "labels.json"
    pdf_path    = lecture_dir / "input.pdf"

    if not seg_path.exists():
        print(f"  {lecture_id}: segments.json missing, skipping")
        return None
    if not labels_path.exists():
        print(f"  {lecture_id}: labels.json missing, skipping")
        return None
    if not pdf_path.exists():
        print(f"  {lecture_id}: input.pdf missing, skipping (can't embed pages)")
        return None

    # Load segments
    segments = [Segment(**s) for s in json.loads(seg_path.read_text())]

    # Load gold
    gold = EvaluationDataset.model_validate_json(labels_path.read_text())

    # Extract PDF if needed
    ext_path = lecture_dir / "extraction.json"
    if ext_path.exists():
        extraction = PdfExtraction.model_validate_json(ext_path.read_text())
    else:
        print(f"  {lecture_id}: extracting PDF …")
        extraction = extract_pdf(pdf_path)
        ext_path.write_text(extraction.model_dump_json(indent=2))

    pages = extraction.pages
    if not pages:
        print(f"  {lecture_id}: no pages in extraction, skipping")
        return None

    embedder = _get_embedder_obj(embedder_name)

    # Build windows
    if aligner == "v1":
        windows = build_windows(segments)
        sim_fn = lambda: similarity_matrix(windows, pages, embedder)
        cp = _cache_path(cache_dir, lecture_id, embedder_name, "v1")
        sim = _load_or_compute_sim(cp, sim_fn)
        config_v1 = AlignConfig.from_env()
        decisions = align_similarities(sim, config_v1)
        # Window timestamps = midpoint
        timestamps = [(w.start + w.end) / 2.0 for w in windows]
        pred_pages = [
            None if d.page_index is None else pages[d.page_index].page
            for d in decisions
        ]
    else:  # v2
        windows = build_sentence_windows(segments)
        sem_fn = lambda: similarity_matrix(windows, pages, embedder)
        cp = _cache_path(cache_dir, lecture_id, embedder_name, "v2")
        sem_sim = _load_or_compute_sim(cp, sem_fn)

        if bm25_weight > 0.0:
            lex_sim = bm25_matrix(windows, pages)
            sim = (1.0 - bm25_weight) * sem_sim + bm25_weight * lex_sim
        else:
            sim = sem_sim

        decisions = align_sentence_similarities(sim, sa_config)
        # Sentence timestamps are midpoints (start == end in build_sentence_windows)
        timestamps = [w.start for w in windows]
        pred_pages = [
            None if d.page_index is None else pages[d.page_index].page
            for d in decisions
        ]

    acc = compute_accuracy(timestamps, pred_pages, gold, allow_provisional)
    split = lecture_split(lecture_id)
    result = {
        "lecture_id": lecture_id,
        "split": split,
        "aligner": aligner,
        "embedder": embedder_name,
        "bm25_weight": bm25_weight,
        **acc,
    }
    return result


# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

_SHARPEN_GRID = [1.0, 2.0, 3.0, 4.0]
_BM25_GRID    = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]


def tune(
    train_dirs: list[Path],
    embedder_name: str,
    cache_dir: Path,
    allow_provisional: bool,
) -> SentenceAlignConfig:
    """Grid-search sharpening_exp × bm25_weight on train lectures."""
    best_acc  = -1.0
    best_cfg  = SentenceAlignConfig()
    n_combos  = len(_SHARPEN_GRID) * len(_BM25_GRID)
    print(f"  Tuning {n_combos} combinations over {len(train_dirs)} train lectures …")

    for exp, bw in product(_SHARPEN_GRID, _BM25_GRID):
        cfg = SentenceAlignConfig(sharpening_exp=exp, bm25_weight=bw)
        accs = []
        for d in train_dirs:
            r = run_lecture(d, embedder_name, "v2", bw, cfg, cache_dir, allow_provisional)
            if r and r["n_scored"] > 0:
                accs.append(r["exact"])
        mean = float(np.mean(accs)) if accs else 0.0
        if mean > best_acc:
            best_acc = mean
            best_cfg = cfg
            print(f"    exp={exp} bm25={bw:.1f}  mean_train={mean:.3f}  ← new best")

    print(f"  Best config: sharpening_exp={best_cfg.sharpening_exp}, "
          f"bm25_weight={best_cfg.bm25_weight:.1f}, mean_train_acc={best_acc:.3f}")
    return best_cfg


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_table(results: list[dict]) -> None:
    train = [r for r in results if r["split"] == "train"]
    test  = [r for r in results if r["split"] == "test"]
    other = [r for r in results if r["split"] not in ("train", "test")]

    header = f"{'Lecture':<40} {'Split':<6} {'Exact':>7} {'Scored':>8} {'Total':>7}"
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)
    for r in sorted(results, key=lambda x: (x["split"], x["lecture_id"])):
        print(f"{r['lecture_id']:<40} {r['split']:<6} {r['exact']:7.3f} "
              f"{r['n_scored']:8d} {r['n_total']:7d}")
    print(sep)
    if train:
        m = np.mean([r["exact"] for r in train])
        print(f"{'MEAN (train)':<40} {'train':<6} {m:7.3f}")
    if test:
        m = np.mean([r["exact"] for r in test])
        print(f"{'MEAN (test)':<40} {'test':<6}  {m:7.3f}")
    if other:
        m = np.mean([r["exact"] for r in other])
        print(f"{'MEAN (other)':<40} {'other':<6}  {m:7.3f}")
    print(sep)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--mavils", type=Path,
                        default=Path(__file__).resolve().parents[2] / "data" / "mavils",
                        help="Directory of imported MaViLS lectures")
    parser.add_argument("--embedder", choices=["e5", "bge-m3", "qwen3"], default="e5")
    parser.add_argument("--aligner", choices=["v1", "v2"], default="v1")
    parser.add_argument("--bm25-weight", type=float, default=0.0,
                        help="BM25 mix weight (0=semantic only, 1=BM25 only)")
    parser.add_argument("--train-only", action="store_true")
    parser.add_argument("--test-only", action="store_true")
    parser.add_argument("--tune", action="store_true",
                        help="Grid-search sharpening_exp × bm25_weight on train lectures")
    parser.add_argument("--allow-provisional", action="store_true", default=True)
    parser.add_argument("--output", type=Path,
                        help="Save JSON report to this path")
    parser.add_argument("--cache-dir", type=Path,
                        default=Path(__file__).resolve().parents[2] / "data" / "cache" / "mavils")
    args = parser.parse_args()

    lecture_dirs = sorted(
        d for d in args.mavils.iterdir() if d.is_dir()
    )
    if not lecture_dirs:
        print(f"No lecture directories found under {args.mavils}")
        return 1

    if args.train_only:
        lecture_dirs = [d for d in lecture_dirs if lecture_split(d.name) == "train"]
    elif args.test_only:
        lecture_dirs = [d for d in lecture_dirs if lecture_split(d.name) == "test"]

    print(f"Found {len(lecture_dirs)} lecture(s)")

    sa_config = SentenceAlignConfig(bm25_weight=args.bm25_weight)

    # Tuning: runs only on train, then uses best config for all
    if args.tune:
        if args.aligner != "v2":
            print("--tune only applies to --aligner v2")
        else:
            train_dirs = [d for d in lecture_dirs if lecture_split(d.name) == "train"]
            sa_config = tune(train_dirs, args.embedder, args.cache_dir, args.allow_provisional)

    results: list[dict] = []
    for d in lecture_dirs:
        print(f"  {d.name} …", end=" ", flush=True)
        r = run_lecture(
            d, args.embedder, args.aligner, sa_config.bm25_weight,
            sa_config, args.cache_dir, args.allow_provisional,
        )
        if r:
            print(f"exact={r['exact']:.3f} ({r['n_scored']}/{r['n_total']} scored)")
            results.append(r)
        else:
            print("skipped")

    if not results:
        print("No results.")
        return 1

    print()
    print_table(results)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "aligner": args.aligner,
            "embedder": args.embedder,
            "bm25_weight": sa_config.bm25_weight,
            "sharpening_exp": sa_config.sharpening_exp if args.aligner == "v2" else None,
            "results": results,
        }
        args.output.write_text(json.dumps(report, indent=2))
        print(f"\nReport saved → {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
