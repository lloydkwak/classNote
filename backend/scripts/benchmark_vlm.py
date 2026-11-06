"""Reproducible opt-in Qwen3-VL smoke. No paid API, no production job mutations."""

import argparse
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vlm.schema import MODEL_ID, MODEL_REVISION, REPO_ROOT, PageRequest, VLMConfig
from app.cache import hash_file
from app.vlm.runtime import QwenRuntime


class PreflightFailure(RuntimeError):
    pass


class OOMProbeRuntime(QwenRuntime):
    """Explicit benchmark only: provoke a real allocation failure with the 8B model loaded."""

    def analyze(self, request, reduced=False):
        if not reduced:
            import torch

            self.load()
            self.phase = "intentional_oom_probe"
            # Request total VRAM as a single allocation while model/runtime occupy part of it.
            # CUDA rejects the allocation; do not touch other processes or switch models.
            torch.empty(torch.cuda.get_device_properties(0).total_memory,
                        dtype=torch.uint8, device="cuda:0")
            raise RuntimeError("OOM probe unexpectedly allocated total GPU memory")
        return super().analyze(request, reduced=True)


def command(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=15)
        return {"returncode": result.returncode, "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"error_type": type(error).__name__}


def preflight(cache_dir):
    import torch
    from transformers import AutoProcessor, BitsAndBytesConfig, Qwen3VLForConditionalGeneration

    versions = {}
    for name in ("torch", "torchvision", "transformers", "bitsandbytes", "accelerate", "pymupdf", "psutil",
                 "pydantic", "numpy", "tokenizers", "huggingface-hub", "python-dotenv"):
        versions[name] = importlib.metadata.version(name)
    # Validate the actual installed API, including the NF4 config construction.
    BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                       bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True)
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "python": sys.version, "platform": platform.platform(), "versions": versions,
              "cuda_build": torch.version.cuda, "cuda_available": torch.cuda.is_available(),
              "nvidia_smi": command("nvidia-smi"),
              "git": command("git", "rev-parse", "HEAD"),
              "git_status": command("git", "status", "--short"),
              "code_sha256": {p.name: hash_file(p) for p in
                              [Path(__file__), *sorted((Path(__file__).parents[1] / "app/vlm").glob("*.py"))]},
              "checkpoint": MODEL_ID, "revision": MODEL_REVISION,
              "cache_free_bytes": shutil.disk_usage(cache_dir).free,
              "api_imports_valid": True}
    if report["cuda_available"]:
        report.update(gpu=torch.cuda.get_device_name(0),
                      compute_capability=torch.cuda.get_device_capability(0),
                      gpu_free_total_bytes=torch.cuda.mem_get_info(0))
    return report


def download_checkpoint(cache_dir, workers=4):
    from huggingface_hub import HfApi, snapshot_download

    info = HfApi().model_info(MODEL_ID, revision=MODEL_REVISION, files_metadata=True)
    allowed = [f.rfilename for f in info.siblings
               if f.rfilename.endswith((".json", ".txt", ".safetensors"))]
    size = sum(f.size or 0 for f in info.siblings if f.rfilename in allowed)
    # Existing snapshots can be reused even when free space is now small.
    snapshot = cache_dir / ("models--" + MODEL_ID.replace("/", "--")) / "snapshots" / MODEL_REVISION
    missing = sum(f.size or 0 for f in info.siblings
                  if f.rfilename in allowed and not (snapshot / f.rfilename).exists())
    free = shutil.disk_usage(cache_dir).free
    if free < missing + 2 * 1024**3:
        raise PreflightFailure(f"insufficient_cache_space: missing={missing}, free={free}, reserve=2147483648")
    snapshot_download(MODEL_ID, revision=MODEL_REVISION, cache_dir=cache_dir,
                      allow_patterns=allowed, max_workers=workers)
    return {"manifest_bytes": size, "missing_bytes_before": missing, "resolved_revision": info.sha}


def render_slide(pdf_path, page_number, output):
    import pymupdf

    synthetic = pdf_path is None
    if synthetic:
        pdf_path = output / "synthetic.pdf"
        with pymupdf.open() as doc:
            page = doc.new_page(width=960, height=540)
            page.insert_text((50, 70), "Gradient Descent", fontsize=30)
            page.insert_text((50, 140), "Update parameters to minimize the loss.", fontsize=20)
            page.insert_text((50, 220), "theta = theta - alpha * gradient", fontsize=23)
            page.insert_text((50, 270), "alpha: learning rate", fontsize=20)
            page.draw_line((560, 460), (870, 460))
            page.draw_line((560, 460), (560, 150))
            page.draw_bezier((590, 200), (650, 450), (760, 450), (850, 230))
            page.insert_text((650, 490), "Loss curve", fontsize=18)
            doc.save(pdf_path)
    with pymupdf.open(pdf_path) as doc:
        if not 1 <= page_number <= len(doc):
            raise PreflightFailure("page outside PDF")
        page = doc[page_number - 1]
        png = output / f"page-{page_number}.png"
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False)
        pix.save(png)
        return png, {"synthetic": synthetic, "page": page_number,
                     "pdf_sha256": hash_file(pdf_path), "png_sha256": hash_file(png),
                     "pdf_rotation": page.rotation, "pdf_size_points": list(page.rect),
                     "render_pixels": [pix.width, pix.height], "pdf_text": page.get_text()}


