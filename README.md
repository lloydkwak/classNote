# classNote

Phase E now defaults to [slide-first Layout v2](docs/layout_v2.md), with a source-validated Lecture Overview as the first exported PDF page and an updated upload/progress/PDF workspace.
See the [Ubuntu runbook](docs/ubuntu_vlm.md), [frontend design](docs/frontend_design.md),
and [measured quality report](docs/annotation_quality_report.md). The real lecture's extraction/alignment
failures remain visible; layout improvements do not imply improved lecture coverage.

Upload a lecture recording and its PDF slides. classNote finds which part of the slides the professor was explaining at each moment and attaches notes there. Clicking a note plays that part of the recording.

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
an isolated environment. The API keeps its existing opt-in grounding flag; the upload UI selects precise grounding by default and supports fallback.

Phase C now adds [Set-of-Mark element-ID grounding](docs/visual_grounding.md), with SSD model cache,
validated speech evidence, original/SoM comparisons, and review PNG/JSON artifacts.

Phase D connects [Evidence Graph and grounded annotations](docs/evidence_graph.md) to the existing
production notes, density and PDF export paths. Enable per job with the API's `visual_grounding=true`
or CLI `--visual-grounding`; unsupported or ambiguous targets remain unconfirmed. See the
[Ubuntu runbook](docs/ubuntu_vlm.md) for isolated-worker execution and the
[quality report](docs/annotation_quality_report.md) for actual synthetic GPU/PDF results and limitations.

## Slide-first / Overview regression on Ubuntu

Every annotated PDF has an Overview followed by the original source slides. Source slide/audio
indices stay unchanged; exported page indices are explicit. Detailed Notes remain available when
an exact visual target is uncertain. See [actual results and limitations](docs/annotation_quality_report.md).

Reuse the existing environments and SSD cache; the commands below do not sync dependencies:

```bash
# Backend: real configured providers from .env, no mock overrides
FFMPEG_PATH=/usr/bin/ffmpeg backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
# Another terminal
cd frontend
PATH=/root/classNote/.cache/node/node_modules/node/bin:$PATH npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`. For SSH, forward that port to the server. Preserve the existing
`.env` and PyAV18.1.0 pin; do not run `uv sync` merely to start these installed environments.
`LAYOUT_ENGINE=v1` / API `layout=v1` remains available for comparison. `v2` is the new default.

```bash
FFMPEG_PATH=/usr/bin/ffmpeg backend/.venv/bin/python backend/scripts/verify_slide_first.py \
  --job data/jobs/05a433631737 --output data/slide-first-runs/new-run --gpu
# Re-render that run from checkpoints without another VLM request:
FFMPEG_PATH=/usr/bin/ffmpeg backend/.venv/bin/python backend/scripts/verify_slide_first.py \
  --job data/jobs/05a433631737 --output data/slide-first-runs/new-run --resume
```

The verifier copies the original job and revalidates saved semantic candidates. It does not claim
new STT or alignment accuracy. Actual PDF/PNG/JSON results are under ignored `data/`.

### Annotation Intelligence v3 (experimental)

The default annotation mode remains `legacy`. New uploads can choose an experimental
learning-point planner or visual review in **필기 설정 → 필기 방식**. The existing provider
supplies semantic plans and whole-lecture study synthesis; Qwen3-VL 8B NF4 supplies
optional visual selection/review. The server records provider/quota/schema failures
and retains validated fallback results. See [v3 architecture](docs/annotation_agent_v3.md),
[Overview synthesis](docs/lecture_overview_v3.md) and [quality limitations](docs/annotation_quality_report.md).

```bash
# Existing environments/cache only; do not uv sync or download the model again.
cd /root/classNote
FFMPEG_PATH=/usr/bin/ffmpeg ANNOTATION_MODE=visual_agent \
  backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000

# In another shell (existing Node 22 path on this server):
cd /root/classNote/frontend
API_TARGET=http://127.0.0.1:8000 PATH=/root/classNote/.cache/node/node_modules/node/bin:$PATH \
  npm run dev -- --host 0.0.0.0 --port 5173
```

Open `http://<server>:5173`, or forward localhost ports over SSH. For a stored job,
its saved `annotation_mode` is authoritative; changing the environment does not
silently switch older jobs. `OVERVIEW_MODE=synthesis` prepares a cited study guide
during notes generation. Exports/density/style changes never call an LLM/VLM.
Use a new directory for reproducible A/B/C artifacts:

```bash
FFMPEG_PATH=/usr/bin/ffmpeg backend/.venv/bin/python backend/scripts/verify_annotation_v3.py \
  --job data/slide-first-runs/real-reviewed/state/jobs/05a433631737 \
  --output data/annotation-v3-runs/my-comparison --prepare --gpu
```
