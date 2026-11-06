"""Compare alignment settings on a job with hand labels, with no LLM or re-embedding.

Replays the DP over the similarities saved in alignment.json, so each setting takes
milliseconds. Labels are a JSON list of {"start", "end", "pages": [...]}: a window scores if
its midpoint's label lists the chosen page.

    uv run python scripts/benchmark_alignment.py data/jobs/<id>/alignment.json truth.json
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Alignment  # noqa: E402
from app.pipeline.page_align import AlignConfig, align_similarities  # noqa: E402


def label_at(truth: list[dict], time: float) -> list[int] | None:
    for span in truth:
        if span["start"] <= time < span["end"]:
            return span["pages"]
    return None


def score(pages: list[int | None], alignment: Alignment, truth: list[dict]) -> dict:
    """Accuracy over labeled windows, weighted by length, exactly and within one page."""
    exact = near = total = 0.0
    backward = 0
    previous = None
    for page, window in zip(pages, alignment.windows):
        if page is not None:
            backward += previous is not None and page < previous
            previous = page
        label = label_at(truth, (window.start + window.end) / 2)
        if label is None:
            continue
        length = window.end - window.start
        total += length
        if page in label:
            exact += length
        if page is not None and min(abs(page - item) for item in label) <= 1:
            near += length
    return {
        "exact": exact / total if total else 0.0,
        "within_one": near / total if total else 0.0,
        "backward_moves": backward,
    }


def evaluate(alignment: Alignment, truth: list[dict], config: AlignConfig) -> dict:
    similarities = np.array([window.similarities for window in alignment.windows])
    decisions = align_similarities(similarities, config)
    numbers = alignment.pages
    pages = [None if d.page_index is None else numbers[d.page_index] for d in decisions]
    return {**score(pages, alignment, truth), "pages": pages}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("alignment", type=Path)
    parser.add_argument("truth", type=Path)
    args = parser.parse_args()
    alignment = Alignment.model_validate_json(args.alignment.read_text(encoding="utf-8"))
    truth = json.loads(args.truth.read_text(encoding="utf-8"))
    saved = score([window.page for window in alignment.windows], alignment, truth)
    raw = score([window.raw_page for window in alignment.windows], alignment, truth)
    print(f"saved alignment: {saved}")
    print(f"raw argmax:      {raw}")
    result = evaluate(alignment, truth, AlignConfig.from_env())
    print(f"current config:  { {k: v for k, v in result.items() if k != 'pages'} }")
    print(f"pages: {result['pages']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
