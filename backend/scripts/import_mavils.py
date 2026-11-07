"""Import MaViLS (Matching Video and Lecture Slides) as an alignment regression set.

LICENSE CHECK — do this before running:
  Code: Apache-2.0 (https://github.com/andererka/MaViLS/blob/main/LICENSE)
  Lecture data: sourced from MIT OCW and similar open-courseware providers.
  Use locally only as a benchmark. Do NOT commit the data to this repository.
  Set MAVILS_LICENSE_CONFIRMED=1 after confirming you understand these terms.

MaViLS data layout (inside the zip / repo):
  data/audioscripts/<lecture>.srt             # one word per SRT block
  data/ground_truth_files/ground_truth_<lecture>.xlsx
      col "Key"   → timestamp in seconds (float)
      col "Value" → 1-based slide number; -1 means no-slide (excluded from metric)
  data/lectures/<lecture>.pdf

Sentence grouping:
  Accumulate SRT words until word.rstrip().endswith(('.','?','!')).
  For lectures: cryptocurrency, short_range_mit, deeplearning_goodfellow,
    solar_resource, psychology_MIT — only '.' terminates a sentence.
  Sentence timestamp = midpoint of [first_word.start, last_word.end].

Output (per lecture under --output):
  <lecture_id>/segments.json             # list[Segment] in app.models JSON
  <lecture_id>/evaluation/labels.json    # EvaluationDataset

Usage:
    MAVILS_LICENSE_CONFIRMED=1 uv run python scripts/import_mavils.py
    MAVILS_LICENSE_CONFIRMED=1 uv run python scripts/import_mavils.py \\
        --source data/mavils-raw/ --output data/mavils/
"""

import argparse
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation.schema import EvaluationDataset, Interval, Review  # noqa: E402
from app.models import Segment, SpokenWord  # noqa: E402
from app.pipeline.run import _write_atomically  # noqa: E402


_GITHUB_ZIP_URL = "https://github.com/andererka/MaViLS/archive/refs/heads/main.zip"

# Lectures that use only '.' as a sentence boundary (from spec)
_DOT_ONLY = frozenset([
    "cryptocurrency", "short_range_mit", "deeplearning_goodfellow",
    "solar_resource", "psychology_MIT",
])


def _check_license() -> None:
    if os.environ.get("MAVILS_LICENSE_CONFIRMED") != "1":
        print(
            "ERROR: Set MAVILS_LICENSE_CONFIRMED=1 after checking:\n"
            "  Code license: https://github.com/andererka/MaViLS/blob/main/LICENSE (Apache-2.0)\n"
            "  Data: MIT OCW and similar sources — use locally only, never commit.\n"
            "This script will not proceed without explicit confirmation."
        )
        sys.exit(1)


def _download_zip(url: str, dest: Path) -> None:
    import urllib.request
    print(f"Downloading {url} …")
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    print(f"  → {dest} ({dest.stat().st_size // 1024 // 1024} MB)")


def _find_or_download(source: Path | None) -> Path:
    """Return a directory containing the MaViLS data subtrees."""
    if source and source.is_dir():
        return source
    cache = Path(__file__).resolve().parents[2] / "data" / "cache" / "mavils-raw"
    zip_path = cache / "mavils-main.zip"
    if not zip_path.exists():
        _download_zip(_GITHUB_ZIP_URL, zip_path)
    extract_dir = cache / "extracted"
    if not extract_dir.exists():
        print(f"Extracting {zip_path} …")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
        print("  done.")
    # The zip contains a top-level MaViLS-main/ directory
    candidates = list(extract_dir.glob("MaViLS-*/"))
    return candidates[0] if candidates else extract_dir


# ---------------------------------------------------------------------------
# SRT parsing
# ---------------------------------------------------------------------------

_SRT_TIME_RE = re.compile(
    r"\[(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})\]\s*(.*)"
)


