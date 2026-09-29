# ViralClip

Turn a long video into short, vertical, viral-ready clips — on your own laptop.

Paste a YouTube link (or upload a long video: a podcast, a talk, a stream VOD) and
ViralClip finds the most clip-worthy moments, scores them, and renders them as
vertical **9:16** clips with face-centered cropping, word-synced subtitles, hook
titles, and optional B-roll. The app and clips live locally on `localhost`;
AssemblyAI transcribes audio and Gemini analyzes the transcript. **No Docker or
database server** is required.

This project is a simplified, local-only adaptation of
[FujiwaraChoki/supoclip](https://github.com/FujiwaraChoki/supoclip). It keeps the
core clip-generation pipeline and drops the hosted-SaaS layers (auth, billing,
Postgres, Redis, Docker). See [Credits & license](#credits--license).

## What it does

- **AI clip selection** — an LLM (Gemini, GPT, Claude, or a local Ollama model)
  picks the most clip-worthy segments from the transcript.
- **Virality scoring** — every clip gets hook, engagement, value, and shareability
  subscores plus a combined 0–100 score.
- **Smart vertical cropping** — face detection keeps the speaker centered in the
  9:16 frame (falls back to a center crop when no face is found).
- **MoviePy rendering** — selects 3–7 moments of 10–30 seconds, crops to
  1080×1920, and applies optional fade effects. Short sources may yield fewer clips.
- **Ready notifications** — a completion toast appears when clips are saved.
  Click **Notify me when ready** on the progress page for a desktop notification;
  keep that page open, including in a background tab.
- **Word-synced subtitles** — word-level timestamps drive animated captions, with
  custom fonts and caption templates.
- **Hook titles** — an AI-written headline is burned into the top of each clip's
  opening seconds.
- **B-roll & transitions** — optional Pexels stock-footage overlays (enabled when
  a Pexels key is set).
- **Built-in editor** — trim, split, and merge clips, then export with platform
  presets (TikTok, Reels, Shorts — all 1080×1920).
- **Real-time progress** — live pipeline updates are streamed to the browser over
  Server-Sent Events (SSE) while your video processes.

## Architecture

- **Backend** — FastAPI (Python 3.11) in `backend/`. Orchestrates download →
  transcribe → AI selection → render. State and live progress live in a
  lightweight in-process job store (SQLite + asyncio pub/sub), so there is no
  Postgres or Redis to run.
- **Frontend** — Next.js 15 (TypeScript, Tailwind) in `frontend/`. A paste-URL /
  upload form, a live progress view, and a clip gallery with virality scores,
  preview, download, and the built-in editor.

## Prerequisites

Install these on your machine (not in Docker):

- **Python 3.11+** and [uv](https://docs.astral.sh/uv/)
- **Node 22+** and [pnpm](https://pnpm.io/)
- **ffmpeg** and **ffprobe** available on your `PATH`

### Installing ffmpeg

Most systems: `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg` (Debian/Ubuntu).

If you cannot install a system package, use a static build. Download
`ffmpeg-release-amd64-static.tar.xz` from
[johnvansickle.com/ffmpeg](https://johnvansickle.com/ffmpeg/), extract it, and copy
the `ffmpeg` and `ffprobe` binaries somewhere on your `PATH` (for example
`~/.local/bin`). The archive is `.tar.xz`; if you do not have an `xz` binary, Python
can extract it (its standard library includes `lzma`):

```bash
python -c "import lzma, tarfile; tarfile.open(fileobj=lzma.open('ffmpeg-release-amd64-static.tar.xz')).extractall('ffmpeg-static')"
```

Verify with `ffmpeg -version` and `ffprobe -version`.

## Setup

```bash
# 1. Install dependencies (backend via uv, frontend via pnpm).
make setup

# 2. Configure environment variables.
cp backend/.env.example backend/.env      # add GOOGLE_API_KEY and ASSEMBLY_AI_API_KEY
cp .env.example frontend/.env             # optional; defaults to localhost:8000
```

`make setup` runs `cd backend && uv sync` and `cd frontend && pnpm install`. If you
do not have `make`, run those two commands directly.

## Environment variables

Full reference: [`backend/.env.example`](backend/.env.example) and
[`.env.example`](.env.example).

**Required**

| Variable | Description |
| --- | --- |
| `GOOGLE_API_KEY` | Gemini API key for AI clip selection. Free key at [aistudio.google.com](https://aistudio.google.com/app/apikey). Required unless you point `LLM` at another provider whose key you set instead. |
| `ASSEMBLY_AI_API_KEY` | Required for default AssemblyAI word-level transcription. Get an existing key from [the AssemblyAI dashboard](https://www.assemblyai.com/dashboard). |

**Optional**

| Variable | Default | Description |
| --- | --- | --- |
| `LLM` | `google-gla:gemini-3.8-flash` | Model id, format `provider:model` (e.g. `openai:gpt-4o-mini`, `anthropic:claude-3-5-sonnet-latest`, `ollama:llama3`). |
| `OPENAI_API_KEY` | – | Use OpenAI models instead of Gemini. |
| `ANTHROPIC_API_KEY` | – | Use Anthropic (Claude) models. |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Use a local Ollama model. |
| `TRANSCRIPTION_PROVIDER` | `assemblyai` | `whisper` (local), `assemblyai`, or `youtube_captions`. |
| `WHISPER_MODEL` | `base` | Local Whisper model size (`tiny`/`base`/`small`/`medium`/`large`). Weights download on first run. |
| `PEXELS_API_KEY` | – | Enables optional Pexels B-roll overlays. |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Frontend → backend base URL. |

## Running

Open **two terminals**:

```bash
# Terminal 1 — backend on http://localhost:8000
make backend

# Terminal 2 — frontend on http://localhost:3000
make frontend
```

Or run both from one terminal:

```bash
make dev        # wraps ./run.sh; Ctrl-C stops both
```

Then open **http://localhost:3000** in your browser.

Without `make`, the equivalents are:

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8000
cd frontend && pnpm dev
```

## Usage flow

1. Open http://localhost:3000.
2. **Paste a YouTube link** (or upload a video file).
3. Choose options (caption template, font, whether to include B-roll).
4. Watch **live progress** as the pipeline runs: download → transcribe → AI
   selection → render.
5. When it finishes, browse the **clip gallery**: each clip shows its virality
   scores and hook title.
6. **Preview, edit** (trim / split / merge), **export** with a TikTok / Reels /
   Shorts preset, and **download** the finished 9:16 mp4.

To install the optional MediaPipe and OpenCV DNN face models, run
`cd backend` and `uv run python scripts/download_face_models.py`. Haar cascade
and center cropping remain available if those models cannot load.

Local Whisper remains available with `TRANSCRIPTION_PROVIDER=whisper`. It
downloads model weights on its first run; `base` is the default model size.

## Development

```bash
make test       # backend: cd backend && uv run pytest -q
make build      # frontend: cd frontend && pnpm build
make check      # both of the above
```

## Credits & license

This is an adaptation of [FujiwaraChoki/supoclip](https://github.com/FujiwaraChoki/supoclip),
which is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.
This project reuses and adapts parts of that codebase (the clip-selection prompt and
schema, the transcription/reframing/caption pipeline, the caption templates, and the
bundled fonts) and is therefore likewise distributed under **AGPL-3.0**. Full thanks
to the original authors for the excellent foundation.
