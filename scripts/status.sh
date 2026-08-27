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
