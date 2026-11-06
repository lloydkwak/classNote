from app.models import (
    AlignedWindow,
    Alignment,
    Line,
    Page,
    PdfExtraction,
    Segment,
    SpokenWord,
    Word,
)
from app.pipeline.mentions import decompose_hangul, find_mentions, match_key, spoken_words


def _page(*lines: str) -> Page:
    """A page with one line per string, and an id for each word."""
    words, rows = [], []
    for row, text in enumerate(lines):
        ids = []
        for column, word in enumerate(text.split()):
            word_id = f"p1_w{len(words)}"
            ids.append(word_id)
            words.append(
                Word(
                    word_id=word_id,
                    text=word,
                    bbox=(column / 10, row / 10, (column + 1) / 10, (row + 1) / 10),
                    line_id=f"p1_l{row}",
                )
            )
        rows.append(
            Line(line_id=f"p1_l{row}", word_ids=ids, text=text, bbox=(0, row / 10, 1, (row + 1) / 10))
        )
    return Page(page=1, title="", blocks=[], words=words, lines=rows)


def _mentions(page: Page, speech: str, glossary=None, words=None):
    alignment = Alignment(
        pages=[1],
        windows=[
            AlignedWindow(
                window_id=0,
                start=0,
                end=100,
                text=speech,
                page=1,
                confidence=1,
                raw_page=1,
                similarities=[1],
            )
        ],
    )
    segment = Segment(start=0, end=100, text=speech, words=words or [])
    return find_mentions(alignment, PdfExtraction(pages=[page], key_terms=[]), [segment], glossary)


def _word_texts(page: Page, mention) -> str:
    by_id = {word.word_id: word.text for word in page.words}
    return " ".join(by_id[word_id] for word_id in mention.word_ids)


def test_decomposes_hangul_by_unicode_arithmetic():
    assert decompose_hangul("각") == "ㄱㅏㄱ"
    assert decompose_hangul("기말") == "ㄱㅣㅁㅏㄹ"
    assert decompose_hangul("a가") == "aㄱㅏ"


def test_jamo_comparison_matches_a_misrecognized_word():
    page = _page("기말고사 범위")
    found = _mentions(page, "이건 김알고사 에 나옵니다")
    assert [_word_texts(page, mention) for mention in found] == ["기말고사"]
    assert found[0].spoken_text == "김알고사"
    assert found[0].score > 86


def test_unrelated_words_do_not_match():
    assert _mentions(_page("기말고사 범위"), "오늘 날씨가 좋습니다 hello world") == []


def test_ignores_case_and_punctuation():
    assert match_key("K-Means,") == match_key("k means")
    page = _page("The K-Means algorithm")
    found = _mentions(page, "k-means uses centroids")
    assert [_word_texts(page, mention) for mention in found] == ["K-Means"]


def test_separates_a_korean_particle_from_a_latin_word():
    page = _page("K-Means")
    found = _mentions(page, "K-means를 봅시다")
    assert [mention.spoken_text for mention in found] == ["K-means"]


def test_strips_a_korean_particle_from_a_korean_word():
    page = _page("클러스터링 예제")
    found = _mentions(page, "클러스터링에서 중요한")
    assert [_word_texts(page, mention) for mention in found] == ["클러스터링"]


def test_prefers_the_longest_term():
    page = _page("Gaussian Mixture Model")
    found = _mentions(page, "a gaussian mixture model here")
    assert [_word_texts(page, mention) for mention in found] == ["Gaussian Mixture Model"]


def test_short_words_must_match_exactly():
    page = _page("EM step")
    assert [_word_texts(page, m) for m in _mentions(page, "the EM runs")] == ["EM"]
    assert _mentions(page, "the EN runs") == []


def test_stopwords_are_not_mentions():
    assert _mentions(_page("the"), "the the the") == []


def test_finds_a_term_everywhere_it_is_written():
    page = _page("centroid one", "centroid two")
    found = _mentions(page, "the centroid moves")
    assert len(found) == 2
    assert {mention.word_ids[0] for mention in found} == {"p1_w0", "p1_w2"}


def test_glossary_matches_a_transliteration():
    page = _page("the base case")
    assert _mentions(page, "베이스 케이스 입니다") == []
    found = _mentions(page, "베이스 케이스 입니다", {"베이스 케이스": "base case"})
    assert [_word_texts(page, mention) for mention in found] == ["base case"]
    assert found[0].spoken_text == "베이스 케이스"


def test_uses_word_times():
    page = _page("centroid")
    words = [
        SpokenWord(start=10, end=11, text="the"),
        SpokenWord(start=12.5, end=13.5, text="centroid"),
    ]
    found = _mentions(page, "the centroid", words=words)
    assert (found[0].time, found[0].end) == (12.5, 13.5)


def test_spaces_words_evenly_without_word_times():
    words = spoken_words(Segment(start=10, end=14, text="one two three four"))
    assert [word.start for word in words] == [10, 11, 12, 13]
    assert words[-1].end == 14


def test_only_searches_speech_aligned_to_the_page():
    page = _page("centroid")
    alignment = Alignment(
        pages=[1],
        windows=[
            AlignedWindow(
                window_id=0,
                start=0,
                end=10,
                text="centroid",
                page=None,
                confidence=0,
                raw_page=1,
                similarities=[0],
            ),
            AlignedWindow(
                window_id=1,
                start=10,
                end=20,
                text="centroid",
                page=1,
                confidence=1,
                raw_page=1,
                similarities=[1],
            ),
        ],
    )
    segments = [
        Segment(start=0, end=10, text="centroid"),
        Segment(start=10, end=20, text="centroid"),
    ]
    found = find_mentions(alignment, PdfExtraction(pages=[page], key_terms=[]), segments)
    assert [mention.time for mention in found] == [10]
