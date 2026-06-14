#!/bin/zsh
set -u

cd "$(dirname "$0")" || exit 1

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

HOST="${GROK_STUDIO_HOST:-127.0.0.1}"
PORT="${GROK_STUDIO_PORT:-8765}"
DATA_HOME="${GROK_STUDIO_DATA_DIR:-$PWD/grok_studio_data_v6}"
export GROK_STUDIO_DATA_DIR="$DATA_HOME"

find_python() {
  local candidate
  local -a candidates
  candidates=(
    "${GROK_STUDIO_PYTHON:-}"
    /opt/homebrew/bin/python3
    /usr/local/bin/python3
    "$HOME/.pyenv/shims/python3"
    "$HOME/miniconda3/bin/python3"
    "$HOME/anaconda3/bin/python3"
    /usr/bin/python3
  )

  for candidate in "${candidates[@]}"; do
    [[ -n "$candidate" && -x "$candidate" ]] || continue
    if "$candidate" -c 'import ssl, sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
      print -r -- "$candidate"
      return 0
    fi
  done
  return 1
}

cleanup_existing_server() {
  local raw_pids pid command_line
  raw_pids="$(lsof -tiTCP:${PORT} -sTCP:LISTEN 2>/dev/null || true)"
  [[ -z "$raw_pids" ]] && return 0

  for pid in "${(@f)raw_pids}"; do
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if [[ "$command_line" != *"grok_studio.py"* && "$command_line" != *"Grok Studio Lab"* ]]; then
      echo "Port ${PORT} is already in use by another process:"
      echo "$command_line"
      echo "Close that process or run with another port, for example:"
      echo "GROK_STUDIO_PORT=8766 ./run_grok_studio.command"
      exit 1
    fi

    curl -s -X POST -H "Content-Type: application/json" -d "{}" \
      "http://${HOST}:${PORT}/api/shutdown" >/dev/null 2>&1 || true

    for _ in {1..20}; do
      sleep 0.15
      lsof -tiTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1 || return 0
    done

    kill "$pid" >/dev/null 2>&1 || true

    for _ in {1..20}; do
      sleep 0.1
      lsof -tiTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1 || return 0
    done

    kill -9 "$pid" >/dev/null 2>&1 || true
  done
}

PYTHON_BIN="$(find_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Compatible Python 3 was not found."
  echo "Install Python 3 with Homebrew, then open Grok Studio Lab again:"
  echo "brew install python"
  exit 127
fi

echo "Starting Grok Studio Lab with $PYTHON_BIN"
cleanup_existing_server
"$PYTHON_BIN" grok_studio.py --host "$HOST" --port "$PORT" --open
exit $?
