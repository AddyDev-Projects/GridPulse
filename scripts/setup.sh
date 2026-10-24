#!/usr/bin/env bash
# One-time GridPulse setup for macOS / Linux. Same steps as scripts/setup.ps1:
# .env check, backend venv + requirements, PostgreSQL role/databases (never drops anything),
# migrations, frontend npm ci. PostgreSQL itself must already be running.
#
#   ./scripts/setup.sh [--skip-database] [--skip-frontend]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
VENV_PY="$BACKEND/.venv/bin/python"
SKIP_DB=0
SKIP_FE=0
for arg in "$@"; do
  case "$arg" in
    --skip-database) SKIP_DB=1 ;;
    --skip-frontend) SKIP_FE=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '==> %s\n' "$1"; }

if [[ ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "Created .env from .env.example."
  echo "Edit .env (database passwords and GRIDPULSE_PG_ADMIN_* settings), then run this script again."
  exit 1
fi

step "Checking Python 3.12+"
PY=""
for candidate in python3.12 python3.13 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
    PY="$candidate"; break
  fi
done
[[ -n "$PY" ]] || { echo "Python 3.12 or newer is required." >&2; exit 1; }

if [[ ! -x "$VENV_PY" ]]; then
  step "Creating backend/.venv with $PY"
  "$PY" -m venv "$BACKEND/.venv"
fi

step "Installing backend dependencies"
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r "$BACKEND/requirements.txt"

cd "$BACKEND"
if [[ $SKIP_DB -eq 0 ]]; then
  step "Creating PostgreSQL role and databases (never drops anything)"
  "$VENV_PY" scripts/db_setup.py
fi
step "Applying migrations to the development database"
"$VENV_PY" -m alembic upgrade head

if [[ $SKIP_FE -eq 0 ]]; then
  step "Checking Node.js (needs 20.19+ or 22.12+)"
  command -v node >/dev/null 2>&1 || { echo "Node.js is not installed or not on PATH." >&2; exit 1; }
  node -e "const [a,b]=process.versions.node.split('.').map(Number); process.exit((a===20&&b>=19)||(a===22&&b>=12)||a>22?0:1)" \
    || { echo "Node.js $(node --version) is too old. Install Node 22 LTS (or 20.19+)." >&2; exit 1; }
  step "Installing frontend dependencies"
  (cd "$ROOT/frontend" && npm ci)
fi

echo
echo "Setup complete."
echo "  Backend:  ./scripts/start-backend.sh"
echo "  Frontend: cd frontend && npm run dev   (then open http://localhost:5173)"
