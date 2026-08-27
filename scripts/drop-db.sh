#!/usr/bin/env bash
# Drop a database on the server. Refuses to touch the live one, so a typo cannot delete the corpus.
#   drop-db.sh <database-name>
# Intended for scratch/restore targets (e.g. indexia_restore) created by restore.sh.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

target="${1:-}"
[[ -n "$target" ]] || die "usage: drop-db.sh <database-name>"
[[ "$target" != "$DB" ]] || die "refusing to drop the live database '$DB'"

wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
db_get "$BASE_URL/api/v1/databases" | grep -q "\"$target\"" \
  || die "no database named '$target' on the server"

log "dropping database '$target'"
db_post "$BASE_URL/api/v1/server" "{\"command\":\"drop database $target\"}" >/dev/null \
  || die "drop failed"
ok "dropped '$target'"
log "databases now:"
db_get "$BASE_URL/api/v1/databases"
echo
