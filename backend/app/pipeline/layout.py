"""Work out how each note is drawn by hand: its mark, its margin position, and its arrow.

This is the single source of the geometry. The viewer and annotated.pdf draw the result as
is, so they look the same, and notes that don't overlap here don't overlap in either.
"""

import math
import random
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pymupdf

from app.models import Block, Point, Sketch

FONTS_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"

GUTTER_RATIO = 0.28  # Of page width, per side. The frontend uses the same value.
GUTTER_PADDING = 0.012  # Of page width.
NOTE_GAP = 0.8  # Of a line, between notes.
FONT_SHRINK = 0.9
CIRCLE_MAX_ASPECT = 2.6  # Longer or narrower figures get a bracket.
CIRCLE_MARGIN = 1.12
WOBBLE_STEP = 18.0  # Points between hand-drawn points.
WOBBLE = 0.7  # Points of jitter.
ARROW_HEAD = 7.0  # Points.


@dataclass(frozen=True)
class NoteFont:
    file: str
    # Sizes are fractions of page width, like the gutter, so a line holds about the same
    # number of characters on any slide shape, and sizes on screen stay predictable.
    size_ratio: float
    min_size_ratio: float
    line_spacing: float


NOTE_FONTS = {
    "pretendard": NoteFont("Pretendard-Medium.otf", 1 / 50, 1 / 60, 1.3),
    # This handwriting font looks small for its size.
    "nanum-pen": NoteFont("NanumPenScript-Regular.ttf", 1 / 39, 1 / 60, 1.1),
}


@dataclass
class LayoutNote:
    id: str
    text: str
    double: bool  # Underline twice, for exam hints.
    blocks: list[Block]
    mark: str | None = None
    target_kind: str | None = None
    rotation: int = 0
    agent_action: dict | None = None


def note_font_path(name: str) -> Path:
    return FONTS_DIR / NOTE_FONTS[name].file


@lru_cache(maxsize=None)
def load_note_font(name: str) -> pymupdf.Font:
    return pymupdf.Font(fontfile=str(note_font_path(name)))


@lru_cache(maxsize=1)
def fallback_font() -> pymupdf.Font:
    return pymupdf.Font("cjk")


def text_runs(line: str, font_name: str) -> list[tuple[str, bool]]:
    """Split a line into (text, uses the fallback font) runs, as a browser does by glyph.

    Note fonts lack some symbols, such as "∴". Changing only those keeps the rest of the
    line in the note font.
    """
    font = load_note_font(font_name)
    runs: list[tuple[str, bool]] = []
    for char in line:
        # Keep math in the existing CJK math-capable fallback. The bundled handwriting /
        # Pretendard font advertises some operator glyphs that render as icons in PDFs.
        math_char=bool(re.fullmatch(r"[=+−α-ωΑ-Ω()\[\]{}→←↔⇒⇔-]",char))
        fallback = math_char or (not char.isspace() and not font.has_glyph(ord(char)))
        if runs and (runs[-1][1] == fallback or char.isspace()):
            runs[-1] = (runs[-1][0] + char, runs[-1][1])
        else:
            runs.append((char, fallback))
    return runs


def run_width(text: str, fallback: bool, font_name: str, font_size: float) -> float:
    font = fallback_font() if fallback else load_note_font(font_name)
    return font.text_length(text, fontsize=font_size)


def text_width(text: str, font_name: str, font_size: float) -> float:
    return sum(
        run_width(run, fallback, font_name, font_size)
        for run, fallback in text_runs(text, font_name)
    )


def gutter_width(page_width: float) -> float:
    return page_width * GUTTER_RATIO


def wrap_text(text: str, width: float, font_size: float, font_name: str) -> list[str]:
    """Break at spaces, and inside a word only when it's wider than the whole line."""
    lines: list[str] = []
    current = ""
    for word in re.findall(r"\S+", text):
        candidate = f"{current} {word}" if current else word
        if text_width(candidate, font_name, font_size) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        for char in word:
            if text_width(current + char, font_name, font_size) > width and current:
                lines.append(current)
                current = ""
            current += char
    if current:
        lines.append(current)
    return lines or [""]


def _wobbly_line(start: Point, end: Point, rng: random.Random) -> list[Point]:
    length = math.dist(start, end)
    count = max(2, int(length / WOBBLE_STEP) + 1)
    points = []
    for index in range(count + 1):
        t = index / count
        jitter = 0 if index in (0, count) else WOBBLE
        points.append(
            (
                start[0] + (end[0] - start[0]) * t + rng.uniform(-jitter, jitter),
                start[1] + (end[1] - start[1]) * t + rng.uniform(-jitter, jitter),
            )
        )
    return points


