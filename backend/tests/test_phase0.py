"""Tests for Phase 0 referee infrastructure."""

import json
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
GOLDEN_DIR  = Path(__file__).resolve().parents[2] / "samples" / "golden"


# ---------------------------------------------------------------------------
# judge_pages.py — automatic layout metrics
# ---------------------------------------------------------------------------

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from judge_pages import (
    _seg, _segments_cross, _bbox_overlap, _possibly_truncated,
    _layout_metrics_for_page,
)


def test_segment_intersection_cross():
    assert _seg((0, 0), (1, 1), (0, 1), (1, 0)) is True


def test_segment_intersection_parallel():
    assert _seg((0, 0), (1, 0), (0, 1), (1, 1)) is False


def test_segments_cross_detects_x():
    path_a = [[0, 0], [2, 2]]
    path_b = [[0, 2], [2, 0]]
    assert _segments_cross(path_a, path_b) is True


def test_segments_cross_no_cross():
    path_a = [[0, 0], [1, 0]]
    path_b = [[0, 1], [1, 1]]
    assert _segments_cross(path_a, path_b) is False


def test_bbox_overlap_true():
    assert _bbox_overlap([0, 0, 2, 2], [1, 1, 3, 3]) is True


def test_bbox_overlap_touching():
    assert _bbox_overlap([0, 0, 1, 1], [1, 0, 2, 1]) is False


def test_possibly_truncated_no_punct():
    assert _possibly_truncated("some mid-word text") is True


def test_possibly_truncated_with_period():
    assert _possibly_truncated("This is a sentence.") is False


def test_possibly_truncated_empty():
    assert _possibly_truncated("") is False


def test_layout_metrics_empty_page():
    m = _layout_metrics_for_page({"notes": []}, 595.0, 842.0)
    assert m["marks"] == 0
    assert m["connector_crossings"] == 0


def test_layout_metrics_one_note_no_arrow():
    page_ann = {
        "notes": [{
            "text": "test note.",
            "sketch": {"x": 10, "y": 20, "width": 100, "height": 30, "arrow": [], "strokes": []}
        }]
    }
    m = _layout_metrics_for_page(page_ann, 595.0, 842.0)
    assert m["marks"] == 1
    assert m["connector_crossings"] == 0
    assert m["possibly_truncated"] == 0


def test_layout_metrics_overlapping_boxes():
    page_ann = {
        "notes": [
            {"text": "note A.", "sketch": {"x": 0, "y": 0, "width": 100, "height": 50, "arrow": [], "strokes": []}},
            {"text": "note B.", "sketch": {"x": 50, "y": 20, "width": 100, "height": 50, "arrow": [], "strokes": []}},
        ]
    }
    m = _layout_metrics_for_page(page_ann, 595.0, 842.0)
    assert m["box_overlaps"] == 1


def test_layout_metrics_crossing_arrows():
    # arrow is a list of sub-paths; each sub-path is a list of [x, y] points
    page_ann = {
        "notes": [
            {"text": "A.", "sketch": {
                "x": 300, "y": 0, "width": 50, "height": 20,
                "arrow": [[[0, 0], [200, 200]]],
                "strokes": [],
            }},
            {"text": "B.", "sketch": {
                "x": 300, "y": 50, "width": 50, "height": 20,
                "arrow": [[[0, 200], [200, 0]]],
                "strokes": [],
            }},
        ]
    }
    m = _layout_metrics_for_page(page_ann, 595.0, 842.0)
    assert m["connector_crossings"] == 1


# ---------------------------------------------------------------------------
# benchmark_alignment.py — simple and job mode
# ---------------------------------------------------------------------------

from benchmark_alignment import _score_simple, _label_at
from app.models import AlignedWindow, Alignment


def _make_alignment(windows):
    return Alignment(
        windows=[AlignedWindow(window_id=i, start=s, end=e, text="", page=p,
                               raw_page=p, confidence=1.0, similarities=[])
                 for i, (s, e, p) in enumerate(windows)],
        pages=[1, 2, 3, 4, 5]
    )


def test_score_simple_exact():
    al = _make_alignment([(0, 10, 1), (10, 20, 2)])
    truth = [{"start": 0, "end": 10, "pages": [1]}, {"start": 10, "end": 20, "pages": [2]}]
    r = _score_simple([w.page for w in al.windows], al, truth)
    assert r["exact"] == pytest.approx(1.0)


def test_score_simple_within_one():
    al = _make_alignment([(0, 10, 1), (10, 20, 3)])
    truth = [{"start": 0, "end": 10, "pages": [1]}, {"start": 10, "end": 20, "pages": [2]}]
    r = _score_simple([w.page for w in al.windows], al, truth)
    assert r["exact"] == pytest.approx(0.5)
    assert r["within_one"] == pytest.approx(1.0)


def test_score_simple_unlabeled_ignored():
    al = _make_alignment([(100, 110, 1)])
    truth = [{"start": 0, "end": 10, "pages": [1]}]
    r = _score_simple([w.page for w in al.windows], al, truth)
    assert r["exact"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# notes_gold.schema.json — schema validity
# ---------------------------------------------------------------------------

def test_notes_gold_schema_is_valid_json():
    schema_path = GOLDEN_DIR / "notes_gold.schema.json"
    assert schema_path.exists(), "notes_gold.schema.json missing"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$schema"].startswith("https://json-schema.org")
    assert "properties" in schema
    assert "actions" in schema["properties"]


def test_notes_gold_p15_matches_schema_shape():
    p15_path = GOLDEN_DIR / "p15.json"
    assert p15_path.exists(), "p15.json missing"
    gold = json.loads(p15_path.read_text(encoding="utf-8"))
    assert isinstance(gold["page"], int)
    assert isinstance(gold["actions"], list)
    assert all("type" in a and "importance" in a and "quote" in a for a in gold["actions"])


# ---------------------------------------------------------------------------
# 05a433631737_provisional.json — basic sanity
# ---------------------------------------------------------------------------

def test_provisional_gold_non_overlapping():
    prov_path = GOLDEN_DIR / "05a433631737_provisional.json"
    assert prov_path.exists(), "05a433631737_provisional.json missing"
    rows = json.loads(prov_path.read_text(encoding="utf-8"))
    assert len(rows) == 8
    for row in rows:
        assert row["end"] > row["start"]
        assert all(isinstance(p, int) and p >= 1 for p in row["pages"])
    # Check non-overlapping
    sorted_rows = sorted(rows, key=lambda r: r["start"])
    for a, b in zip(sorted_rows, sorted_rows[1:]):
        assert a["end"] <= b["start"] + 0.1  # small tolerance for float equality
