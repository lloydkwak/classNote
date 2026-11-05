# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`PROMPT.md` is the full spec: LectureNote takes a lecture recording and PDF slides and attaches the professor's spoken explanations to the matching spot on the slides. It defines the directory layout, the JSON schemas, the API routes, and the build order (section 9). Read it before implementing anything.

## Setup

Needs `uv`, Node.js, and `ffmpeg` on PATH (Windows: `winget install Gyan.FFmpeg`, or set `FFMPEG_PATH`).

```sh
cp .env.example .env            # then set GEMINI_API_KEY
cd backend && uv sync
cd frontend && npm install
```

## Run

```sh
cd backend && uv run uvicorn app.main:app --reload    # http://127.0.0.1:8000
cd frontend && npm run dev                            # http://localhost:5173

# Pipeline from the CLI. --from-step and --to-step take: extract, transcribe, window, align
cd backend && uv run python scripts/run_pipeline.py --pdf slides.pdf --audio lecture.m4a
cd backend && uv run python scripts/run_pipeline.py --job-id <id> --from-step align

# No keys or models: mock transcript, mock LLM, mock embeddings
cd backend && STT_PROVIDER=mock LLM_PROVIDER=mock EMBEDDING_PROVIDER=mock uv run python scripts/run_pipeline.py --pdf slides.pdf
```

## Test

```sh
cd backend && uv run pytest                                      # no keys needed; mocks are forced in tests/conftest.py
cd backend && uv run pytest tests/test_page_align.py -k going_back   # a single test
cd backend && uv run python scripts/check_llm.py                 # one real LLM call to check the key
cd backend && uv run python scripts/eval_alignment.py data/jobs/<id>/alignment.json truth.json
cd frontend && npm run build                                     # type-checks and builds
```

The audio tests skip when ffmpeg is missing. `scripts/make_test_pdf.py` writes the test PDF that matches the mock transcript.

## Stack

- **Backend:** Python 3.11+, FastAPI, `uv`, PyMuPDF, `ffmpeg`, `sentence-transformers` (`intfloat/multilingual-e5-base`)
- **STT:** local `faster-whisper` by default, with OpenAI `whisper-1` as an optional alternative (`STT_PROVIDER`)
- **LLM:** Gemini through `google-genai` by default (`LLM_PROVIDER=gemini`, `GEMINI_API_KEY`), swappable to OpenAI or Anthropic with one setting
- **Frontend:** React, Vite, TypeScript, `react-pdf`
- **Storage:** filesystem only, with no database. Job data goes in `data/jobs/{job_id}/` and the API cache in `data/cache/`.

## Architecture

The core is a sequential pipeline, orchestrated by `backend/app/pipeline/run.py`:

PDF extraction → audio conversion + STT → windowing → page alignment → block matching + note generation → consolidation → PDF export

- **Resumable steps:** each step saves its output as JSON in the job folder, so a run can restart from any step with `--from-step`.
- **Page alignment is the technical differentiator:** `page_align.py` uses an order-constrained DP (Viterbi) over a windows × pages similarity matrix. Going back to an earlier page is heavily penalized but must stay allowed, and off-topic windows get `page=null`. The debug screen compares raw argmax against the DP result, so keep both available.
- **Swappable providers:** STT and LLM sit behind interfaces, and the LLM is selected with `LLM_PROVIDER` and `LLM_MODEL`. Mock versions of both must let the full pipeline run without API keys.

## Rules that span multiple modules

- **Language:** write each note in the language the professor used in that part of the lecture, and never translate technical terms. Lectures may be Korean, English, or code-switched mid-sentence. Don't hard-code Korean into the logic, and don't force Whisper's `language` parameter by default (it's an optional override).
- **Bounding boxes:** normalize them to 0–1 relative to page size. Both the frontend overlay and the PDF export use these values.
- **Note integrity:** LLM output is validated with pydantic and retried once on failure. Each note's `quote` must be verified in code against the transcript, allowing minor whitespace and punctuation differences, and notes that fail are dropped. Notes contain only information that isn't already on the slide.
- **E5 embeddings:** use the `query:` and `passage:` prefixes the model requires.
- **Demo reliability:** cache all external calls in `data/cache/`, keyed by input hash. With `DEMO_MODE=1`, use only cached results and make no external calls.
- **Gemini free tier:** send LLM requests sequentially, back off and retry on 429 errors instead of failing, and rely on the cache so repeat runs don't call the API.
- **Secrets:** never print, log, or commit `.env` values. Keep `.env.example` in sync with every variable the code reads, using placeholder values only.

## Working agreements from the spec

- Work through the steps in `PROMPT.md` section 9 in order. After each step, verify it works, commit, and briefly report what was completed and how it was verified.
- If a step gets stuck, cut scope. For example, fall back to page-level notes if block-level alignment is unreliable.
- If an API key is missing, say what belongs in `.env` and continue with the mock providers.
- If sample data is missing, ask for a PDF and audio file in `samples/`, and continue with a generated test PDF and mock transcript in the meantime.
- Keep the code simple, and avoid over-abstraction and unnecessary dependencies.