def _underline(rect: tuple, double: bool, rng: random.Random) -> list[list[Point]]:
    x0, _, x1, y1 = rect
    tilt = rng.uniform(-1.0, 1.0)
    strokes = [_wobbly_line((x0, y1 + 1.5), (x1, y1 + 1.5 + tilt), rng)]
    if double:
        strokes.append(_wobbly_line((x0 + 3, y1 + 5), (x1 - 3, y1 + 5 + tilt), rng))
    return strokes


def _circle(rect: tuple, rng: random.Random) -> list[list[Point]]:
    """An ellipse that overshoots its start, as a hand-drawn circle does."""
    x0, y0, x1, y1 = rect
    center_x, center_y = (x0 + x1) / 2, (y0 + y1) / 2
    radius_x = (x1 - x0) / 2 * CIRCLE_MARGIN * math.sqrt(2) / 1.25
    radius_y = (y1 - y0) / 2 * CIRCLE_MARGIN * math.sqrt(2) / 1.25
    start_angle = rng.uniform(-2.6, -2.0)
    drift = rng.uniform(1.0, 1.04)
    count = 48
    points = []
    for index in range(count + 1):
        t = index / count
        angle = start_angle + t * (2 * math.pi + 0.3)
        grow = 1 + (drift - 1) * t
        points.append(
            (
                center_x + radius_x * grow * math.cos(angle) + rng.uniform(-0.5, 0.5),
                center_y + radius_y * grow * math.sin(angle) + rng.uniform(-0.5, 0.5),
            )
        )
    return [points]


def _bracket(rect: tuple, side: str, rng: random.Random) -> list[list[Point]]:
    x0, y0, x1, y1 = rect
    tick = min(10.0, (x1 - x0) / 4)
    edge = x0 - 5 if side == "left" else x1 + 5
    inward = tick if side == "left" else -tick
    return [
        _wobbly_line((edge + inward, y0), (edge, y0), rng)
        + _wobbly_line((edge, y0), (edge, y1), rng)[1:]
        + _wobbly_line((edge, y1), (edge + inward, y1), rng)[1:]
    ]


def _block_rect(block: Block, width: float, height: float) -> tuple:
    x0, y0, x1, y1 = block.bbox
    return x0 * width, y0 * height, x1 * width, y1 * height


def _mark_for(
    note: LayoutNote, side: str, width: float, height: float, rng: random.Random
) -> tuple[str, list[list[Point]], Point | None]:
    """Return the mark's kind, its strokes, and the point the arrow should reach."""
    if not note.blocks:
        return "none", [], None
    strokes: list[list[Point]] = []
    kind = "underline"
    for block in note.blocks:
        rect = _block_rect(block, width, height)
        if note.mark is not None:
            kind=note.mark
            if kind=="circle":
                strokes += _circle(rect,rng)
            elif kind=="bracket":
                strokes += _bracket(rect,side,rng)
            elif kind=="underline":
                strokes += _underline(rect,note.double,rng)
            continue
        if block.kind != "figure":
            strokes += _underline(rect, note.double, rng)
            continue
        aspect = (rect[2] - rect[0]) / max(rect[3] - rect[1], 1e-6)
        if 1 / CIRCLE_MAX_ASPECT <= aspect <= CIRCLE_MAX_ASPECT:
            kind = "circle"
            strokes += _circle(rect, rng)
        else:
            kind = "bracket"
            strokes += _bracket(rect, side, rng)
    if not strokes:
        return "none",[],None
    near = min if side == "left" else max
    anchor = near(
        (point for stroke in strokes for point in stroke),
        key=lambda point: point[0],
    )
    return kind, strokes, anchor


