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

## Test

```sh
cd backend && uv run pytest
cd backend && uv run python scripts/check_llm.py     # one real LLM call to check your key
```
