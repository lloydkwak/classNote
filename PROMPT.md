# Project: LectureNote — Auto-annotate lecture slides from a lecture recording

Build the following service end-to-end, starting from an empty repository. This is for a hackathon demo: the goal is **a working, demo-ready product within 24 hours**. Prioritize a core pipeline that runs reliably from start to finish over breadth of features.

## 1. Product definition

- **One-liner:** The user uploads a recording of an in-person lecture and the lecture's PDF slides. The service finds which part of the PDF the professor was explaining at each moment and attaches highlights and notes (annotations) at that exact location.
- **Target user:** University students who receive lecture slides as a PDF and attend in-person lectures — especially students who miss explanations while trying to write them down.
- **Core value:** Place the professor's spoken explanations, examples, and emphasis ("this will be on the exam") that are *not* on the slide directly at the relevant spot on the slide. Clicking an annotation plays the part of the recording where it was said.
- **Scope:** Post-lecture processing only. Do not implement real-time processing.

### Language rules (important)

Lectures are given in Korean, English, or a mix of both, often switching mid-sentence (e.g. "이 recursion이 base case에서 멈추고…").

- **Notes must be written in the language the professor actually used** in that part of the lecture. If the professor explained it in Korean, write the note in Korean; if in English, write it in English. For mixed speech, follow the dominant language of the segment.
- **Never translate technical terms.** Keep domain terms, names, and jargon exactly as the professor said them or as they appear on the slide (e.g. keep "base case", "deadlock", "time complexity" as-is inside a Korean note).
- Do not hard-code Korean anywhere in the logic. Everything (STT, embeddings, emphasis detection, prompts) must work for Korean, English, and code-switched speech.

## 2. Tech stack

- **Backend:** Python 3.11+, FastAPI, package management with `uv`
- **PDF processing:** PyMuPDF (`pymupdf`)
- **Audio preprocessing:** `ffmpeg` (convert to mono 16kHz mp3, split if long)
- **STT:** local `faster-whisper` by default (`STT_PROVIDER=faster-whisper`, no API key, segment-level timestamps). The model size is configurable and defaults to one that runs acceptably on a laptop CPU.
  - Do **not** force the `language` parameter by default, since lectures may be mixed-language; make it an optional override.
  - Pass key terms extracted from the PDF (slide titles, frequent technical terms) via Whisper's prompt parameter to improve recognition of jargon. Respect the prompt length limit.
  - Keep OpenAI `whisper-1` (`STT_PROVIDER=openai`, `response_format=verbose_json`) as an optional alternative behind the same interface. If a file exceeds its API size limit (25MB), split with ffmpeg and merge results with corrected timestamp offsets.
- **Embeddings:** `sentence-transformers` with `intfloat/multilingual-e5-base` (runs locally, handles Korean, English, and mixed text). Use the `query:` / `passage:` prefixes as e5 requires.
- **LLM:** Abstract the provider behind an env var (`LLM_PROVIDER=gemini|openai|anthropic`, `LLM_MODEL=...`). The default is Gemini through the `google-genai` SDK, with the key in `GEMINI_API_KEY`. Swapping to OpenAI or Anthropic must take one setting. Always request JSON output, validate with pydantic, and retry once on validation failure.
  - The Gemini free tier has tight per-minute and per-day limits: send LLM requests sequentially (no parallel bursts), back off and retry on 429 errors instead of failing, and rely on the cache so repeated runs don't call the API again.
- **Frontend:** React + Vite + TypeScript. Render pages with `react-pdf` (pdf.js) and draw highlights as absolutely positioned div overlays on top of the PDF.
- **Storage:** No database. Use the filesystem: store inputs and intermediate outputs as JSON under `data/jobs/{job_id}/`.

## 3. Directory layout

```
/backend
  app/
    main.py            # FastAPI entry point
    api/               # routers
    pipeline/
      pdf_extract.py
      audio.py         # ffmpeg conversion/splitting
      stt.py
      windowing.py
      page_align.py    # embeddings + order-constrained DP
      block_align.py   # block matching within a page + note generation (LLM)
      consolidate.py
      export_pdf.py
      run.py           # pipeline orchestration
    llm/               # provider abstraction
    models.py          # pydantic schemas
    cache.py
  tests/
  scripts/
    run_pipeline.py    # run the pipeline from the CLI
    eval_alignment.py  # measure alignment accuracy
/frontend
/data                  # gitignored
/samples               # demo PDF/audio go here (gitignored, documented in README)
README.md
.env.example
```