class DeviceSampler:
    """Device-wide samples include CUDA runtime and other processes, unlike Torch allocator peaks."""
    def __init__(self):
        self.values = []
        self.ram_values = []
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        import psutil

        while not self.done.is_set():
            memory = psutil.virtual_memory()
            self.ram_values.append(memory.total - memory.available)
            sample = command("nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits", "-i", "0")
            try:
                self.values.append(int(sample["stdout"]) * 1024**2)
            except (KeyError, ValueError):
                pass
            self.done.wait(0.2)

    def start(self):
        self.thread.start()

    def stop(self):
        self.done.set()
        self.thread.join(timeout=16)
        return {"device_sample_count": len(self.values),
                "device_sampled_peak_bytes": max(self.values) if self.values else None,
                "device_sampled_mean_bytes": sum(self.values) / len(self.values) if self.values else None,
                "system_ram_used_sampled_peak_bytes": max(self.ram_values) if self.ram_values else None,
                "system_ram_definition": "total minus available; includes tmpfs/model cache and other processes",
                "sampling_interval_seconds": 0.2}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preflight", "download", "smoke"])
    parser.add_argument("--model-cache", type=Path, default=REPO_ROOT / "models/huggingface")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "data/vlm-runs" / datetime.now().strftime("%Y%m%dT%H%M%S%f"))
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--next-page", type=int,
                        help="Use a different PDF page for the second sequential request")
    parser.add_argument("--max-pixels", type=int, default=262144)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--download-workers", type=int, choices=[1, 2, 3, 4], default=4)
    parser.add_argument("--exercise-oom", action="store_true",
                        help="Explicitly induce CUDA OOM after loading 8B, then test reduced retry")
    args = parser.parse_args()
    if args.page < 1 or (args.next_page is not None and args.next_page < 1):
        parser.error("pages must be positive (1-based)")
    if args.exercise_oom and args.action != "smoke":
        parser.error("--exercise-oom requires smoke")
    args.output.mkdir(parents=True, exist_ok=True)
    args.model_cache.mkdir(parents=True, exist_ok=True)
    report = {"status": "started", "runs": [],
              "measurement_type": "real_gpu" if args.action == "smoke" else args.action}
    report["intentional_oom_probe"] = args.exercise_oom
    sampler = None
    exit_code = 0
    try:
        report["environment"] = preflight(args.model_cache)
        if not report["environment"]["cuda_available"]:
            raise PreflightFailure("CUDA unavailable; model download and smoke stopped")
        if args.action == "download":
            started = time.perf_counter()
            report["download"] = download_checkpoint(args.model_cache, args.download_workers)
            report["download"]["wall_seconds"] = time.perf_counter() - started
        if args.action == "smoke":
            from app.vlm.provider import GPUWorkerProvider

            if args.next_page is not None and args.pdf is None:
                raise PreflightFailure("--next-page requires --pdf")
            png, source = render_slide(args.pdf, args.page, args.output)
            report["source"] = source
            config = VLMConfig(model_cache_dir=args.model_cache.resolve(),
                               cache_dir=(args.output / "cache").resolve(),
                               max_pixels=args.max_pixels, max_new_tokens=args.max_new_tokens,
                               timeout_seconds=args.timeout)
            report["config"] = config.model_dump(mode="json")
            sampler = DeviceSampler()
            sampler.start()
            provider = GPUWorkerProvider(config, runtime_factory=OOMProbeRuntime if args.exercise_oom else QwenRuntime)
            try:
                for page_number in (args.page, args.next_page or args.page):
                    if page_number != args.page:
                        png, source = render_slide(args.pdf, page_number, args.output)
                    started = time.perf_counter()
                    result = provider.analyze_page(PageRequest(image_path=png.resolve(), page=page_number), use_cache=False)
                    result["request_page"] = page_number
                    result["source"] = source
                    result["wall_seconds"] = time.perf_counter() - started
                    title = result["analysis"]["title"]
                    norm = lambda text: " ".join(text.casefold().split())
                    result["literal_pdf_check"] = {
                        "title_in_pdf_text": bool(title) and norm(title) in norm(source["pdf_text"]),
                        "review_type": "automatic_literal_only", "semantic_correctness": None,
                        "region_correctness": None,
                    }
                    report["runs"].append(result)
                    (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
            finally:
                started = time.perf_counter()
                provider.close()
                report["worker_close_seconds"] = time.perf_counter() - started
            report["status"] = "completed_requires_visual_review"
            report["summary"] = {
                "real_requests": len(report["runs"]),
                "first_request_wall_seconds": report["runs"][0]["wall_seconds"],
                "subsequent_request_wall_seconds": report["runs"][1]["wall_seconds"],
                "warm_pages_per_second": 1 / report["runs"][1]["wall_seconds"],
                "oom_count": sum(r["oom_count"] for r in report["runs"]),
                "schema_valid_count": sum(r["metrics"]["schema_valid"] for r in report["runs"]),
            }
        else:
            report["status"] = "completed"
    except Exception as error:
        # Only these errors contain our controlled diagnostics; do not serialize remote auth errors.
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
        from app.vlm.provider import VLMFailure
        if isinstance(error, (PreflightFailure, VLMFailure)):
            report["reason"] = str(error)[:400]
        if isinstance(error, VLMFailure):
            report["failure_details"] = error.details
        exit_code = 1
    finally:
        if sampler is not None:
            report["gpu_sampling"] = sampler.stop()
        (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps({"status": report["status"], "report": str(args.output / "report.json")}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
