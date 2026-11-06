"""Run the pipeline from the command line.

New job:     uv run python scripts/run_pipeline.py --pdf slides.pdf --audio lecture.m4a
Resume job:  uv run python scripts/run_pipeline.py --job-id <id> --from-step align
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows consoles may not default to UTF-8, and transcripts contain Korean.
sys.stdout.reconfigure(encoding="utf-8")

from app.pipeline.run import (  # noqa: E402
    STEP_NAMES,
    create_job,
    job_dir,
    read_alignment,
    run_pipeline,
)


def format_time(seconds: float) -> str:
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def print_alignment(job_id: str) -> None:
    alignment = read_alignment(job_id)
    print(f"\n{'time':<13} {'page':>4} {'raw':>4} {'conf':>5}  text")
    for window in alignment.windows:
        span = f"{format_time(window.start)}-{format_time(window.end)}"
        page = "-" if window.page is None else window.page
        print(f"{span:<13} {page:>4} {window.raw_page:>4} {window.confidence:>5.2f}  {window.text[:60]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the LectureNote pipeline.")
    parser.add_argument("--pdf", type=Path, help="Slides, for a new job")
    parser.add_argument("--audio", type=Path, help="Recording, for a new job")
    parser.add_argument("--job-id", help="Job to resume, or the id for a new job")
    parser.add_argument("--from-step", choices=STEP_NAMES)
    parser.add_argument("--to-step", choices=STEP_NAMES)
    parser.add_argument("--visual-grounding",action=argparse.BooleanOptionalAction,default=None)
    parser.add_argument("--annotation-mode",choices=["legacy","ai_planner","visual_agent"])
    args = parser.parse_args()

    if args.pdf:
        job_id = create_job(args.pdf, args.audio, args.job_id,visual_grounding=args.visual_grounding,annotation_mode=args.annotation_mode)
    elif args.job_id:
        job_id = args.job_id
        if args.visual_grounding is not None or args.annotation_mode is not None:
            import json
            path=job_dir(job_id)/"job.json"
            options=json.loads(path.read_text()) if path.exists() else {}
            if args.visual_grounding is not None:options["visual_grounding"]=args.visual_grounding
            if args.annotation_mode is not None:options["annotation_mode"]=args.annotation_mode
            path.write_text(json.dumps(options),encoding="utf-8")
    else:
        parser.error("Pass --pdf for a new job, or --job-id to resume one.")

    started = time.monotonic()

    def show_status(status: str) -> None:
        print(f"[{time.monotonic() - started:6.1f}s] {status}", flush=True)

    run_pipeline(job_id, args.from_step, args.to_step, show_status)
    print(f"[{time.monotonic() - started:6.1f}s] done")
    print(f"Job {job_id}: {job_dir(job_id)}")
    print_alignment(job_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