## 4. Pipeline

### 4.1 PDF extraction (`pdf_extract.py`)
- Extract text blocks per page: `block_id`, `page`, `text`, `bbox`.
- **Normalize bboxes to 0–1 relative to page size.** Both the frontend overlay and the PDF export use these values.
- Filter out tiny or meaningless blocks (page numbers, repeated headers/footers). A block that appears at the same position with the same text across many pages is a header/footer.
- Mark the page title separately (first block or largest-font block).
- Mark pages with very little text as `low_text=true`.
- Also produce a list of key terms (titles + frequent technical terms) to feed the STT prompt.
- Also extract figure regions as blocks with `kind="figure"`: image blocks, plus vector drawings (`page.get_drawings()`) clustered into regions. Ignore tiny decorations and full-page backgrounds.
- Render each figure to PNG and ask the LLM (multimodal) for a one-sentence description of what it shows, in the slide's language, keeping technical terms. Use it as the block's text, so page alignment and block matching can match speech like "이 그래프 보면" to the figure. Cache by image hash; in `DEMO_MODE` use the cache only. If the call fails, keep an empty caption rather than failing the job.

### 4.2 Audio + STT (`audio.py`, `stt.py`)
- Convert the uploaded file to mono 16kHz mp3.
- Output: `segments = [{start, end, text}]` (seconds).
- Cache STT results keyed by the audio file hash (+ prompt).

### 4.3 Windowing (`windowing.py`)
- Concatenate segments into windows of roughly 30 seconds (configurable). Never cut inside a segment.
- Output: `windows = [{window_id, start, end, text}]`

### 4.4 Page alignment (`page_align.py`) ← the core of the project
- Build a windows × pages cosine-similarity matrix. Page embeddings come from the title + body block text.
- Decide each window's page with an **order-constrained DP (Viterbi)**:
  - Stay on the same page: no penalty
  - Move to the next page (+1): small penalty
  - Skip multiple pages (+2 or more): penalty proportional to distance
  - Go back to an earlier page: large penalty (allowed, not forbidden — professors do go back)
  - All penalty values configurable.
- Attach `page` and `confidence` (based on the gap between the best and second-best similarity) to each window.
- Off-topic chatter (all similarities below a threshold) gets `page=null`.

### 4.5 Block matching + note generation (`block_align.py`)
- Group consecutive windows aligned to the same page into one "explanation span".
- For each span, give the LLM: the page's block list (`block_id` + text) and the span's transcript.
- The LLM returns a JSON array:
  ```json
  [{
    "block_ids": ["p3_b2"],
    "type": "explanation | example | emphasis | exam_hint | question",
    "note": "1–2 sentences, only what is NOT already on the slide",
    "quote": "the part of the transcript this note is based on",
    "language": "ko | en",
    "importance": 1
  }]
  ```
- Prompt rules:
  - Do not repeat what is already written on the slide; note **only information that is not on the slide**.
  - Write each note in the language the professor used in that span; never translate technical terms (see Language rules).
  - Do not invent anything. The `quote` must actually appear in the transcript — verify this in code (allow minor whitespace/punctuation differences) and drop notes that fail.
  - If a note doesn't correspond to a specific block, leave `block_ids` empty and treat it as a page-level note.
- Also detect emphasis with simple rules in both languages (e.g. Korean: "시험", "중요", "꼭", "외워"; English: "exam", "important", "remember", "make sure") and pass matches to the LLM as `exam_hint` candidates.
- Attach the source span's `start` and `end` timestamps to each note.

### 4.6 Consolidation (`consolidate.py`)
- Merge duplicate notes attached to the same block.
- Cap notes per page (default 5), keeping the highest `importance`.
- Final `annotations.json`:
  ```json
  {
    "job_id": "...",
    "pages": [{
      "page": 3,
      "notes": [{
        "id": "...",
        "block_ids": ["p3_b2"],
        "bboxes": [[x0, y0, x1, y1]],
        "type": "exam_hint",
        "note": "...",
        "language": "ko",
        "start": 812.4,
        "end": 841.0
      }]
    }]
  }
  ```

