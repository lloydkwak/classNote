import math
import random

import pytest

from app.models import Block
from app.pipeline.layout import (
    LayoutNote,
    fallback_font,
    gutter_width,
    handwriting_font,
    layout_page,
    line_needs_fallback,
    wrap_text,
)

WIDTH, HEIGHT = 960, 540


def block(bbox, kind="text", block_id="b"):
    return Block(block_id=block_id, page=1, text="x", bbox=bbox, kind=kind)


def note(note_id="n", text="A short note", blocks=(), double=False):
    return LayoutNote(id=note_id, text=text, double=double, blocks=list(blocks))


def only(*args, **kwargs):
    return layout_page([note(*args, **kwargs)], WIDTH, HEIGHT)[0]


def assert_no_overlaps(sketches):
    for side in ("left", "right"):
        boxes = sorted(
            (sketch.y, sketch.y + sketch.height) for sketch in sketches if sketch.side == side
        )
        for (_, bottom), (top, _) in zip(boxes, boxes[1:]):
            assert bottom <= top + 1e-6


def assert_inside_the_margins(sketches, width=WIDTH, height=HEIGHT):
    gutter = gutter_width(width)
    for sketch in sketches:
        assert sketch.y >= -1e-6
        assert sketch.y + sketch.height <= height + 1e-6
        if sketch.side == "left":
            assert -gutter <= sketch.x and sketch.x + sketch.width <= 0
        else:
            assert width <= sketch.x and sketch.x + sketch.width <= width + gutter


def test_text_block_gets_an_underline_below_it():
    sketch = only(blocks=[block((0.1, 0.2, 0.5, 0.3))])
    assert sketch.mark == "underline"
    assert len(sketch.strokes) == 1
    xs = [x for x, _ in sketch.strokes[0]]
    ys = [y for _, y in sketch.strokes[0]]
    assert min(xs) == pytest.approx(0.1 * WIDTH, abs=2)
    assert max(xs) == pytest.approx(0.5 * WIDTH, abs=2)
    assert all(abs(y - 0.3 * HEIGHT) < 6 for y in ys)
    assert len({round(y, 2) for y in ys}) > 1  # It wobbles, like a hand.


def test_exam_hint_is_underlined_twice():
    sketch = only(blocks=[block((0.1, 0.2, 0.5, 0.3))], double=True)
    assert len(sketch.strokes) == 2


def test_figure_gets_a_circle_that_surrounds_it():
    sketch = only(blocks=[block((0.5, 0.3, 0.9, 0.8), kind="figure")])
    assert sketch.mark == "circle"
    xs = [x for x, _ in sketch.strokes[0]]
    ys = [y for _, y in sketch.strokes[0]]
    assert min(xs) < 0.5 * WIDTH and max(xs) > 0.9 * WIDTH
    assert min(ys) < 0.3 * HEIGHT and max(ys) > 0.8 * HEIGHT


def test_very_long_figure_gets_a_bracket_on_the_margin_side():
    sketch = only(blocks=[block((0.05, 0.4, 0.45, 0.45), kind="figure")])
    assert sketch.mark == "bracket"
    assert sketch.side == "left"
    assert min(x for x, _ in sketch.strokes[0]) < 0.05 * WIDTH


def test_note_goes_in_the_nearest_margin():
    assert only(blocks=[block((0.05, 0.2, 0.3, 0.3))]).side == "left"
    assert only(blocks=[block((0.7, 0.2, 0.95, 0.3))]).side == "right"


def test_note_stays_near_its_target_when_there_is_room():
    sketch = only(blocks=[block((0.7, 0.6, 0.9, 0.7))])
    center = sketch.y + sketch.height / 2
    assert center == pytest.approx(0.65 * HEIGHT, abs=1)


def test_arrow_runs_from_the_note_to_the_mark():
    sketch = only(blocks=[block((0.7, 0.2, 0.9, 0.3))])
    shaft = sketch.arrow[0]
    assert shaft[0][0] == pytest.approx(sketch.x - 3)
    mark_points = [point for stroke in sketch.strokes for point in stroke]
    assert any(math.dist(shaft[-1], point) < 1 for point in mark_points)
    assert len(sketch.arrow) == 3  # A shaft and two head strokes.


def test_left_arrow_starts_at_the_end_of_short_text():
    sketch = only(text="Short", blocks=[block((0.1, 0.2, 0.3, 0.3))])
    assert sketch.side == "left"
    assert sketch.arrow[0][0][0] < sketch.x + sketch.width / 2


def test_page_level_note_has_no_mark_or_arrow():
    sketch = only()
    assert sketch.mark == "none"
    assert sketch.strokes == []
    assert sketch.arrow == []


def test_lines_fit_the_note_width():
    sketch = only(text="이 base case가 없으면 recursion이 멈추지 않습니다. " * 4)
    assert len(sketch.lines) > 1
    font = handwriting_font()
    for line in sketch.lines:
        assert font.text_length(line, fontsize=sketch.font_size) <= sketch.width + 0.01


def test_long_word_is_broken_inside_the_line_width():
    lines = wrap_text("x" * 400, 100, 16)
    assert len(lines) > 1
    assert "".join(lines) == "x" * 400


def test_symbols_missing_from_the_handwriting_font_use_the_fallback():
    assert line_needs_fallback("λ → x")
    assert not line_needs_fallback("이 recursion")
    assert fallback_font().has_glyph(ord("λ"))


def test_same_note_is_drawn_the_same_way_every_time():
    blocks = [block((0.1, 0.2, 0.5, 0.3))]
    assert only(blocks=blocks) == only(blocks=blocks)


def test_notes_targeting_the_same_spot_do_not_overlap():
    target = [block((0.6, 0.4, 0.9, 0.5))]
    notes = [note(f"n{index}", "한 줄 정도의 설명입니다. " * 2, target) for index in range(5)]
    sketches = layout_page(notes, WIDTH, HEIGHT)
    assert_no_overlaps(sketches)
    assert_inside_the_margins(sketches)
    assert {sketch.side for sketch in sketches} == {"right"}


def test_too_many_long_notes_spill_over_shrink_and_still_do_not_overlap():
    target = [block((0.6, 0.1, 0.9, 0.2))]
    notes = [note(f"n{index}", "긴 설명 문장입니다 recursion. " * 30, target) for index in range(5)]
    sketches = layout_page(notes, WIDTH, HEIGHT)
    assert_no_overlaps(sketches)
    assert_inside_the_margins(sketches)
    assert {sketch.side for sketch in sketches} == {"left", "right"}


@pytest.mark.parametrize("seed", range(60))
def test_random_pages_never_overlap_or_leave_the_margins(seed):
    rng = random.Random(seed)
    width, height = rng.choice([(960, 540), (720, 540), (595, 842), (842, 595)])
    notes = []
    for index in range(rng.randint(1, 5)):
        x0, y0 = rng.uniform(0, 0.8), rng.uniform(0, 0.8)
        blocks = [
            block(
                (x0, y0, x0 + rng.uniform(0.05, 0.2), y0 + rng.uniform(0.02, 0.2)),
                kind=rng.choice(["text", "figure"]),
            )
        ]
        text = "설명 words 입니다 " * rng.randint(1, 40)
        notes.append(note(f"n{index}", text, blocks if rng.random() > 0.2 else []))
    sketches = layout_page(notes, width, height)
    assert_no_overlaps(sketches)
    assert_inside_the_margins(sketches, width, height)
