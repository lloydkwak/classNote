# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

The repo has no application code yet, so there are no build, lint, test, or run commands. `PROMPT.md` is the full spec for what will be built: LectureNote, a hackathon demo that takes a lecture recording and PDF slides and attaches the professor's spoken explanations to the matching spot on the slides.

Read `PROMPT.md` before implementing anything. It defines the directory layout, the JSON schemas, the API routes, and the build order (section 9). Replace this section with real commands once step 1 (scaffold) is done.

## Planned stack

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