### 4.7 PDF export (`export_pdf.py`)
- Use PyMuPDF to draw the notes on the original PDF and save as `annotated.pdf`.
- Draw notes like real handwriting instead of flat boxes: underline for text blocks, a circle or bracket around figures, and an arrow from the annotation to a note in the nearest margin. Margin notes must not overlap each other. The viewer draws the same thing.
- Use different colors per note type (e.g. red tones for exam hints).
- Make sure Korean text renders correctly in annotations (check font handling).

## 5. API

- `POST /api/jobs` — upload PDF + audio, return `job_id`, run the pipeline in the background
- `GET /api/jobs/{job_id}` — per-step status (`extracting_pdf`, `transcribing`, `aligning`, `writing_notes`, `done`, `error`) and error message
- `GET /api/jobs/{job_id}/annotations` — `annotations.json`
- `GET /api/jobs/{job_id}/pdf`, `/audio`, `/annotated.pdf` — serve files (support HTTP Range on audio so seeking works)
- `GET /api/jobs/{job_id}/debug` — per-window alignment results and similarities (used for the presentation visualization)

## 6. Frontend

- **Upload screen:** drag-and-drop PDF and audio, show per-step progress.
- **Viewer screen (the main demo screen):**
  - Left: PDF pages, with handwritten-style marks and margin notes on annotated blocks
  - Right: note cards for the current page (with type badges)
  - Bottom: audio player
  - Clicking a highlight or card → play from that span
  - While audio plays, auto-navigate to the page being explained and emphasize the active highlight ("follow along" toggle)
  - `annotated.pdf` download button
- **Debug screen:** a chart with time on the x-axis and page on the y-axis showing alignment results, comparing raw argmax vs. after the order-constrained DP. This explains the technical differentiator in the presentation.

## 7. Demo reliability

- Cache all external calls (STT, LLM) in `data/cache/` keyed by input hash. Re-running the same input must not hit the APIs.
- With `DEMO_MODE=1`, use only cached results and make no external calls — the demo must work even if the venue's network drops.
- Target 10–15 minute recordings for the demo, since processing time grows with length.
- Save each step's output as JSON in the job folder so the pipeline can resume from a given step (`--from-step`).

## 8. Tests and evaluation

- `page_align` must be testable without any API. Write unit tests with synthetic similarity matrices covering moving forward, going back, and off-topic spans.
- Test `pdf_extract` with a small PDF generated by PyMuPDF in the test itself.
- `scripts/eval_alignment.py`: compare against a hand-labeled ground-truth file (`[{start, end, page}]`) and print page-alignment accuracy, both before and after the DP.
- Provide a mock LLM provider and a mock STT so one integration test can run the whole pipeline end-to-end without API keys. Include a mixed Korean/English mock transcript and assert that notes keep the source language and that technical terms are not translated.

## 9. How to work

Proceed step by step in the order below. After each step, verify it works and commit. At each step, briefly tell me what was completed and how you verified it.

1. **Scaffold:** repo structure, `uv` project, Vite project, `.env.example`, `.gitignore`, README draft; confirm backend and frontend both start
2. **PDF extraction** + tests
3. **Audio conversion + STT** + caching
4. **Windowing + page alignment** + unit tests + `eval_alignment.py`
5. **CLI run from PDF + audio → page alignment result** ← first milestone
6. **Block matching + note generation + consolidation** (LLM abstraction, including the mock provider)
7. **PDF export**
8. **API + background jobs + progress status**
9. **Frontend upload screen + viewer** ← second milestone; the demo must be possible here
10. **Follow-along mode, debug screen**
11. **DEMO_MODE, finish README** (setup, env vars, how to prepare sample data, run commands)

Principles:
- If a step gets stuck, cut scope so you can move on. If block-level alignment is unreliable, fall back to page-level notes.
- When a step needs an external API key, tell me what to put in `.env`, and keep going with the mock providers if no key is present.
- If sample data is needed, tell me to put a PDF and audio file in `samples/`. Until I do, proceed with a test-PDF generator and mock transcript data.
- Avoid over-abstraction and unnecessary dependencies. Keep the code simple and readable, as befits a hackathon.

Start with step 1.
