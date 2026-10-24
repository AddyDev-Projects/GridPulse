#!/usr/bin/env bash
# Apply pending migrations, then start the GridPulse API (macOS / Linux).
#   ./scripts/start-backend.sh [port]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$ROOT/backend/.venv/bin/python"
PORT="${1:-8000}"

[[ -x "$VENV_PY" ]] || { echo "backend/.venv not found. Run ./scripts/setup.sh first." >&2; exit 1; }
cd "$ROOT/backend"
echo "==> Applying migrations (alembic upgrade head)"
"$VENV_PY" -m alembic upgrade head
echo "==> Starting API on http://127.0.0.1:$PORT (Ctrl+C to stop)"
exec "$VENV_PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
