# ViralClip -- local, no-Docker AI video-clipping tool.
#
# Common targets:
#   make setup      Install backend (uv) and frontend (pnpm) dependencies.
#   make backend    Run the FastAPI backend on http://localhost:8000
#   make frontend   Run the Next.js frontend on http://localhost:3000
#   make dev        Run both backend and frontend together (see ./run.sh).
#   make check      Run backend tests and a frontend build.
#
# Prerequisites (installed on your machine, NOT via Docker):
#   - Python 3.11+ and uv        https://docs.astral.sh/uv/
#   - Node 22+ and pnpm          https://pnpm.io/
#   - ffmpeg and ffprobe on PATH (see the "ffmpeg" note in README.md)

.PHONY: setup setup-backend setup-frontend backend frontend dev check test build clean

setup: setup-backend setup-frontend

setup-backend:
	cd backend && uv sync

setup-frontend:
	cd frontend && pnpm install

backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && pnpm dev

# Run backend + frontend together in one terminal (Ctrl-C stops both).
dev:
	./run.sh

check: test build

test:
	cd backend && uv run pytest -q

build:
	cd frontend && pnpm build

clean:
	rm -rf backend/data/temp backend/data/outputs
