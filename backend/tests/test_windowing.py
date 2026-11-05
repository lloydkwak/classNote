from app.models import Segment
from app.pipeline.windowing import build_windows


def segments(*spans):
    return [Segment(start=start, end=end, text=f"{start}-{end}") for start, end in spans]


def test_groups_segments_into_windows_of_about_the_target_length():
    windows = build_windows(segments((0, 10), (10, 20), (20, 30), (30, 40), (40, 50)), 30)
    assert [(window.start, window.end) for window in windows] == [(0, 30), (30, 50)]
    assert windows[0].text == "0-10 10-20 20-30"
    assert [window.window_id for window in windows] == [0, 1]


def test_never_cuts_inside_a_segment():
    source = segments((0, 25), (25, 70), (70, 80))
    windows = build_windows(source, 30)
    assert [(window.start, window.end) for window in windows] == [(0, 70), (70, 80)]
    joined = " ".join(window.text for window in windows)
    assert joined == " ".join(segment.text for segment in source)


def test_window_length_is_configurable():
    assert len(build_windows(segments((0, 10), (10, 20)), 10)) == 2


def test_no_segments_gives_no_windows():
    assert build_windows([], 30) == []
