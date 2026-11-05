"""Offline LLM for tests and keyless runs. Its notes quote the transcript verbatim."""

import json
import re
import struct

from app.llm.base import LLMProvider

# Prompts that want notes place their input as JSON after this marker.
INPUT_MARKER = "INPUT_JSON:"

MOCK_CHART_CAPTION = "Bar chart comparing the running time of merge sort and naive recursion"
MOCK_PHOTO_CAPTION = "Photo of a printed stack trace"

HANGUL = re.compile(r"[가-힣]")
LATIN = re.compile(r"[A-Za-z]")


def _language(text: str) -> str:
    """Korean sentences often have more Latin letters than Hangul, from code-switching."""
    return "ko" if len(HANGUL.findall(text)) >= len(LATIN.findall(text)) / 4 else "en"


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def _overlap(sentence: str, block_text: str) -> int:
    words = {word.lower() for word in re.findall(r"[^\W_]{2,}", block_text)}
    return sum(word.lower() in words for word in re.findall(r"[^\W_]{2,}", sentence))


def _note_for(sentence: str, blocks: list[dict], is_candidate: bool) -> dict:
    best = max(blocks, key=lambda block: _overlap(sentence, block["text"]), default=None)
    linked = best is not None and _overlap(sentence, best["text"]) > 0
    return {
        "block_ids": [best["block_id"]] if linked else [],
        "type": "exam_hint" if is_candidate else "explanation",
        "note": sentence,
        "quote": sentence,
        "language": _language(sentence),
        "importance": 5 if is_candidate else 2,
    }


def _png_aspect_ratio(png: bytes) -> float:
    width, height = struct.unpack(">II", png[16:24])  # From the PNG header.
    return width / height


class MockProvider(LLMProvider):
    name = "mock"
    model = "mock"

    def describe_images(self, prompt: str, images: list[bytes]) -> str:
        """It can't see, so wide images are the sample chart and others the sample photo."""
        return json.dumps(
            [
                MOCK_CHART_CAPTION if _png_aspect_ratio(png) > 1.2 else MOCK_PHOTO_CAPTION
                for png in images
            ]
        )

    def complete_json(self, prompt: str) -> str:
        _, marker, payload = prompt.rpartition(INPUT_MARKER)
        if not marker:
            return json.dumps({"ok": True})  # A connection check, not a request for notes.
        request = json.loads(payload)
        candidates = request.get("exam_hint_candidates", [])
        notes = [
            _note_for(
                sentence,
                request.get("blocks", []),
                any(candidate in sentence for candidate in candidates),
            )
            for sentence in _sentences(request.get("transcript", ""))
        ]
        return json.dumps(notes, ensure_ascii=False)
