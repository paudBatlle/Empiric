#!/bin/zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: 'uv' is not installed."
  echo "Install it from https://docs.astral.sh/uv/ and run again."
  read -r "?Press Enter to close..."
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "Error: 'npm' is not installed."
  echo "Install Node.js (includes npm) and run again."
  read -r "?Press Enter to close..."
  exit 1
fi

echo "Syncing Python dependencies..."
uv sync --locked

if [ ! -d "frontend/node_modules" ]; then
  echo "Installing frontend dependencies (first run only)..."
  (cd frontend && npm install)
fi

# Stop previously launched Empiric processes to avoid stale environments and port conflicts.
echo "Stopping previous Empiric processes (if any)..."
pkill -f "AgentLoop/ui_web.py" >/dev/null 2>&1 || true
pkill -f "$ROOT_DIR/frontend/node_modules/.bin/vite --host 127.0.0.1 --port 5173" >/dev/null 2>&1 || true
sleep 1

# If ports are still occupied, they are likely owned by other apps.
if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Error: Port 8000 is already in use by another process."
  echo "Please close that process and run again."
  read -r "?Press Enter to close..."
  exit 1
fi
if lsof -nP -iTCP:5173 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Error: Port 5173 is already in use by another process."
  echo "Please close that process and run again."
  read -r "?Press Enter to close..."
  exit 1
fi

: > .empiric_backend.log
: > .empiric_frontend.log

cleanup() {
  if [ -n "${FRONT_PID:-}" ] && kill -0 "$FRONT_PID" >/dev/null 2>&1; then
    kill "$FRONT_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${BACK_PID:-}" ] && kill -0 "$BACK_PID" >/dev/null 2>&1; then
    kill "$BACK_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

echo "Starting backend..."
uv run python AgentLoop/ui_web.py > .empiric_backend.log 2>&1 &
BACK_PID=$!

echo "Starting frontend..."
(cd frontend && npm run dev -- --host 127.0.0.1 --port 5173) > .empiric_frontend.log 2>&1 &
FRONT_PID=$!

echo "Waiting for frontend to become ready..."
APP_URL=""
for i in {1..60}; do
  if [ -f ".empiric_frontend.log" ]; then
    APP_URL="$(sed -nE 's/.*(http:\/\/127\.0\.0\.1:[0-9]+\/).*/\1/p' .empiric_frontend.log | head -n 1)"
  fi
  if [ -n "$APP_URL" ] && curl -sSf "$APP_URL" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if [ -z "$APP_URL" ]; then
  APP_URL="http://127.0.0.1:5173/"
fi

if ! open "$APP_URL" >/dev/null 2>&1; then
  echo "Browser could not be opened automatically."
  echo "Open this URL manually: $APP_URL"
fi

echo
echo "Empiric is running."
echo "App URL: $APP_URL"
echo "Keep this window open while using the app."
echo "Close this window or press Ctrl+C to stop."
echo
echo "Logs:"
echo "  Backend:  $ROOT_DIR/.empiric_backend.log"
echo "  Frontend: $ROOT_DIR/.empiric_frontend.log"
echo

wait "$FRONT_PID"
