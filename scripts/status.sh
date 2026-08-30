#!/usr/bin/env bash
# Show container state and server readiness.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "containers:"
dc ps || true
echo

# Load env without dying (status should work even if .env is absent).
[[ -f "$ENV_FILE" ]] && { set -a; . "$ENV_FILE"; set +a; BASE_URL="${INDEXIA_URL:-https://localhost:${INDEXIA_HTTP_PORT:-2480}}"; }

if [[ -n "${ARCADEDB_ROOT_PASSWORD:-}" ]] && wait_ready 1; then
  ok "server READY at $BASE_URL"
  db_get "$BASE_URL/api/v1/databases" \
    | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); r=d.get("result", d)
    print("[indexia] databases:", ", ".join(r) if isinstance(r,list) else r)
except Exception: pass' || true
else
  log "server not reachable at $BASE_URL (start it with scripts/up.sh)"
fi

# db-mode is a marker up.sh writes, not anything docker-compose remembers — a resolved port
# binding tells this fresh shell nothing about how it got that way. See lib.sh's db_mode_write.
# --tailscale ADDS a second publish rather than replacing the loopback one above, so this is
# purely informational — it does not change what was just checked.
if [[ "$(db_mode_read)" == "tailscale" ]]; then
  fqdn="$(tailscale_fqdn)"
  if [[ -n "$fqdn" ]]; then
    log "also reachable over the tailnet at https://$fqdn:${INDEXIA_HTTP_PORT:-2480}"
  else
    log "db-mode says tailscale, but this node's MagicDNS name could not be read (is tailscale up?)"
  fi
fi
