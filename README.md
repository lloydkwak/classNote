# LectureNote

Upload a lecture recording and its PDF slides. LectureNote finds which part of the slides the professor was explaining at each moment and attaches notes there. Clicking a note plays that part of the recording.

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), Node.js 22+, and `ffmpeg` on your PATH (on Windows: `winget install Gyan.FFmpeg`).

```sh
cp .env.example .env   # then fill in GEMINI_API_KEY
cd backend && uv sync
cd ../frontend && npm install
```

## Run

```sh
cd backend && uv run uvicorn app.main:app --reload   # http://127.0.0.1:8000
cd frontend && npm run dev                           # http://localhost:5173
```

## Credits

Margin notes use [Pretendard](https://github.com/orioncactus/pretendard) (`backend/assets/fonts/Pretendard-LICENSE.txt`), or [Nanum Pen Script](https://fonts.google.com/specimen/Nanum+Pen+Script) (`backend/assets/fonts/OFL.txt`) with `NOTE_FONT=nanum-pen`. Both are under the SIL Open Font License.

## Test

```sh
cd backend && uv run pytest
cd backend && uv run python scripts/check_llm.py     # one real LLM call to check your key
```

## Ubuntu / local Qwen3-VL experiments

See [Ubuntu service and VLM runbook](docs/ubuntu_vlm.md), [implementation plan](docs/advanced_annotation_plan.md),
and [GPU measurements](docs/gpu_benchmark.md). The opt-in single-page Qwen3-VL-8B NF4 worker runs in
an isolated environment; the existing annotation pipeline remains the service default.

Phase C now adds [Set-of-Mark element-ID grounding](docs/visual_grounding.md), with SSD model cache,
validated speech evidence, original/SoM comparisons, and review PNG/JSON artifacts.

Phase D connects [Evidence Graph and grounded annotations](docs/evidence_graph.md) to the existing
production notes, density and PDF export paths. Enable per job with the API's `visual_grounding=true`
or CLI `--visual-grounding`; the service default remains the existing planner. See the
[Ubuntu runbook](docs/ubuntu_vlm.md) for isolated-worker execution and the
[quality report](docs/annotation_quality_report.md) for actual synthetic GPU/PDF results and limitations.
