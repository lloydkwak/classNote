"""Compare an alignment against hand-labeled ground truth, before and after the DP.

Ground truth is a JSON list of {"start", "end", "page"}. Use "page": null for off-topic.

    uv run python scripts/eval_alignment.py data/jobs/<id>/alignment.json truth.json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Alignment  # noqa: E402


def true_page(time: float, truth: list[dict]) -> tuple[bool, int | None]:
    """Return (is labeled, page) for the moment `time`."""
    for span in truth:
        if span["start"] <= time < span["end"]:
            return True, span["page"]
    return False, None


def evaluate(alignment: Alignment, truth: list[dict]) -> dict:
    """Score each window by the label at its midpoint.

    Raw argmax can never say "off-topic", so `on_topic_*` compares both methods on
    only the windows that have a page label.
    """
    labeled = []
    for window in alignment.windows:
        found, page = true_page((window.start + window.end) / 2, truth)
        if found:
            labeled.append((window, page))
    on_topic = [(window, page) for window, page in labeled if page is not None]

    def accuracy(rows: list, attribute: str) -> float:
        if not rows:
            return 0.0
        return sum(getattr(window, attribute) == page for window, page in rows) / len(rows)

    return {
        "windows": len(labeled),
        "raw_accuracy": accuracy(labeled, "raw_page"),
        "dp_accuracy": accuracy(labeled, "page"),
        "on_topic_windows": len(on_topic),
        "on_topic_raw_accuracy": accuracy(on_topic, "raw_page"),
        "on_topic_dp_accuracy": accuracy(on_topic, "page"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("alignment", type=Path)
    parser.add_argument("truth", type=Path)
    args = parser.parse_args()

    alignment = Alignment.model_validate_json(args.alignment.read_text(encoding="utf-8"))
    truth = json.loads(args.truth.read_text(encoding="utf-8"))
    result = evaluate(alignment, truth)
    print(f"All labeled windows ({result['windows']}):")
    print(f"  before DP (raw argmax): {result['raw_accuracy']:.1%}")
    print(f"  after DP:               {result['dp_accuracy']:.1%}")
    print(f"On-topic windows only ({result['on_topic_windows']}):")
    print(f"  before DP (raw argmax): {result['on_topic_raw_accuracy']:.1%}")
    print(f"  after DP:               {result['on_topic_dp_accuracy']:.1%}")


if __name__ == "__main__":
    main()
