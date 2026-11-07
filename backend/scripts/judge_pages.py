"""Pairwise LLM judge for annotated lecture slides.

Computes automatic layout metrics and optionally runs an LLM-as-judge comparison
between two annotated PDF versions.  Run after each pipeline change to measure
whether the output improved.

AUTOMATIC METRICS (always computed, no API key needed):
  For each annotated page, reads annotations.json + alignment.json and reports:
  · marks per page
  · connector-connector crossings
  · ink-text interference (leader crosses slide text)
  · annotation box overlaps
  · notes out of bounds
  · notes on off-topic windows (window.page is None)
  · possibly-truncated note texts (no terminal punctuation)
  · alignment exact/±1 (if gold labels exist in evaluation/labels.json)

PAIRWISE LLM JUDGE (requires --compare and an LLM API key):
  Renders both annotated PDFs to PNGs (via pdftoppm), then sends each page pair
  plus the original slide and the transcript span to the configured LLM.
  Both orders (A then B, B then A) are submitted to cancel position bias.
  Rubric:
    1. correct_target  — annotation points to the right element
    2. faithful        — note accurately represents what the professor said
    3. adds_knowledge  — information not already visible on the slide
    4. note_style      — condensed student notes, not copied sentences
    5. readable        — legible note text
    6. uncluttered     — few precise marks, not uniform underlines
    7. student_quality — would a strong student write this?

Usage:
    # Automatic metrics only
    uv run python scripts/judge_pages.py --job 05a433631737

    # Pairwise comparison: job A vs job B (or vs a second PDF)
    uv run python scripts/judge_pages.py --job A_id --compare B_id --output data/judge/run-01/

    # Allow provisional alignment gold
    uv run python scripts/judge_pages.py --job A_id --allow-provisional
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Alignment, Annotations, PdfExtraction, Segment  # noqa: E402
from app.pipeline import run as pipeline  # noqa: E402


# ---------------------------------------------------------------------------
# Automatic layout metrics
# ---------------------------------------------------------------------------

def _seg(a, b, c, d) -> bool:
    """True if segment (a,b)–(c,d) intersects segment (c,d)–... using cross-product test."""
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    def on_seg(p, q, r):
        return min(p[0], r[0]) <= q[0] <= max(p[0], r[0]) and min(p[1], r[1]) <= q[1] <= max(p[1], r[1])
    d1, d2 = cross(c, d, a), cross(c, d, b)
    d3, d4 = cross(a, b, c), cross(a, b, d)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
       ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
        return True
    if d1 == 0 and on_seg(c, a, d): return True
    if d2 == 0 and on_seg(c, b, d): return True
    if d3 == 0 and on_seg(a, c, b): return True
    if d4 == 0 and on_seg(a, d, b): return True
    return False


def _segments_cross(path_a: list, path_b: list) -> bool:
    for i in range(len(path_a) - 1):
        for j in range(len(path_b) - 1):
            if _seg(path_a[i], path_a[i+1], path_b[j], path_b[j+1]):
                return True
    return False


def _bbox_overlap(a: list, b: list) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0


def _note_bbox(note: dict) -> list[float] | None:
    sketch = note.get("sketch", {})
    x, y = sketch.get("x"), sketch.get("y")
    w, h = sketch.get("width"), sketch.get("height")
    if None in (x, y, w, h):
        return None
    return [x, y, x + w, y + h]


def _possibly_truncated(text: str) -> bool:
    if not text:
        return False
    text = text.rstrip()
    return bool(text) and text[-1] not in ".!?…。：:」』»\"')"


def _layout_metrics_for_page(page_ann: dict, page_width: float, page_height: float) -> dict:
    notes = page_ann.get("notes", [])
    if not notes:
        return {"marks": 0, "connector_crossings": 0, "ink_text_interference": 0,
                "box_overlaps": 0, "out_of_bounds": 0, "possibly_truncated": 0}

    sketches = [n.get("sketch", {}) for n in notes]
    # arrow is a list of sub-paths; take the first sub-path (the leader line)
    arrows = [s.get("arrow", [[]])[0] if s.get("arrow") else [] for s in sketches]
    boxes = [_note_bbox(n) for n in notes]

    connector_crossings = sum(
        _segments_cross(arrows[i], arrows[j])
        for i in range(len(arrows)) for j in range(i + 1, len(arrows))
        if len(arrows[i]) >= 2 and len(arrows[j]) >= 2
    )

    # ink-text interference: check if any arrow segment crosses a word bbox
    # (approximated by the note's target bboxes)
    ink_text = 0
    for sketch, arrow in zip(sketches, arrows):
        if len(arrow) < 2:
            continue
        tgt = sketch.get("placement", {}).get("target_bbox")
        if tgt:
            tx0, ty0, tx1, ty1 = tgt
            for i in range(len(arrow) - 1):
                seg_a, seg_b = arrow[i], arrow[i + 1]
                word_corners = [
                    (tx0, ty0), (tx1, ty0), (tx1, ty1), (tx0, ty1), (tx0, ty0)
                ]
                for j in range(4):
                    if _seg(seg_a, seg_b, word_corners[j], word_corners[j+1]):
                        ink_text += 1
                        break

    box_overlaps = sum(
        1 for i, a in enumerate(boxes) for b in boxes[i + 1:]
        if a and b and _bbox_overlap(a, b)
    )

    gutter = page_width * 0.28
    out_of_bounds = sum(
        1 for b in boxes if b and (
            b[0] < -gutter or b[2] > page_width + gutter
            or b[1] < 0 or b[3] > page_height
        )
    )

    truncated = sum(
        1 for n in notes if _possibly_truncated(n.get("text", "") or n.get("sketch", {}).get("text", ""))
    )

    return {
        "marks": len(notes),
        "connector_crossings": connector_crossings,
        "ink_text_interference": ink_text,
        "box_overlaps": box_overlaps,
        "out_of_bounds": out_of_bounds,
        "possibly_truncated": truncated,
    }


def _off_topic_notes(annotations: Annotations, alignment: Alignment) -> int:
    """Count notes whose page was assigned to an off-topic window."""
    off_topic_pages = {
        w.page for w in alignment.windows if w.page is None
    }
    null_windows = [w for w in alignment.windows if w.page is None]
    if not null_windows:
        return 0
    off_pages: set[int] = set()
    for w in null_windows:
        # We don't know exactly which page the note is on when off-topic, but we can
        # flag pages where all windows are off-topic.
        pass
    # Simpler: flag notes on pages that only appear in off-topic windows
    all_assigned = {w.page for w in alignment.windows if w.page is not None}
    return sum(
        len(page_ann.get("notes", []))
        for page_ann in annotations.pages
        if page_ann.page not in all_assigned
    )


def compute_automatic_metrics(folder: Path, allow_provisional: bool = False) -> dict[str, Any]:
    ann_path = folder / "annotations.json"
    if not ann_path.exists():
        return {"error": "annotations.json not found"}

    annotations = Annotations.model_validate_json(ann_path.read_text(encoding="utf-8"))
    alignment = Alignment.model_validate_json((folder / "alignment.json").read_text(encoding="utf-8"))
    extraction = PdfExtraction.model_validate_json((folder / "extraction.json").read_text(encoding="utf-8"))

    page_metrics: list[dict] = []
    for page_ann in annotations.pages:
        page_num = page_ann.page
        pdf_page = next((p for p in extraction.pages if p.page == page_num), None)
        pw = pdf_page.width if pdf_page else 595.0
        ph = pdf_page.height if pdf_page else 842.0
        m = _layout_metrics_for_page(page_ann.model_dump(), pw, ph)
        m["page"] = page_num
        page_metrics.append(m)

    total_notes = sum(m["marks"] for m in page_metrics)
    annotated_pages = len(page_metrics)
    marks_per_page = total_notes / annotated_pages if annotated_pages else 0.0
    connector_crossings = sum(m["connector_crossings"] for m in page_metrics)
    ink_text = sum(m["ink_text_interference"] for m in page_metrics)
    box_overlaps = sum(m["box_overlaps"] for m in page_metrics)
    out_of_bounds = sum(m["out_of_bounds"] for m in page_metrics)
    possibly_truncated = sum(m["possibly_truncated"] for m in page_metrics)
    off_topic = _off_topic_notes(annotations, alignment)

    result: dict[str, Any] = {
        "total_annotated_pages": annotated_pages,
        "total_notes": total_notes,
        "marks_per_page": round(marks_per_page, 2),
        "connector_connector_crossings": connector_crossings,
        "ink_text_interference": ink_text,
        "annotation_box_overlaps": box_overlaps,
        "notes_out_of_bounds": out_of_bounds,
        "possibly_truncated_notes": possibly_truncated,
        "notes_on_off_topic_windows": off_topic,
        "per_page": page_metrics,
    }

    # Alignment metrics (if gold is available)
    eval_path = folder / "evaluation" / "labels.json"
    if eval_path.exists():
        from app.evaluation.experiments import replay
        from app.evaluation.metrics import alignment_metrics
        from app.evaluation.storage import load_dataset
        dataset = load_dataset(folder)
        eligible = sum(
            1 for lb in dataset.alignment_labels
            if lb.review.status == "verified"
            or (allow_provisional and lb.review.status in {"verified", "provisional"})
        )
        if eligible:
            segments = [Segment.model_validate(r)
                        for r in json.loads((folder / "segments.json").read_text(encoding="utf-8"))]
            pred = replay(alignment, extraction, segments, "saved")
            am = alignment_metrics(pred, dataset, allow_provisional)
            result["alignment"] = {
                "exact_page_accuracy": am["exact_page_accuracy"],
                "within_one_page_accuracy": am["within_one_page_accuracy"],
                "evaluated_seconds": am["evaluated_seconds"],
                "verified_label_count": am["verified_label_count"],
                "label_count": am["label_count"],
            }
        else:
            result["alignment"] = {"error": "No eligible labels (use --allow-provisional or verify in inspector)"}
    else:
        result["alignment"] = {"error": "No evaluation/labels.json — run init_gold.py first"}

    return result


# ---------------------------------------------------------------------------
# Pairwise LLM judge
# ---------------------------------------------------------------------------

_RUBRIC_PROMPT = """\
You are comparing two annotated lecture slide images to judge which is better for a student reviewing the lecture.

