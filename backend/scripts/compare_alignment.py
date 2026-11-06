"""Replay distance-sensitive backward costs against provisional labels, without API calls."""

import json
from dataclasses import asdict, replace
from pathlib import Path

from benchmark_alignment import evaluate
from app.models import Alignment
from app.pipeline.page_align import AlignConfig


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("alignment", type=Path)
    parser.add_argument("truth", type=Path)
    args = parser.parse_args()
    alignment = Alignment.model_validate_json(args.alignment.read_text(encoding="utf-8"))
    truth = json.loads(args.truth.read_text(encoding="utf-8"))
    for cost in (0.0, 0.05, 0.1, 0.2, 0.4):
        config = replace(AlignConfig(), back_penalty_per_page=cost)
        result = evaluate(alignment, truth, config)
        pages = result.pop("pages")
        print(json.dumps({"config": asdict(config), **result, "first_page": pages[0]}))


if __name__ == "__main__":
    main()
