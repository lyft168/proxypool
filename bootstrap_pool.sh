#!/bin/bash
# Keep the active upstream pool topped up from the live-classified list.
# If POOL_FILE drops below $POOL_MIN entries, repopulate it from
# proxies_live.txt (classifier output). Prevents self-starvation when
# every registration consumes one pool slot and the pool is self-sourced.
set -u
BASE_DIR="${BASE_DIR:-$(cd "$(dirname "$0")" && pwd)}"
# shellcheck source=/dev/null
[ -f "$BASE_DIR/.env" ] && set -a && . "$BASE_DIR/.env" && set +a
BASE_DIR="${BASE_DIR:-$(cd "$(dirname "$0")" && pwd)}"

POOL_FILE="${POOL_FILE:-$BASE_DIR/proxies_http.txt}"
LIVE_FILE="${LIVE_FILE:-$BASE_DIR/proxies_live.txt}"
POOL_MIN="${POOL_MIN:-20}"
LOG="$BASE_DIR/bootstrap.log"
stamp() { date -u +%FT%TZ; }

cur=$(wc -l < "$POOL_FILE" 2>/dev/null || echo 0)
if [ "$cur" -lt "$POOL_MIN" ] && [ -s "$LIVE_FILE" ]; then
  cp "$LIVE_FILE" "$POOL_FILE"
  new=$(wc -l < "$POOL_FILE")
  echo "[$(stamp)] pool refilled from $LIVE_FILE: $cur -> $new" >> "$LOG"
fi
