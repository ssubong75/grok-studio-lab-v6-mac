#!/bin/zsh

set -u

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

APP_BUNDLE="$(cd "$(dirname "$0")/../.." && pwd)"
APP_PARENT="$(dirname "$APP_BUNDLE")"
BUNDLED_HOME="$APP_BUNDLE/Contents/Resources/GrokStudio"
DATA_HOME_BASE="$HOME/Library/Application Support/Grok Studio Lab/V6"
DEFAULT_DATA_HOME="$DATA_HOME_BASE/Data"
FALLBACK_DATA_BASE="$DATA_HOME_BASE/Instances"
HOST="${GROK_STUDIO_HOST:-127.0.0.1}"
PORT="${GROK_STUDIO_PORT:-8765}"

show_dialog() {
  /usr/bin/osascript - "$1" <<'APPLESCRIPT'
on run argv
  display dialog (item 1 of argv) buttons {"OK"} default button "OK" with icon caution
end run
APPLESCRIPT
}

find_python() {
  local candidate
  local -a candidates
  candidates=(
    /opt/homebrew/bin/python3
    /usr/local/bin/python3
    "$HOME/.pyenv/shims/python3"
    "$HOME/miniconda3/bin/python3"
    "$HOME/anaconda3/bin/python3"
    /usr/bin/python3
  )

  for candidate in "${candidates[@]}"; do
    [[ -x "$candidate" ]] || continue
    if "$candidate" -c 'import ssl, sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
      print -r -- "$candidate"
      return 0
    fi
  done
  return 1
}

fallback_instance_id() {
  local identity
  identity="$(/usr/bin/xattr -p com.apple.quarantine "$APP_BUNDLE" 2>/dev/null || true)"
  if [[ -z "$identity" && "$APP_BUNDLE" == *"/AppTranslocation/"* ]]; then
    identity="${APP_BUNDLE#*/AppTranslocation/}"
    identity="${identity%%/*}"
  fi
  [[ -n "$identity" ]] || identity="$APP_BUNDLE"
  print -rn -- "$identity" | /usr/bin/shasum -a 256 | /usr/bin/awk '{print substr($1, 1, 16)}'
}

if [[ -f "$APP_PARENT/grok_studio.py" ]]; then
  TARGET_HOME="$APP_PARENT"
else
  TARGET_HOME="$BUNDLED_HOME"
fi

if [[ ! -f "$TARGET_HOME/grok_studio.py" ]]; then
  show_dialog "Grok Studio Lab 실행 파일을 찾을 수 없습니다. 압축을 다시 풀거나 전체 배포 폴더를 사용해주세요."
  exit 1
fi

PYTHON_BIN="$(find_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  show_dialog "Python 3를 찾을 수 없습니다. Homebrew에서 'brew install python'을 실행한 뒤 Grok Studio Lab을 다시 열어주세요."
  exit 1
fi

if [[ -n "${GROK_STUDIO_DATA_DIR:-}" ]]; then
  DATA_HOME="$GROK_STUDIO_DATA_DIR"
elif [[ "$APP_BUNDLE" == *"/AppTranslocation/"* ]]; then
  DATA_HOME="$FALLBACK_DATA_BASE/$(fallback_instance_id)"
else
  DATA_HOME="$DEFAULT_DATA_HOME"
fi

mkdir -p \
  "$DATA_HOME/account_auth" \
  "$DATA_HOME/logs" \
  "$DATA_HOME/prompts" \
  "$DATA_HOME/Upload Image" \
  "$DATA_HOME/tmp" \
  "$DATA_HOME/metadata" \
  "$DATA_HOME/media" \
  "$DATA_HOME/Gallery"
if [[ $? -ne 0 ]]; then
  show_dialog "Grok Studio Lab 데이터 폴더를 만들 수 없습니다.
$DATA_HOME"
  exit 1
fi

[[ -f "$DATA_HOME/settings.json" ]] || print '{}'> "$DATA_HOME/settings.json"
[[ -f "$DATA_HOME/library.json" ]] || print '{"version":3,"categories":["Inbox","Image","Video","Prompt","Finals"],"gallery_folders":[],"items":[]}'> "$DATA_HOME/library.json"
[[ -f "$DATA_HOME/accounts.json" ]] || print '{"active_id":"","accounts":[]}'> "$DATA_HOME/accounts.json"

LOG_DIR="$DATA_HOME/logs"
LOG_FILE="$LOG_DIR/grok_studio.log"

{
  echo ""
  echo "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] Finder launch requested"
  echo "Python: $PYTHON_BIN"
  echo "App: $APP_BUNDLE"
  echo "Runtime: $TARGET_HOME"
  echo "Data: $DATA_HOME"
} >> "$LOG_FILE"

(
  cd "$TARGET_HOME" || exit 1
  /usr/bin/nohup /usr/bin/env \
    GROK_STUDIO_PYTHON="$PYTHON_BIN" \
    GROK_STUDIO_DATA_DIR="$DATA_HOME" \
    ./run_grok_studio.command >> "$LOG_FILE" 2>&1 </dev/null &
)

for _ in {1..60}; do
  if /usr/bin/curl -fsS "http://${HOST}:${PORT}/api/state" >/dev/null 2>&1; then
    exit 0
  fi
  /bin/sleep 0.25
done

show_dialog "Grok Studio Lab을 시작하지 못했습니다.
오류 로그를 확인해주세요:
$LOG_FILE"
exit 1
