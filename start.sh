#!/usr/bin/env bash
# Melody Transcription — start API + frontend
cd "$(dirname "$0")"
source venv/bin/activate
export PORT="${PORT:-8765}"
echo "Starting at http://localhost:${PORT}"
echo "(Port 8000 is used by another service on this machine.)"
exec uvicorn server:app --host 0.0.0.0 --port "$PORT" --reload
