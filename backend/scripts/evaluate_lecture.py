"""Local dataset import, reproducible benchmark and bounded rich-notes smoke test."""

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cache import hash_file
from app.config import get_settings
from app.evaluation.report import evaluate_job, git_provenance
from app.evaluation.schema import EvaluationDataset
from app.evaluation.storage import import_legacy, load_dataset, save_dataset
from app.models import Alignment, PdfExtraction, Segment
from app.pipeline import run as pipeline
from app.pipeline.rich_notes import extract_content, plan_annotations
from app.pipeline.consolidate import consolidate
from app.pipeline.export_pdf import export_annotated_pdf


def provider_status():
    settings = get_settings()
    return {"configured_provider": settings.llm_provider, "configured_model": settings.llm_model,
            "demo_mode": settings.demo_mode,
            "key_available": {name: bool(os.environ.get(name)) and not os.environ[name].startswith("your-")
                              for name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")}}


def smoke(folder, output, real=False, seconds=60.0):
    from app.llm import get_provider
    from app.llm.mock import MockProvider
    from app.llm.base import RetryPolicy

    started = time.perf_counter()
    status = provider_status()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"real_requested": real, "status": status, "git": git_provenance(),
                "cost": None, "human_semantic_review": False,
                "source_job": folder.name,
                "source_hashes": {name: hash_file(folder / name) for name in (
                    "input.pdf", "audio.mp3", "extraction.json", "alignment.json", "segments.json") if (folder / name).exists()}}
    (output / "smoke.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    key = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(status["configured_provider"])
    if real and (status["demo_mode"] or key is None or not status["key_available"][key]):
        manifest["outcome"] = "real_call_unavailable"
    else:
        try:
            provider = get_provider() if real else MockProvider()
            provider.retry_policy = RetryPolicy(max_retries=0, request_timeout=30)
            alignment = pipeline._read_alignment(folder)
            first = next(window for window in alignment.windows if window.page is not None)
            selected = [window for window in alignment.windows
                        if first.start <= window.start and window.end <= first.start + seconds]
            if not selected:
                raise ValueError("No whole window fits; increase --seconds")
            alignment = Alignment(pages=alignment.pages, windows=selected)
            (output / "alignment.json").write_text(alignment.model_dump_json(indent=2), encoding="utf-8")
            extraction = pipeline._read_extraction(folder)
            segments = [segment for segment in pipeline._read_list(folder / "segments.json", Segment)
                        if selected[0].start <= segment.start and segment.end <= selected[-1].end]
            pipeline._write_models(output / "segments.json", segments)
            content = extract_content(alignment, extraction, segments, provider)
            content.alignment_hash = hash_file(folder / "alignment.json")
            content.extraction_hash = hash_file(folder / "extraction.json")
            annotations = consolidate(plan_annotations(content, extraction, "balanced"), extraction,
                                      f"smoke-{folder.name}", max_notes_per_page=len(content.items))
            (output / "detailed_notes.json").write_text(content.model_dump_json(indent=2), encoding="utf-8")
            (output / "annotations.json").write_text(annotations.model_dump_json(indent=2), encoding="utf-8")
            export_annotated_pdf(folder / "input.pdf", annotations, output / "annotated.pdf")
            manifest.update({"outcome": ("complete" if content.items else "completed_no_grounded_items") if content.complete else "partial_or_failed",
                             "real_provider_calls": content.telemetry["provider_calls"] if real else 0,
                             "items": len(content.items), "types": sorted({item.type for item in content.items}),
                             "candidate_types": dict(Counter(item.type for item in content.items) + Counter(
                                 record.candidate.type for record in content.validation if record.candidate is not None)),
                             "validation": [record.model_dump(exclude={"candidate"}) for record in content.validation if record.status != "accepted"],
                             "telemetry": content.telemetry, "spans": content.spans,
                             "evaluated_start": selected[0].start, "evaluated_end": selected[-1].end,
                             "annotation_count": sum(len(page.notes) for page in annotations.pages)})
        except Exception as error:
            manifest.update({"outcome": "failed", "failure_type": type(error).__name__})
    manifest["runtime_seconds"] = time.perf_counter() - started
    (output / "smoke.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "init", "import-legacy", "benchmark", "smoke", "schema"])
    parser.add_argument("--job")
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--notes-run", type=Path)
    parser.add_argument("--legacy", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-provisional", action="store_true")
    parser.add_argument("--embedding-model")
    parser.add_argument("--lexical-weight", type=float, default=.25)
    parser.add_argument("--glossary", type=Path)
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--seconds", type=float, default=60)
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(provider_status()))
        return
    if args.command == "schema":
        print(json.dumps(EvaluationDataset.model_json_schema(), indent=2))
        return
    if not args.job or not pipeline.job_dir(args.job).is_dir():
        parser.error("--job must identify an existing local job")
    if Path(args.job).name != args.job or args.job in {".", ".."}:
        parser.error("Invalid job ID")
    folder = pipeline.job_dir(args.job)
    dataset = EvaluationDataset.model_validate_json(args.dataset.read_text(encoding="utf-8")) if args.dataset else load_dataset(folder)
    if args.command == "import-legacy":
        if args.legacy is None:
            parser.error("--legacy is required")
        dataset = import_legacy(json.loads(args.legacy.read_text(encoding="utf-8")), folder)
    if args.command in {"init", "import-legacy"}:
        saved = save_dataset(folder, dataset)
        print(json.dumps({"dataset_id": saved.dataset_id, "revision": saved.revision}))
    elif args.command == "benchmark":
        glossary = json.loads(args.glossary.read_text(encoding="utf-8")) if args.glossary else None
        result = evaluate_job(folder, dataset, allow_provisional=args.allow_provisional,
                              embedding_model=args.embedding_model, lexical_weight=args.lexical_weight, glossary=glossary,
                              notes_folder=args.notes_run)
        target = args.output or folder / "evaluation" / f"report-{time.time_ns()}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        print(json.dumps({"report": str(target), "alignment": {name: row["metrics"] for name, row in result["alignment"].items()}}))
    elif args.command == "smoke":
        output = args.output or get_settings().data_dir / "evaluation-runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        smoke(folder, output, args.real, args.seconds)


if __name__ == "__main__":
    main()