def _srt_time(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(path: Path) -> list[SpokenWord]:
    """Parse a MaViLS-style SRT (one word per block) into SpokenWord list."""
    words: list[SpokenWord] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        m = _SRT_TIME_RE.match(line)
        if m:
            start = _srt_time(m.group(1), m.group(2), m.group(3), m.group(4))
            end   = _srt_time(m.group(5), m.group(6), m.group(7), m.group(8))
            text  = m.group(9).strip()
            if text and end > start:
                words.append(SpokenWord(start=start, end=end, text=text))
    return words


def _is_sentence_end(word: str, dot_only: bool) -> bool:
    stripped = word.rstrip()
    if dot_only:
        return stripped.endswith(".")
    return stripped.endswith((".", "?", "!"))


def build_segments(words: list[SpokenWord], lecture_stem: str) -> list[Segment]:
    """Group SpokenWord list into sentence-level Segments."""
    dot_only = lecture_stem in _DOT_ONLY
    segments: list[Segment] = []
    buf: list[SpokenWord] = []
    for word in words:
        buf.append(word)
        if _is_sentence_end(word.text, dot_only):
            midpoint = (buf[0].start + buf[-1].end) / 2.0
            text = " ".join(w.text for w in buf)
            segments.append(Segment(
                start=midpoint,
                end=midpoint,  # sentence timestamp is a point; start == end is fine for embedding
                text=text,
                words=list(buf),
            ))
            buf = []
    if buf:
        midpoint = (buf[0].start + buf[-1].end) / 2.0
        segments.append(Segment(
            start=midpoint,
            end=midpoint,
            text=" ".join(w.text for w in buf),
            words=list(buf),
        ))
    return segments


# ---------------------------------------------------------------------------
# Excel ground-truth parsing
# ---------------------------------------------------------------------------

def parse_ground_truth(xlsx_path: Path) -> list[tuple[float, int]]:
    """Return [(timestamp_seconds, slide_1based), ...] sorted by timestamp.

    Columns (MaViLS has two variants):
      "Key"/"Time" → timestamp in seconds
      "Slidenumber" → 1-based slide (-1 = no slide on screen)
    slide = -1 is kept here; callers decide whether to exclude it.
    """
    from openpyxl import load_workbook
    wb = load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb.active
    rows: list[tuple[float, int]] = []
    time_col: int | None = None
    slide_col: int | None = None
    header_found = False
    for row_cells in ws.iter_rows(values_only=True):
        if not header_found:
            lowered = [str(c).strip().lower() if c is not None else "" for c in row_cells]
            # Timestamp column: "key" or "time"
            for name in ("key", "time"):
                if name in lowered:
                    time_col = lowered.index(name)
                    break
            # Slide column: "slidenumber" or "value" (fall back)
            for name in ("slidenumber", "value"):
                if name in lowered:
                    slide_col = lowered.index(name)
                    break
            if time_col is not None and slide_col is not None:
                header_found = True
            continue
        if time_col is None or slide_col is None:
            continue
        try:
            ts   = float(row_cells[time_col])
            slide = int(float(row_cells[slide_col]))
        except (TypeError, ValueError, IndexError):
            continue
        rows.append((ts, slide))
    wb.close()
    rows.sort(key=lambda r: r[0])
    return rows


# ---------------------------------------------------------------------------
# Filename matching: srt stem → xlsx stem
# ---------------------------------------------------------------------------

def _normalize_stem(stem: str) -> str:
    """Strip suffixes and normalize punctuation for fuzzy matching."""
    stem = stem.lower()
    stem = re.sub(r"_high_res$", "", stem)
    stem = re.sub(r"_mit$", "", stem)
    stem = re.sub(r"_\d+(_\d+)*$", "", stem)  # trailing _2 or _2_2 etc.
    stem = stem.replace("&", "_and_").replace("&", "and")  # & → _and_
    stem = re.sub(r"[^a-z0-9]+", "_", stem)  # non-alphanumeric → _
    stem = re.sub(r"_+", "_", stem).strip("_")
    return stem


def _xlsx_stems(xlsx_dir: Path) -> dict[str, Path]:
    """Return {normalized_stem: path} for all xlsx files in the directory."""
    out: dict[str, Path] = {}
    for p in xlsx_dir.glob("*.xlsx"):
        stem = p.stem
        # Remove ground_truth_ prefix
        stem = re.sub(r"^ground_truth_", "", stem)
        out[_normalize_stem(stem)] = p
    return out


def find_xlsx(srt_stem: str, xlsx_dir: Path) -> Path | None:
    """Fuzzy-match SRT stem to an xlsx ground-truth file."""
    norm_srt = _normalize_stem(srt_stem)
    xlsx_map = _xlsx_stems(xlsx_dir)
    if norm_srt in xlsx_map:
        return xlsx_map[norm_srt]
    # Fallback: try prefix match
    for norm_xlsx, path in xlsx_map.items():
        if norm_srt.startswith(norm_xlsx) or norm_xlsx.startswith(norm_srt):
            return path
    return None


# ---------------------------------------------------------------------------
# EvaluationDataset construction
# ---------------------------------------------------------------------------

def _gold_to_intervals(gold: list[tuple[float, int]], lecture_id: str) -> list[Interval]:
    """Group consecutive gold rows on the same slide into Intervals.

    Rows with slide=-1 are kept as off-topic intervals (pages=[]).
    """
    if not gold:
        return []
    intervals: list[Interval] = []
    # We need a time range per row; use midpoint between consecutive timestamps
    times = [t for t, _ in gold]
    slides = [s for _, s in gold]
    # Build half-open time bins: [t_i, t_{i+1})
    # For the last row, extend 1 second.
    ends = times[1:] + [times[-1] + 1.0]

    # Group consecutive same-slide rows
    i = 0
    interval_idx = 0
    while i < len(times):
        slide = slides[i]
        j = i + 1
        while j < len(times) and slides[j] == slide:
            j += 1
        start = times[i]
        end = ends[j - 1]
        if end <= start:
            end = start + 0.5
        pages = [] if slide == -1 else [slide]
        intervals.append(Interval(
            id=f"{lecture_id}-{interval_idx}",
            start=start,
            end=end,
            pages=pages,
            review=Review(
                status="provisional",
                source="legacy_inference",
                note="Ground truth from MaViLS Excel; not human-verified by this project",
            ),
        ))
        interval_idx += 1
        i = j
    return intervals


def _build_evaluation_dataset(lecture_id: str, intervals: list[Interval]) -> EvaluationDataset:
    return EvaluationDataset(
        dataset_id=f"mavils-{lecture_id}",
        lecture_id=lecture_id,
        split="test",
        provenance=(
            "Imported from MaViLS dataset (andererka/MaViLS on GitHub, Apache-2.0). "
            "Lecture data sourced from MIT OCW and similar open-courseware providers. "
            "Labels are sentence→slide ground truth from the Excel files; status=provisional."
        ),
        alignment_labels=intervals,
    )


# ---------------------------------------------------------------------------
# Per-lecture processing
# ---------------------------------------------------------------------------

def process_lecture(
    srt_path: Path,
    xlsx_path: Path,
    pdf_path: Path | None,
    output_base: Path,
) -> bool:
    lecture_id = srt_path.stem  # keep original stem as ID
    out_dir = output_base / lecture_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Segments
    seg_path = out_dir / "segments.json"
    if not seg_path.exists():
        words = parse_srt(srt_path)
        if not words:
            print(f"  {lecture_id}: no words parsed from SRT, skipping")
            return False
        segments = build_segments(words, lecture_id)
        _write_atomically(seg_path, json.dumps([s.model_dump() for s in segments], indent=2))
        print(f"  {lecture_id}: {len(words)} words → {len(segments)} sentences → segments.json")
    else:
        print(f"  {lecture_id}: segments.json exists, skipping")

    # Evaluation labels
    eval_dir = out_dir / "evaluation"
    eval_dir.mkdir(exist_ok=True)
    labels_path = eval_dir / "labels.json"
    if not labels_path.exists():
        gold = parse_ground_truth(xlsx_path)
        if not gold:
            print(f"  {lecture_id}: no gold rows in {xlsx_path.name}, skipping")
            return False
        # Only keep rows with valid slide numbers (slide >= 1 or == -1)
        valid_gold = [(t, s) for t, s in gold if s >= 1 or s == -1]
        intervals = _gold_to_intervals(valid_gold, lecture_id)
        dataset = _build_evaluation_dataset(lecture_id, intervals)
        _write_atomically(labels_path, dataset.model_dump_json(indent=2))
        n_valid = sum(1 for _, s in gold if s >= 1)
        print(f"  {lecture_id}: {len(gold)} gold rows, {n_valid} with valid slides → labels.json")
    else:
        print(f"  {lecture_id}: labels.json exists, skipping")

    # PDF
    if pdf_path and not (out_dir / "input.pdf").exists():
        shutil.copy2(pdf_path, out_dir / "input.pdf")
        print(f"  {lecture_id}: copied {pdf_path.name} → input.pdf")

    return True


# ---------------------------------------------------------------------------
# Directory discovery
# ---------------------------------------------------------------------------

def discover_lectures(root: Path) -> list[tuple[Path, Path, Path | None]]:
    """Return [(srt_path, xlsx_path, pdf_path|None), ...] from a MaViLS root."""
    srt_dir  = root / "data" / "audioscripts"
    xlsx_dir = root / "data" / "ground_truth_files"
    pdf_dir  = root / "data" / "lectures"

    if not srt_dir.exists():
        # Try the extracted zip which may have MaViLS-main/ prefix already stripped
        # Walk to find the audioscripts directory
        candidates = list(root.rglob("audioscripts"))
        if candidates:
            srt_dir  = candidates[0]
            xlsx_dir = srt_dir.parent / "ground_truth_files"
            pdf_dir  = srt_dir.parent / "lectures"

    if not srt_dir.exists():
        print(f"WARNING: could not find audioscripts under {root}")
        return []

    xlsx_map = _xlsx_stems(xlsx_dir) if xlsx_dir.exists() else {}
    # Rebuild full mapping with original paths for lookup
    xlsx_full: dict[str, Path] = {}
    if xlsx_dir.exists():
        for p in xlsx_dir.glob("*.xlsx"):
            stem = re.sub(r"^ground_truth_", "", p.stem)
            xlsx_full[_normalize_stem(stem)] = p

    results: list[tuple[Path, Path, Path | None]] = []
    for srt_path in sorted(srt_dir.glob("*.srt")):
        xlsx_path = find_xlsx(srt_path.stem, xlsx_dir) if xlsx_dir.exists() else None
        if xlsx_path is None:
            print(f"  WARNING: no xlsx found for {srt_path.name}, skipping")
            continue
        # Find PDF (stem match)
        pdf_path: Path | None = None
        if pdf_dir.exists():
            norm = _normalize_stem(srt_path.stem)
            # Try exact match first, then keyword overlap
            _STOP = frozenset({"and", "the", "of", "in", "for", "a"})
            norm_words = set(norm.split("_")) - _STOP
            pdf_matches: list[tuple[int, Path]] = []
            for pdf in pdf_dir.glob("*.pdf"):
                pdf_norm = _normalize_stem(pdf.stem)
                if pdf_norm == norm:
                    pdf_matches.append((100, pdf))  # exact
                else:
                    overlap = len(norm_words & (set(pdf_norm.split("_")) - _STOP))
                    if overlap > 0:
                        pdf_matches.append((overlap, pdf))
            if pdf_matches:
                pdf_matches.sort(key=lambda x: -x[0])
                pdf_path = pdf_matches[0][1]
        results.append((srt_path, xlsx_path, pdf_path))
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    _check_license()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--source", type=Path,
        help="Local MaViLS root directory (downloads from GitHub if absent)",
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path(__file__).resolve().parents[2] / "data" / "mavils",
        help="Output directory (default: data/mavils/)",
    )
    args = parser.parse_args()

    root = _find_or_download(args.source)
    args.output.mkdir(parents=True, exist_ok=True)

    lectures = discover_lectures(root)
    if not lectures:
        print(f"No matched SRT+XLSX pairs found under {root}")
        return 1

    print(f"Found {len(lectures)} SRT+XLSX pairs\n")
    imported = sum(
        process_lecture(srt, xlsx, pdf, args.output)
        for srt, xlsx, pdf in lectures
    )
    print(f"\nImported {imported}/{len(lectures)} lectures → {args.output}")
    print("\nNext steps:")
    print("  Run the benchmark:")
    print("    uv run python scripts/benchmark_mavils.py --mavils data/mavils/ \\")
    print("        --aligner v1 --embedder e5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
