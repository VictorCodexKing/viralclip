# ViralClip Backend

FastAPI backend for the local AI video-clipping tool. Paste a YouTube link (or a long
video) and it finds the most viral-worthy moments, scores them, and renders vertical
9:16 clips with face-centered cropping, word-synced subtitles, hook titles, and
optional B-roll.

This is a single-laptop application: no Docker, no Postgres, no Redis, no auth/billing.
Persistence and live progress are handled by a lightweight in-process job store
(`app/jobstore.py`, SQLite + asyncio pub/sub).

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- `ffmpeg` and `ffprobe` on your `PATH`

## Setup

```bash
cd backend
uv sync
```

## Run

```bash
uv run uvicorn app.main:app --port 8000
```

Then open http://localhost:8000/health.

## Test

```bash
uv run pytest -q
```

## Configuration

All configuration is read from environment variables (a `.env` file at the backend
root is loaded automatically). See `app/config.py` for the full list. The most common:

- `GOOGLE_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` — LLM provider keys
- `LLM` — model id (default `google-gla:gemini-1.5-flash`)
- `ASSEMBLY_AI_API_KEY` — optional AssemblyAI transcription
- `PEXELS_API_KEY` — optional B-roll
- `WHISPER_MODEL` — local Whisper model size (default `base`)
- `TRANSCRIPTION_PROVIDER` — `whisper` | `assemblyai` | `youtube_captions`
