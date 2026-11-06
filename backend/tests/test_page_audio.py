from app.models import AlignedWindow, Alignment
from app.pipeline import page_audio as module
from app.pipeline.page_audio import build_page_audio


def _window(window_id, start, end, page, confidence=0.5):
    return AlignedWindow(
        window_id=window_id,
        start=start,
        end=end,
        text="speech",
        page=page,
        confidence=confidence,
        raw_page=page or 1,
        similarities=[0.0],
    )


def _alignment(*rows, pages=(1, 2, 3)):
    return Alignment(
        pages=list(pages),
        windows=[_window(index, *row) for index, row in enumerate(rows)],
    )


def _ranges(result, page):
    entry = next(item for item in result.pages if item.page == page)
    return [(item.start, item.end) for item in entry.audio_ranges]


def test_a_page_with_no_speech_is_listed_with_no_ranges():
    result = build_page_audio(_alignment((0, 30, 1)))
    entry = next(item for item in result.pages if item.page == 2)
    assert entry.audio_ranges == []
    assert entry.primary_start is None
    assert entry.total_duration == 0


def test_consecutive_windows_become_one_range():
    result = build_page_audio(_alignment((10, 40, 1), (40, 70, 1)))
    assert _ranges(result, 1) == [(9.75, 70.0)]
    assert result.pages[0].audio_ranges[0].source_window_ids == ["w0", "w1"]


def test_a_revisit_keeps_separate_ranges_in_time_order():
    result = build_page_audio(_alignment((0, 30, 1), (30, 60, 2), (60, 90, 1)))
    ranges = result.pages[0].audio_ranges
    assert [(item.start, item.end) for item in ranges] == [(0.0, 30.0), (60.0, 90.0)]
    assert [item.reason for item in ranges] == ["first_visit", "revisit"]
    assert result.pages[0].primary_start == 0.0
    assert result.pages[0].total_duration == 60.0


def test_going_back_to_an_earlier_page_is_preserved():
    result = build_page_audio(_alignment((0, 30, 2), (30, 60, 3), (60, 90, 2), (90, 120, 3)))
    assert _ranges(result, 2) == [(0.0, 30.0), (60.0, 90.0)]
    assert _ranges(result, 3) == [(30.0, 60.0), (90.0, 120.0)]


def test_a_skipped_page_gets_nothing():
    result = build_page_audio(_alignment((0, 30, 1), (30, 60, 3)))
    assert _ranges(result, 2) == []


def test_another_pages_speech_is_never_merged_in():
    """Even when the other page's window is very short."""
    result = build_page_audio(_alignment((0, 30, 1), (30, 31, 2), (31, 60, 1)))
    assert _ranges(result, 1) == [(0.0, 30.0), (31.0, 60.0)]
    assert _ranges(result, 2) == [(30.0, 31.0)]


def test_off_topic_is_not_assigned_to_any_page():
    result = build_page_audio(_alignment((0, 30, 1), (30, 60, None), (60, 90, 1)))
    assert _ranges(result, 1) == [(0.0, 30.0), (60.0, 90.0)]
    assert result.unmatched_seconds == 30.0
    assert all("w1" not in item.source_window_ids for entry in result.pages for item in entry.audio_ranges)


def test_a_short_pause_merges_and_a_long_silence_splits():
    short = build_page_audio(_alignment((0, 30, 1), (31, 60, 1)))
    assert _ranges(short, 1) == [(0.0, 60.0)]
    gap = module.MERGE_GAP_SECONDS + 1
    long = build_page_audio(_alignment((0, 30, 1), (30 + gap, 60, 1)))
    assert len(_ranges(long, 1)) == 2


def test_silence_before_the_first_word_is_not_part_of_a_range():
    result = build_page_audio(_alignment((35, 60, 1)))
    assert _ranges(result, 1) == [(34.75, 60.0)]


def test_ranges_stay_inside_the_recording():
    result = build_page_audio(_alignment((0.1, 30, 1), (30, 60, 2)), duration=59.5)
    assert _ranges(result, 1)[0][0] == 0.0
    assert _ranges(result, 2)[0][1] == 59.5


def test_padding_never_overlaps_a_neighbor():
    result = build_page_audio(_alignment((0, 30, 1), (30.1, 60, 2)))
    assert _ranges(result, 1)[0][1] <= _ranges(result, 2)[0][0]


def test_confidence_is_weighted_by_length():
    result = build_page_audio(_alignment((0, 10, 1, 1.0), (10, 40, 1, 0.0)))
    assert result.pages[0].audio_ranges[0].confidence == 0.25


def test_times_are_the_recordings_own_clock():
    """A window at an hour stays at an hour, with no shift from dropped silence."""
    result = build_page_audio(_alignment((3600, 3630, 2)))
    assert _ranges(result, 2) == [(3599.75, 3630.0)]


def test_an_empty_alignment_lists_every_page():
    result = build_page_audio(_alignment(pages=(1, 2)))
    assert [entry.page for entry in result.pages] == [1, 2]
    assert result.duration == 0


def test_an_empty_window_is_ignored():
    result = build_page_audio(_alignment((5, 5, 1), (5, 30, 2)))
    assert _ranges(result, 1) == []


def test_schema_version():
    assert build_page_audio(_alignment()).schema_version == 2
