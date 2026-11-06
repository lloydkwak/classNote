"""Find where the professor's words refer to words on the current slide. No LLM.

These are hard evidence of what was pointed at. Spoken words are compared with the slide by
fuzzy matching, and Hangul at the jamo level, so that a misrecognition like "김알고사" still
matches "기말고사". A glossary adds spoken forms that can't match by spelling, such as
"베이스 케이스" for "base case".
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from app.models import Alignment, Line, Mention, Page, PdfExtraction, Segment, SpokenWord, Word
from app.pipeline.block_align import build_spans

MAX_WORDS = 4  # In one term, on the slide or in speech.
MIN_SCORE = 86.0
EXACT_ONLY_BELOW = 5  # Short keys match too much by accident, so they must be exact.
MIN_KEY_LENGTH = 3
MIN_ACRONYM_LENGTH = 2  # "EM" and "ML" count, though "in" and "of" don't.

HANGUL_FIRST = 0xAC00
HANGUL_LAST = 0xD7A3
LEADS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
VOWELS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
TAILS = ["", *"ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"]

# A Latin word and the Korean particle after it are one spoken word: "K-means를".
SPOKEN_TOKEN = re.compile(r"[가-힣]+|(?:(?![가-힣])\w)+(?:[-'](?:(?![가-힣])\w)+)*")
# Longest first, so "에서" is removed whole rather than leaving "에".
PARTICLES = (
    "이라고", "에서는", "으로는", "에서", "으로", "에는", "에게", "한테", "부터", "까지", "처럼",
    "보다", "라고", "이라", "은", "는", "이", "가", "을", "를", "에", "의", "로", "와", "과", "도",
    "만",
)
STOPWORDS = frozenset(
    "the and for are but not you all can was one our out has have had his her she him this that "
    "with from they will would there their what about which when make like time just know take "
    "into your some could them than then only its also how each use used using more most any "
    "may such these those been being were where while very".split()
)


def decompose_hangul(text: str) -> str:
    """Replace each Hangul syllable with its jamo, by Unicode arithmetic."""
    parts = []
    for char in text:
        code = ord(char)
        if HANGUL_FIRST <= code <= HANGUL_LAST:
            index = code - HANGUL_FIRST
            parts += [LEADS[index // 588], VOWELS[index % 588 // 28], TAILS[index % 28]]
        else:
            parts.append(char)
    return "".join(parts)


def _plain(text: str) -> str:
    """Lowercase letters and digits only, so "K-Means," and "k means" compare equal."""
    return "".join(char for char in unicodedata.normalize("NFKC", text).lower() if char.isalnum())


def match_key(text: str) -> str:
    return decompose_hangul(_plain(text))


def _without_particle(token: str) -> str | None:
    if not re.fullmatch(r"[가-힣]+", token):
        return None
    for particle in PARTICLES:
        if token.endswith(particle) and len(token) > len(particle):
            return token[: -len(particle)]
    return None


@dataclass
class _SpokenToken:
    text: str
    start: float
    end: float


def spoken_words(segment: Segment) -> list[SpokenWord]:
    """A segment's words, spacing them evenly when the provider gave no word times."""
    if segment.words:
        return segment.words
    texts = segment.text.split()
    step = (segment.end - segment.start) / max(len(texts), 1)
    return [
        SpokenWord(
            start=segment.start + index * step, end=segment.start + (index + 1) * step, text=text
        )
        for index, text in enumerate(texts)
    ]


def _spoken_tokens(segments: Sequence[Segment], start: float, end: float) -> list[_SpokenToken]:
    tokens = []
    for segment in segments:
        if segment.end < start or segment.start > end:
            continue
        for word in spoken_words(segment):
            if start <= (word.start + word.end) / 2 <= end:
                tokens += [
                    _SpokenToken(text, word.start, word.end)
                    for text in SPOKEN_TOKEN.findall(word.text)
                ]
    return tokens


def _is_meaningful(words: Sequence[Word]) -> bool:
    """Whether these slide words are worth matching at all."""
    if len(words) > 1:
        content = [word for word in words if _plain(word.text).lower() not in STOPWORDS]
        return bool(content) and _plain(words[0].text) not in STOPWORDS
    text = _plain(words[0].text)
    if text in STOPWORDS:
        return False
    acronym = words[0].text.strip(".,:;()[]").isupper()
    return len(text) >= (MIN_ACRONYM_LENGTH if acronym else MIN_KEY_LENGTH)


