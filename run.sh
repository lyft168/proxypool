#!/bin/bash
# Scheduled run: collect accounts, dedupe, classify by country. flock-guarded
# so overlapping ticks skip instead of stacking. All config comes from .env.
set -u
BASE_DIR="${BASE_DIR:-$(cd "$(dirname "$0")" && pwd)}"
# shellcheck source=/dev/null
[ -f "$BASE_DIR/.env" ] && set -a && . "$BASE_DIR/.env" && set +a
BASE_DIR="${BASE_DIR:-$(cd "$(dirname "$0")" && pwd)}"

PY="$BASE_DIR/venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
DISPLAY="${DISPLAY:-:99}"
LOG="$BASE_DIR/run.log"
stamp() { date -u +%FT%TZ; }

exec 9>"$BASE_DIR/.run.lock"
if ! flock -n 9; then
  echo "[$(stamp)] previous run still active, skip" >> "$LOG"
  exit 0
fi
{
  echo "[$(stamp)] run start"
  if [ ! -s "${POOL_FILE:-$BASE_DIR/proxies_http.txt}" ]; then
    echo "[$(stamp)] upstream proxy pool missing/empty - nothing to do, exit 0"
    exit 0
  fi
  if ! pgrep -x Xvfb >/dev/null; then
    /usr/bin/Xvfb "$DISPLAY" -screen 0 1280x1024x24 >/dev/null 2>&1 &
    sleep 2
  fi
  bash "$BASE_DIR/bootstrap_pool.sh"
  DISPLAY="$DISPLAY" "$PY" "$BASE_DIR/main.py" "${ACCOUNTS_PER_RUN:-1}"
  rc=$?
  "$PY" "$BASE_DIR/main.py" --dedupe 2>/dev/null
  bash "$BASE_DIR/country_split.sh"
  echo "[$(stamp)] run end rc=$rc accounts=$(wc -l < "$BASE_DIR/accounts.jsonl" 2>/dev/null || echo 0) px=$(wc -l < "$BASE_DIR/gologin_proxies.txt" 2>/dev/null || echo 0) countries=$(paste -sd, - "$BASE_DIR/proxy_list.txt" 2>/dev/null)"
} >> "$LOG" 2>&1
