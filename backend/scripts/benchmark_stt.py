"""Time one faster-whisper configuration on a recording, and score it against a reference.

    uv run python scripts/benchmark_stt.py clip.mp3 --model small --beam 1 --batch 8 \
        --prompt-mode hotwords --terms terms.json --reference reference.json

Each run prints one JSON line. Run configurations in separate processes, so none inherits
another's memory use. Accuracy is measured against the reference transcript, which is itself
a machine transcript: a low error rate means "close to the reference", not "correct".
"""

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402

import app  # noqa: E402,F401  Sets the thread environment, as the server does.

SHORT_PROMPT_CHARS = 120
# The same run of this many words appearing this often means the decoder got stuck.
LOOP_WORDS = 3
LOOP_REPEATS = 4


def edit_distance(reference: list, hypothesis: list) -> int:
    """Levenshtein distance, one row at a time."""
    previous = np.arange(len(hypothesis) + 1)
    for row, item in enumerate(reference, start=1):
        different = np.array([item != other for other in hypothesis], dtype=int)
        current = np.empty_like(previous)
        current[0] = row
        substituted = previous[:-1] + different
        deleted = previous[1:] + 1
        best = np.minimum(substituted, deleted)
        for column in range(1, len(previous)):
            current[column] = min(best[column - 1], current[column - 1] + 1)
        previous = current
    return int(previous[-1])


def normalize(text: str) -> str:
    """Lowercase and drop punctuation, which transcripts disagree on without meaning much."""
    kept = [
        char if unicodedata.category(char)[0] in "LN" else " "
        for char in unicodedata.normalize("NFC", text).casefold()
    ]
    return " ".join("".join(kept).split())


def error_rates(reference: str, hypothesis: str) -> dict[str, float]:
    reference, hypothesis = normalize(reference), normalize(hypothesis)
    words, heard = reference.split(), hypothesis.split()
    characters = list(reference.replace(" ", ""))
    return {
        "wer": round(edit_distance(words, heard) / max(len(words), 1), 3),
        # Korean spacing varies a lot, so characters are the fairer comparison.
        "cer": round(
            edit_distance(characters, list(hypothesis.replace(" ", ""))) / max(len(characters), 1),
            3,
        ),
    }


def count_terms(text: str, terms: dict[str, list[str]]) -> dict[str, dict[str, int]]:
    """For each slide term, count its slide spelling and its phonetic Hangul spellings."""
    lowered = unicodedata.normalize("NFC", text).casefold()
    return {
        term: {
            "latin": len(re.findall(re.escape(term.casefold()), lowered)),
            "hangul": sum(lowered.count(spelling) for spelling in spellings),
        }
        for term, spellings in terms.items()
    }


def find_loops(text: str) -> list[str]:
    """Return word runs repeated back to back, the signature of a stuck decoder."""
    words = normalize(text).split()
    loops = set()
    for index in range(len(words) - LOOP_WORDS * LOOP_REPEATS + 1):
        run = words[index : index + LOOP_WORDS]
        if all(
            words[index + LOOP_WORDS * repeat : index + LOOP_WORDS * (repeat + 1)] == run
            for repeat in range(1, LOOP_REPEATS)
        ):
            loops.add(" ".join(run))
    return sorted(loops)


def prompt_options(mode: str, prompt: str) -> dict:
    if mode == "initial":
        return {"initial_prompt": prompt}
    if mode == "short":
        return {"initial_prompt": prompt[:SHORT_PROMPT_CHARS].rsplit(",", 1)[0]}
    if mode == "hotwords":
        return {"hotwords": prompt}
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("audio")
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--beam", type=int, default=5)
    parser.add_argument("--batch", type=int, default=0, help="0 means not batched")
    parser.add_argument("--threads", type=int, default=0, help="0 means CTranslate2's default")
    parser.add_argument("--prompt-mode", choices=["initial", "short", "hotwords", "none"], default="initial")
    parser.add_argument("--prompt-file", type=Path, help="JSON list of slide key terms")
    parser.add_argument("--terms", type=Path, help='JSON: {"centroid": ["센트로이드"], ...}')
    parser.add_argument("--reference", type=Path, help="JSON transcript to compare with")
    parser.add_argument("--timestamps", action="store_true", help="Batched: keep segment timestamps")
    parser.add_argument("--output", type=Path, help="Save the transcript as JSON")
    args = parser.parse_args()

    from faster_whisper import BatchedInferencePipeline, WhisperModel

    from app.pipeline.stt import build_prompt

    started = time.monotonic()
    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=args.threads)
    load_seconds = time.monotonic() - started

    terms = json.loads(args.prompt_file.read_text(encoding="utf-8")) if args.prompt_file else []
    options = {
        "vad_filter": True,
        "condition_on_previous_text": False,
        "beam_size": args.beam,
        **prompt_options(args.prompt_mode, build_prompt(terms)),
    }
    if args.batch:
        runner = BatchedInferencePipeline(model)
        options |= {"batch_size": args.batch, "without_timestamps": not args.timestamps}
    else:
        runner = model
        options["multilingual"] = True

    started = time.monotonic()
    segments, info = runner.transcribe(args.audio, **options)
    rows = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]
    seconds = time.monotonic() - started
    text = " ".join(row["text"] for row in rows)

    result = {
        "model": args.model,
        "beam": args.beam,
        "batch": args.batch,
        "threads": args.threads,
        "prompt": args.prompt_mode,
        "timestamps": args.timestamps or not args.batch,
        "load_s": round(load_seconds, 1),
        "seconds": round(seconds, 1),
        "rtf": round(seconds / info.duration, 3),
        "segments": len(rows),
        "longest_segment_s": round(max((row["end"] - row["start"] for row in rows), default=0), 1),
        "language": info.language,
        "latin_words": len(re.findall(r"[A-Za-z]{3,}", text)),
        "loops": find_loops(text),
    }
    if args.reference:
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
        result |= error_rates(" ".join(row["text"] for row in reference), text)
    if args.terms:
        result["terms"] = count_terms(text, json.loads(args.terms.read_text(encoding="utf-8")))
    if args.output:
        args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
