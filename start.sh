#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

SKIP_INSTALL=0
APP_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --skip-install)
      SKIP_INSTALL=1
      ;;
    *)
      APP_ARGS+=("$arg")
      ;;
  esac
done

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION="${PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION:-python}"

if [[ ! -x ".venv/bin/python" ]]; then
  python3 -m venv .venv
fi

if [[ "$SKIP_INSTALL" -eq 0 ]]; then
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

LOG_FILE="${APP_LOG_FILE:-logs/backend.log}"
mkdir -p "$(dirname "$LOG_FILE")"
echo "Backend logs -> $LOG_FILE"
echo "Model -> ${OPENROUTER_MODEL:-deepseek/deepseek-v3.2}"
echo "Checking migrations -> applying pending migrations (with foreign-revision recovery)"

".venv/bin/python" - <<'PY'
try:
    from src.database.schema import run_migrations
except Exception as exc:
    raise SystemExit(
        "Migration dependencies missing in .venv. "
        "Install with: .venv/bin/pip install -r requirements.txt"
    ) from exc

run_migrations()
print("Migrations up to date.")
PY

CMD=(".venv/bin/python" "-m" "streamlit" "run" "app.py")
if [[ ${#APP_ARGS[@]} -gt 0 ]]; then
  CMD+=("${APP_ARGS[@]}")
fi

"${CMD[@]}" 2>&1 | tee -a "$LOG_FILE"
