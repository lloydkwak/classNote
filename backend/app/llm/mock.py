"""Offline LLM for tests and keyless runs. Its notes quote the transcript verbatim."""

import json
import re
import struct

from app.llm.base import LLMProvider

# Prompts that want notes place their input as JSON after this marker.
INPUT_MARKER = "INPUT_JSON:"

MOCK_CHART_CAPTION = "Bar chart comparing the running time of merge sort and naive recursion"
MOCK_PHOTO_CAPTION = "Photo of a printed stack trace"

# The prefix is added afterward, so these leave room for it.
NOTE_LIMITS = {"ko": 32, "en": 50}
EXAMPLE_WORDS = re.compile(r"예를 들|for example|for instance", re.IGNORECASE)
# (spoken ending at the end of the sentence, note-style ending)
SPOKEN_ENDINGS = [
    (r"(입니다|이에요|예요|이죠|죠)$", "임"),
    (r"(합니다|해요|하죠|하세요|해 ?주세요)$", "함"),
    (r"(됩니다|돼요|되죠)$", "됨"),
    (r"(습니다|어요|아요|거죠|거예요|거에요|세요)$", "음"),
]

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


def _span_language(sentences: list[str], lecture_sample: str) -> str:
    """Like the real prompt: the lecture's main language, unless the whole span is another."""
    main = _language(lecture_sample or " ".join(sentences))
    if sentences and all(_language(sentence) != main for sentence in sentences):
        return _language(sentences[0])
    return main


def _condense(sentence: str, language: str) -> str:
    """Turn a spoken sentence into a note, by shortening what it already says."""
    text = re.sub(r"[.!?]+$", "", sentence.strip())
    if language == "ko":
        for spoken, written in SPOKEN_ENDINGS:
            text = re.sub(spoken, written, text)
    limit = NOTE_LIMITS.get(language, NOTE_LIMITS["en"])
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0]


def _note_type(sentence: str, is_candidate: bool) -> str:
    if is_candidate:
        return "exam_hint"
    if sentence.endswith("?"):
        return "question"
    if EXAMPLE_WORDS.search(sentence):
        return "example"
    return "explanation"


def _note_for(sentence: str, blocks: list[dict], is_candidate: bool) -> dict:
    best = max(blocks, key=lambda block: _overlap(sentence, block["text"]), default=None)
    linked = best is not None and _overlap(sentence, best["text"]) > 0
    language = _language(sentence)
    return {
        "block_ids": [best["block_id"]] if linked else [],
        "type": _note_type(sentence, is_candidate),
        "note": _condense(sentence, language),
        "quote": sentence,
        "language": language,
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
        if request.get("task") == "content_extraction":
            return json.dumps({"items": [
                {"type": "explanation", "title": sentence[:60], "content": sentence,
                 "source_quote": sentence, "short_note": _condense(sentence, _language(sentence)),
                 "block_ids": note["block_ids"],
                 "importance": 3, "language": note["language"]}
                for note in self._span_notes(request)
                for sentence in [note["quote"]]
            ]}, ensure_ascii=False)
        if "spans" in request:
            sample = request.get("lecture_sample", "")
            notes = [
                {**note, "span": span["span"]}
                for span in request["spans"]
                for note in self._span_notes({**span, "lecture_sample": sample})
            ]
        else:
            notes = self._span_notes(request)
        return json.dumps(notes, ensure_ascii=False)

    def _span_notes(self, request: dict) -> list[dict]:
        candidates = request.get("exam_hint_candidates", [])
        sentences = _sentences(request.get("transcript", ""))
        language = _span_language(sentences, request.get("lecture_sample", ""))
        # It can't translate, so it only writes notes on sentences already in that language.
        return [
            _note_for(
                sentence,
                request.get("blocks", []),
                any(candidate in sentence for candidate in candidates),
            )
            for sentence in sentences
            if _language(sentence) == language
        ]
