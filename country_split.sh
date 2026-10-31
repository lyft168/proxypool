#!/bin/bash
# Classify every proxy by exit country -> proxy_<CC>.txt + proxy_list.txt index.
# The lookup request goes through the proxy itself, so the country reported is
# the actual exit country. All config comes from .env.
set -u
BASE_DIR="${BASE_DIR:-$(cd "$(dirname "$0")" && pwd)}"
# shellcheck source=/dev/null
[ -f "$BASE_DIR/.env" ] && set -a && . "$BASE_DIR/.env" && set +a
BASE_DIR="${BASE_DIR:-$(cd "$(dirname "$0")" && pwd)}"

GEO_API_URL="${GEO_API_URL:-http://ip-api.com/json/?fields=countryCode}"
POOL_OUT="${POOL_OUT:-$BASE_DIR/gologin_proxies.txt}"
LOG="$BASE_DIR/country.log"
stamp() { date -u +%FT%TZ; }

exec 8>"$BASE_DIR/.split.lock"
flock -n 8 || exit 0
cd "$BASE_DIR" || exit 1
: > .countries.tmp
declare -A seen
while IFS=: read -r h pt u pw; do
  [ -z "$u" ] && continue
  if [ -n "${seen[$u]:-}" ]; then cc="${seen[$u]}"; else
    cc=$(curl -s --max-time 12 -x "http://$u:$pw@$h:$pt" "$GEO_API_URL" \
         | grep -o '"countryCode":"[A-Z]*"' | cut -d'"' -f4)
    [ -z "$cc" ] && cc="XX"
    seen[$u]="$cc"
  fi
  echo "$cc|$h:$pt:$u:$pw" >> .countries.tmp
done < "$POOL_OUT"

rm -f proxy_??.txt proxies_live.txt proxies_dead.txt
: > proxy_list.txt
: > proxies_live.txt
: > proxies_dead.txt
for cc in $(cut -d'|' -f1 .countries.tmp | sort -u); do
  grep "^$cc|" .countries.tmp | cut -d'|' -f2 > "proxy_${cc}.txt"
  n=$(wc -l < "proxy_${cc}.txt")
  echo "proxy_${cc}.txt $n" >> proxy_list.txt
  # XX = failed country lookup = effectively dead/unreachable through this proxy.
  if [ "$cc" = "XX" ]; then
    cat "proxy_${cc}.txt" >> proxies_dead.txt
  else
    cat "proxy_${cc}.txt" >> proxies_live.txt
  fi
done
alive=$(wc -l < proxies_live.txt)
dead=$(wc -l < proxies_dead.txt)
echo "proxies_live.txt $alive" >> proxy_list.txt
echo "proxies_dead.txt $dead" >> proxy_list.txt
echo "[$(stamp)] classified=$(wc -l < .countries.tmp) alive=$alive dead=$dead countries=$(cut -d'|' -f1 .countries.tmp | sort -u | paste -sd, -)" >> "$LOG"
