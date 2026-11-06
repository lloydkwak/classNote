"""Runs only inside the disposable worker. No imports of torch in the API process."""

import gc
import resource
import time
import traceback

from pydantic import ValidationError

from app.vlm.schema import PageAnalysis, analysis_prompt


class StructuredOutputFailure(ValueError):
    def __init__(self, raw_output, metrics):
        self.raw_output, self.metrics = raw_output, metrics
        super().__init__("invalid structured output")


class QwenRuntime:
    def __init__(self, config):
        self.config = config
        self.model = self.processor = None
        self.phase = "unloaded"

    def load(self):
        if self.model is not None:
            return 0.0
        import torch
        from transformers import AutoProcessor, BitsAndBytesConfig, Qwen3VLForConditionalGeneration

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable; run GPU smoke outside the restricted sandbox")
        started = time.perf_counter()
        self.phase = "load"
        options = dict(revision=self.config.revision,
                       cache_dir=self.config.model_cache_dir,
                       local_files_only=self.config.local_files_only)
        self.processor = AutoProcessor.from_pretrained(self.config.model_id, **options)
        self.processor.image_processor.size = {
            "shortest_edge": 16384, "longest_edge": self.config.max_pixels,
        }
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(
            self.config.model_id, **options,
            quantization_config=BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.float16,
            ),
            dtype=torch.float16, device_map={"": 0}, attn_implementation="sdpa",
        ).eval()
        if not getattr(self.model, "is_loaded_in_4bit", False):
            raise RuntimeError("4-bit model loading was not enabled")
        torch.cuda.synchronize()
        return time.perf_counter() - started

    def analyze(self, request, reduced=False):
        import torch
        from PIL import Image

        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        load_seconds = self.load()
        self.phase = "prepare_input"
        pixels = max(16384, self.config.max_pixels // (2 if reduced else 1))
        output_budget = max(64, self.config.max_new_tokens // (2 if reduced else 1))
        with Image.open(request.image_path) as source:
            original_size = source.size
            image = source.convert("RGB")
        scale = min(1.0, (pixels / (image.width * image.height)) ** 0.5)
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
        self.processor.image_processor.size = {"shortest_edge": 16384, "longest_edge": pixels}
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image}, {"type": "text", "text": analysis_prompt()},
        ]}]
        inputs = self.processor.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True,
            return_dict=True, return_tensors="pt",
        )
        inputs.pop("token_type_ids", None)
        input_tokens = inputs.input_ids.shape[-1]
        grid = inputs.get("image_grid_thw")
        image_grid = grid.tolist() if grid is not None else None
        input_budget = self.config.max_input_tokens // (2 if reduced else 1)
        if input_tokens > input_budget:
            raise ValueError(f"input tokens {input_tokens} exceed budget {input_budget}; no truncation")
        inputs = inputs.to("cuda:0")
        infer_start = time.perf_counter()
        self.phase = "generate"
        with torch.inference_mode():
            generated = self.model.generate(**inputs, max_new_tokens=output_budget, do_sample=False)
        torch.cuda.synchronize()
        latency = time.perf_counter() - infer_start
        tokens = generated[:, input_tokens:]
        raw = self.processor.batch_decode(tokens, skip_special_tokens=True,
                                          clean_up_tokenization_spaces=False)[0]
        metrics = {
            "model_load_seconds": load_seconds, "inference_seconds": latency,
            "worker_request_seconds": time.perf_counter() - started,
            "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
            "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "input_image_size": original_size, "bounded_image_size": image.size,
            "image_grid_thw": image_grid,
            "input_tokens": input_tokens, "output_tokens": tokens.shape[-1],
            "tokens_per_second": tokens.shape[-1] / latency,
            "max_pixels": pixels, "max_new_tokens": output_budget,
            "schema_valid": False, "reduced_budget": reduced,
            "checkpoint": self.config.model_id, "revision": self.config.revision,
            "quantization": "bitsandbytes_nf4_double", "attention": "sdpa", "batch_size": 1,
        }
        # Retain failed generation measurements without caching it as success.
        try:
            self.phase = "validate_output"
            analysis = PageAnalysis.model_validate_json(raw)
        except ValidationError as error:
            raise StructuredOutputFailure(raw, metrics) from error
        metrics["schema_valid"] = True
        return {"analysis": analysis.model_dump(mode="json"), "metrics": metrics}

    def close(self):
        import torch

        self.model = self.processor = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def worker_main(connection, config, factory):
    """Keep one lazy model until close; terminate after OOM to clear traceback references."""
    runtime = None
    try:
        runtime = factory(config)
        while True:
            command = connection.recv()
            if command is None:
                return
            request, reduced = command
            try:
                connection.send({"ok": True, "value": runtime.analyze(request, reduced)})
            except Exception as error:
                # torch is optional for deterministic lifecycle tests.
                is_oom = error.__class__.__name__ in {"OutOfMemoryError", "MockOOM"}
                response = {"ok": False, "kind": "oom" if is_oom else "inference",
                            "error_type": error.__class__.__name__,
                            "phase": getattr(runtime, "phase", "mock"),
                            "frames": [{"function": f.name, "line": f.lineno,
                                        "file": f.filename.rsplit("/", 1)[-1]}
                                       for f in traceback.extract_tb(error.__traceback__)[-6:]]}
                if is_oom and error.__class__.__name__ != "MockOOM":
                    import torch

                    response["oom_metrics"] = {
                        "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                        "peak_reserved_bytes": torch.cuda.max_memory_reserved(),
                    }
                if isinstance(error, StructuredOutputFailure):
                    response.update(raw_output=error.raw_output, metrics=error.metrics)
                connection.send(response)
                if is_oom:
                    return
    except EOFError:
        pass
    finally:
        if runtime is not None:
            runtime.close()
        connection.close()