SLIDE CONTENT (the original slide, for reference):
{slide_text}

TRANSCRIPT EXCERPT (what the professor said for this slide):
{transcript}

You are shown two annotated versions of this slide:
- Version A: the left/first image
- Version B: the right/second image

Judge each version on these dimensions (score 1–5, where 5 is best):
1. correct_target:  Each mark points to the right word, formula, or figure
2. faithful:        Notes accurately represent what the professor said
3. adds_knowledge:  Notes add information not already on the slide (no redundancy)
4. note_style:      Condensed student-style notes, not full sentences or slide copies
5. readable:        Note text is legible and clear
6. uncluttered:     Few precise marks rather than many uniform underlines
7. student_quality: Would a strong student be satisfied with these notes?

Respond with valid JSON only — no markdown, no explanation outside the JSON:
{
  "winner": "A" | "B" | "tie",
  "reasoning": "<2-3 sentences explaining the key difference>",
  "scores": {
    "A": {"correct_target": int, "faithful": int, "adds_knowledge": int,
          "note_style": int, "readable": int, "uncluttered": int, "student_quality": int},
    "B": {"correct_target": int, "faithful": int, "adds_knowledge": int,
          "note_style": int, "readable": int, "uncluttered": int, "student_quality": int}
  }
}
"""


def _render_pdf_pages(pdf_path: Path, pages: list[int], output_dir: Path, dpi: int = 150) -> dict[int, Path]:
    """Render specific pages of a PDF to PNG via pdftoppm."""
    result = {}
    for page_num in pages:
        prefix = output_dir / f"p{page_num:04d}"
        cmd = ["pdftoppm", "-png", "-r", str(dpi), "-f", str(page_num), "-l", str(page_num),
               str(pdf_path), str(prefix)]
        subprocess.run(cmd, check=True, capture_output=True)
        candidates = sorted(output_dir.glob(f"p{page_num:04d}*.png"))
        if candidates:
            result[page_num] = candidates[-1]
    return result


def _image_to_base64(path: Path) -> str:
    import base64
    return base64.b64encode(path.read_bytes()).decode()


def _judge_one_page(
    provider, page_num: int,
    img_a: Path, img_b: Path,
    slide_text: str, transcript: str,
) -> dict:
    """Run the judge in both orders (AB and BA) to cancel position bias."""
    import base64
    prompt = _RUBRIC_PROMPT.format(slide_text=slide_text, transcript=transcript)

    def _call(first: Path, second: Path, order: str) -> dict | None:
        try:
            img_a64 = _image_to_base64(first)
            img_b64 = _image_to_base64(second)
            # Build a vision-capable message
            content = [
                {"type": "text", "text": prompt},
                {"type": "image", "data": img_a64, "media_type": "image/png"},
                {"type": "text", "text": "— Version A ends, Version B begins —"},
                {"type": "image", "data": img_b64, "media_type": "image/png"},
            ]
            response = provider.complete(messages=[{"role": "user", "content": content}])
            text = response.strip()
            # Strip optional markdown fences
            if text.startswith("```"):
                text = text.split("```", 2)[1].lstrip("json").strip().split("```")[0]
            verdict = json.loads(text)
            return {"order": order, "verdict": verdict, "raw": response}
        except Exception as exc:
            return {"order": order, "error": str(exc)}

    ab = _call(img_a, img_b, "AB")
    ba = _call(img_b, img_a, "BA")

    # Flip the BA winner to canonical A/B framing
    result = {"page": page_num, "ab": ab, "ba": ba}
    winners = []
    for run in (ab, ba):
        if run and "verdict" in run:
            w = run["verdict"].get("winner", "tie")
            if run["order"] == "BA":
                w = {"A": "B", "B": "A"}.get(w, "tie")
            winners.append(w)
    if winners:
        a_wins = winners.count("A")
        b_wins = winners.count("B")
        result["consensus"] = "A" if a_wins > b_wins else ("B" if b_wins > a_wins else "tie")
    return result


def run_pairwise_judge(
    folder_a: Path, folder_b: Path, output_dir: Path,
    allow_provisional: bool = False,
) -> dict:
    from app.llm import get_provider

    ann_a = Annotations.model_validate_json((folder_a / "annotations.json").read_text(encoding="utf-8"))
    ann_b = Annotations.model_validate_json((folder_b / "annotations.json").read_text(encoding="utf-8"))
    alignment = Alignment.model_validate_json((folder_a / "alignment.json").read_text(encoding="utf-8"))
    extraction = PdfExtraction.model_validate_json((folder_a / "extraction.json").read_text(encoding="utf-8"))
    segments = [Segment.model_validate(r) for r in
                json.loads((folder_a / "segments.json").read_text(encoding="utf-8"))]

    pages_a = {p.page for p in ann_a.pages}
    pages_b = {p.page for p in ann_b.pages}
    common_pages = sorted(pages_a & pages_b)
    if not common_pages:
        return {"error": "No common annotated pages between the two jobs"}

    output_dir.mkdir(parents=True, exist_ok=True)
    png_a_dir = output_dir / "png_a"
    png_b_dir = output_dir / "png_b"
    png_a_dir.mkdir(exist_ok=True)
    png_b_dir.mkdir(exist_ok=True)

    pdf_a = folder_a / "annotated.pdf"
    pdf_b = folder_b / "annotated.pdf"
    if not pdf_a.exists():
        pdf_a = folder_a / "input.pdf"
    if not pdf_b.exists():
        pdf_b = folder_b / "input.pdf"

    pngs_a = _render_pdf_pages(pdf_a, common_pages, png_a_dir)
    pngs_b = _render_pdf_pages(pdf_b, common_pages, png_b_dir)

    provider = get_provider()
    page_results = []
    for page_num in common_pages:
        if page_num not in pngs_a or page_num not in pngs_b:
            continue
        pdf_page = next((p for p in extraction.pages if p.page == page_num), None)
        slide_text = " ".join(b.text for b in pdf_page.blocks) if pdf_page else ""
        window = next((w for w in alignment.windows if w.page == page_num), None)
        transcript = window.text if window else ""
        res = _judge_one_page(provider, page_num, pngs_a[page_num], pngs_b[page_num],
                              slide_text, transcript)
        page_results.append(res)

    consensuses = [r["consensus"] for r in page_results if "consensus" in r]
    win_rate_a = consensuses.count("A") / len(consensuses) if consensuses else None
    report = {
        "job_a": str(folder_a),
        "job_b": str(folder_b),
        "pages_judged": len(page_results),
        "win_rate_a": win_rate_a,
        "win_rate_b": 1 - win_rate_a if win_rate_a is not None else None,
        "tie_rate": consensuses.count("tie") / len(consensuses) if consensuses else None,
        "page_results": page_results,
    }
    report_path = output_dir / "judge_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _fmt(v) -> str:
    if v is None:
        return "N/A"
    if isinstance(v, float):
        return f"{v:.1%}" if 0 <= v <= 1 else f"{v:.2f}"
    return str(v)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job", required=True, help="Job ID to evaluate")
    parser.add_argument("--compare", help="Second job ID for pairwise comparison (optional)")
    parser.add_argument("--output", type=Path, help="Output directory for pairwise report")
    parser.add_argument("--allow-provisional", action="store_true",
                        help="Include provisional alignment labels in accuracy metrics")
    args = parser.parse_args()

    folder_a = pipeline.job_dir(args.job)
    if not folder_a.is_dir():
        sys.exit(f"Job folder not found: {folder_a}")

    print(f"=== Automatic Metrics: {args.job} ===\n")
    metrics = compute_automatic_metrics(folder_a, args.allow_provisional)

    print(f"  Total annotated pages:      {metrics['total_annotated_pages']}")
    print(f"  Total notes:                {metrics['total_notes']}")
    print(f"  Marks per page:             {metrics['marks_per_page']:.1f}")
    print(f"  Connector crossings:        {metrics['connector_connector_crossings']}")
    print(f"  Ink-text interference:      {metrics['ink_text_interference']}")
    print(f"  Annotation box overlaps:    {metrics['annotation_box_overlaps']}")
    print(f"  Notes out of bounds:        {metrics['notes_out_of_bounds']}")
    print(f"  Possibly truncated notes:   {metrics['possibly_truncated_notes']}")
    print(f"  Notes on off-topic windows: {metrics['notes_on_off_topic_windows']}")
    al = metrics.get("alignment", {})
    if "error" in al:
        print(f"\n  Alignment metrics:          {al['error']}")
    else:
        print(f"\n  Alignment exact accuracy:   {_fmt(al.get('exact_page_accuracy'))}")
        print(f"  Alignment ±1 accuracy:      {_fmt(al.get('within_one_page_accuracy'))}")
        print(f"  Evaluated seconds:          {al.get('evaluated_seconds', 'N/A')}")
        print(f"  Gold labels (verified):     {al.get('verified_label_count', 0)}/{al.get('label_count', 0)}")

    print(f"\n  Target precision/recall:    N/A (requires human-verified notes gold in samples/golden/)")
    print(f"  Entailment rate:            N/A (requires MiniCheck or LLM verifier — Phase 2)")

    if args.compare:
        folder_b = pipeline.job_dir(args.compare)
        if not folder_b.is_dir():
            sys.exit(f"Compare job folder not found: {folder_b}")
        output = args.output or (
            pipeline.job_dir("").parent / "judge-runs" / f"{args.job}-vs-{args.compare}"
        )
        print(f"\n=== Pairwise LLM Judge: {args.job} vs {args.compare} ===\n")
        report = run_pairwise_judge(folder_a, folder_b, output, args.allow_provisional)
        if "error" in report:
            print(f"  Error: {report['error']}")
        else:
            print(f"  Pages judged:  {report['pages_judged']}")
            print(f"  Win rate A:    {_fmt(report['win_rate_a'])}")
            print(f"  Win rate B:    {_fmt(report['win_rate_b'])}")
            print(f"  Tie rate:      {_fmt(report['tie_rate'])}")
            print(f"  Full report:   {output / 'judge_report.json'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
