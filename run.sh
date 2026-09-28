#!/usr/bin/env bash
#
# Run the ViralClip backend (http://localhost:8000) and frontend
# (http://localhost:3000) together in a single terminal. Press Ctrl-C to stop
# both. No Docker involved.
#
# Requirements: uv, pnpm, and ffmpeg/ffprobe on PATH. See README.md.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() {
  echo ""
  echo "Shutting down..."
  # Kill the whole process group so uvicorn and next both stop.
  kill 0 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting backend on http://localhost:8000 ..."
(cd "$ROOT_DIR/backend" && uv run uvicorn app.main:app --reload --port 8000) &

echo "Starting frontend on http://localhost:3000 ..."
(cd "$ROOT_DIR/frontend" && pnpm dev) &

# Wait for either process to exit.
wait -n