def _slide_terms(page: Page, glossary: Mapping[str, str]) -> list[tuple[str, list[Word]]]:
    """Every run of up to MAX_WORDS words in a line, as (comparison key, words).

    A run gets one more key for each glossary entry that spells it.
    """
    aliases: dict[str, list[str]] = {}
    for spoken, spelling in glossary.items():
        aliases.setdefault(match_key(spelling), []).append(match_key(spoken))
    words = {word.word_id: word for word in page.words}
    terms = []
    for line in page.lines:
        terms += _line_terms(line, words, aliases)
    return terms


def _line_terms(
    line: Line, words: Mapping[str, Word], aliases: Mapping[str, list[str]]
) -> list[tuple[str, list[Word]]]:
    in_line = [words[word_id] for word_id in line.word_ids]
    terms = []
    for first in range(len(in_line)):
        for count in range(1, MAX_WORDS + 1):
            run = in_line[first : first + count]
            if len(run) < count or not _is_meaningful(run):
                continue
            key = match_key("".join(word.text for word in run))
            terms += [(alias, run) for alias in aliases.get(key, [])]
            if len(key) >= MIN_ACRONYM_LENGTH:
                terms.append((key, run))
    return terms


def _spoken_terms(tokens: Sequence[_SpokenToken]) -> list[tuple[str, int, int]]:
    """Every run of up to MAX_WORDS tokens, as (key, first token, end token)."""
    terms = []
    for first in range(len(tokens)):
        for count in range(1, MAX_WORDS + 1):
            run = tokens[first : first + count]
            if len(run) < count:
                continue
            terms.append((match_key("".join(token.text for token in run)), first, first + count))
            bare = _without_particle(run[-1].text)
            if bare:
                joined = "".join(token.text for token in run[:-1]) + bare
                terms.append((match_key(joined), first, first + count))
    return terms


def _find_on_page(
    page: Page, tokens: Sequence[_SpokenToken], glossary: Mapping[str, str]
) -> list[Mention]:
    slide = _slide_terms(page, glossary)
    spoken = _spoken_terms(tokens)
    if not slide or not spoken:
        return []
    scores = process.cdist(
        [key for key, _ in slide],
        [key for key, _, _ in spoken],
        scorer=fuzz.ratio,
        score_cutoff=MIN_SCORE,
        workers=1,
    )
    found = []
    for row, column in zip(*scores.nonzero()):
        slide_key, words = slide[row]
        spoken_key, first, end = spoken[column]
        score = float(scores[row, column])
        if min(len(slide_key), len(spoken_key)) < EXACT_ONLY_BELOW and slide_key != spoken_key:
            continue
        found.append((len(slide_key) * (score / 100) ** 4, score, first, end, words))
    # Longest first, so "K-Means Algorithm" beats its own "K-Means" over the same speech, but
    # sharply favoring exact matches, or an unrelated next word would be pulled in by a fuzzy one.
    found.sort(key=lambda item: (-item[0], item[2]))

    mentions: dict[tuple[int, int, tuple[str, ...]], Mention] = {}
    taken: dict[tuple[int, int], float] = {}
    for weight, score, first, end, words in found:
        overlapping = [
            other for other in taken if first < other[1] and other[0] < end
        ]
        # The same speech can point at each place the term is written, but only that term.
        if overlapping and overlapping != [(first, end)]:
            continue
        if overlapping and taken[(first, end)] != weight:
            continue
        taken[(first, end)] = weight
        word_ids = tuple(word.word_id for word in words)
        run = tokens[first:end]
        mentions.setdefault(
            (first, end, word_ids),
            Mention(
                page=page.page,
                word_ids=list(word_ids),
                time=run[0].start,
                end=run[-1].end,
                spoken_text=" ".join(token.text for token in run),
                score=round(score, 1),
            ),
        )
    return sorted(mentions.values(), key=lambda mention: (mention.time, mention.word_ids))


def find_mentions(
    alignment: Alignment,
    extraction: PdfExtraction,
    segments: Sequence[Segment],
    glossary: Mapping[str, str] | None = None,
) -> list[Mention]:
    """Search each aligned span for its page's words. `glossary` maps spoken forms to spellings."""
    pages = {page.page: page for page in extraction.pages}
    mentions = []
    for span in build_spans(alignment):
        tokens = _spoken_tokens(segments, span.start, span.end)
        mentions += _find_on_page(pages[span.page], tokens, glossary or {})
    return mentions