def _arrow(start: Point, end: Point, rng: random.Random) -> list[list[Point]]:
    """A slightly curved shaft and a two-stroke head."""
    mid = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    bend = rng.uniform(0.06, 0.14) * math.dist(start, end) * rng.choice((-1, 1))
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    control = (mid[0] - math.sin(angle) * bend, mid[1] + math.cos(angle) * bend)
    count = max(4, int(math.dist(start, end) / WOBBLE_STEP))
    shaft = []
    for index in range(count + 1):
        t = index / count
        x = (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * control[0] + t**2 * end[0]
        y = (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * control[1] + t**2 * end[1]
        jitter = 0 if index in (0, count) else WOBBLE / 2
        shaft.append((x + rng.uniform(-jitter, jitter), y + rng.uniform(-jitter, jitter)))
    tail = shaft[-2]
    heading = math.atan2(end[1] - tail[1], end[0] - tail[0])
    heads = [
        [
            (
                end[0] - ARROW_HEAD * math.cos(heading + spread),
                end[1] - ARROW_HEAD * math.sin(heading + spread),
            ),
            end,
        ]
        for spread in (0.45, -0.45)
    ]
    return [shaft, *heads]


@dataclass
class _Placed:
    note: LayoutNote
    lines: list[str]
    height: float
    target_y: float
    side: str
    y: float = 0.0


def _stack(items: list[_Placed], page_height: float, gap: float) -> bool:
    """Place notes near their targets without overlap. Returns False if they don't fit."""
    items.sort(key=lambda item: item.target_y)
    total = sum(item.height for item in items) + gap * max(len(items) - 1, 0)
    if total > page_height:
        return False
    cursor = 0.0
    for item in items:
        item.y = max(cursor, item.target_y - item.height / 2)
        cursor = item.y + item.height + gap
    limit = page_height
    for item in reversed(items):  # Pull back up from the bottom edge.
        item.y = min(item.y, limit - item.height)
        limit = item.y - gap
    return True


def _nearest_side(note: LayoutNote, width: float) -> str:
    if not note.blocks:
        return "right"
    center = sum((block.bbox[0] + block.bbox[2]) / 2 for block in note.blocks) / len(note.blocks)
    return "left" if center < 0.5 else "right"


def _target_y(note: LayoutNote, height: float) -> float:
    if not note.blocks:
        return 0.0
    return height * sum((block.bbox[1] + block.bbox[3]) / 2 for block in note.blocks) / len(note.blocks)


def _assign(items: list[_Placed], page_height: float, gap: float) -> bool:
    """Prefer each note's nearest margin, moving notes across when a margin is full."""
    left = [item for item in items if item.side == "left"]
    right = [item for item in items if item.side == "right"]
    for full, other, name in ((left, right, "right"), (right, left, "left")):
        while not _stack(full, page_height, gap) and full:
            moved = max(full, key=lambda item: item.height)
            full.remove(moved)
            moved.side = name
            other.append(moved)
    return _stack(left, page_height, gap) and _stack(right, page_height, gap)


def layout_page(
    notes: list[LayoutNote], width: float, height: float, font_name: str = "pretendard"
) -> list[Sketch]:
    """Return one sketch per note, in the same order."""
    gutter = gutter_width(width)
    padding = width * GUTTER_PADDING
    note_width = gutter - 2 * padding
    note_font = NOTE_FONTS[font_name]
    font_size = width * note_font.size_ratio
    # Below this it's hard to read on a projector, so long notes get cut instead.
    minimum = width * note_font.min_size_ratio

    while True:
        line_height = font_size * note_font.line_spacing
        gap = line_height * NOTE_GAP
        items = []
        for note in notes:
            lines = wrap_text(note.text, note_width, font_size, font_name)
            items.append(
                _Placed(
                    note,
                    lines,
                    len(lines) * line_height,
                    _target_y(note, height),
                    _nearest_side(note, width),
                )
            )
        if _assign(items, height, gap):
            break
        if font_size > minimum:
            font_size = max(font_size * FONT_SHRINK, minimum)
            continue
        # Even the smallest writing doesn't fit, so shorten the longest note and retry.
        longest = max(notes, key=lambda note: len(note.text))
        longest.text = longest.text[: max(1, int(len(longest.text) * 0.8))].rstrip() + "…"

    sketches = {}
    for item in items:
        note = item.note
        rng = random.Random(note.id)  # The same note always looks the same.
        mark, strokes, anchor = _mark_for(note, item.side, width, height, rng)
        x = -gutter + padding if item.side == "left" else width + padding
        arrow: list[list[Point]] = []
        if anchor is not None:
            # Start from the end of the text, not the note's box, which may be much wider.
            longest_line = max(text_width(line, font_name, font_size) for line in item.lines)
            edge = x + longest_line + 3 if item.side == "left" else x - 3
            arrow = _arrow((edge, item.y + line_height * 0.6), anchor, rng)
        sketches[note.id] = Sketch(
            mark=mark,
            strokes=strokes,
            arrow=arrow,
            side=item.side,
            x=x,
            y=item.y,
            width=note_width,
            height=item.height,
            font_size=font_size,
            line_height=line_height,
            lines=item.lines,
        )
    return [sketches[note.id] for note in notes]
