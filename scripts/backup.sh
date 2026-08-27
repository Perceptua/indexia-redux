#!/usr/bin/env bash
# Take a hot (non-blocking) backup of the database. Writes a zip into ./backups.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"

log "hot backup of '$DB' (BACKUP DATABASE)…"
resp="$(db_post "$BASE_URL/api/v1/command/$DB" '{"language":"sql","command":"BACKUP DATABASE"}')" || die "backup failed: $resp"

# ArcadeDB writes into a per-database subdir: ./backups/<db>/<db>-backup-<ts>.zip
newest="$(find "$ROOT/backups" -name '*.zip' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2- || true)"
if [[ -n "$newest" ]]; then
  ok "backup written: ${newest#"$ROOT"/} ($(du -h "$newest" | cut -f1))"
else
  log "server response: $resp"
  log "no zip found under ./backups — check arcadedb.server.backupDirectory"
fi
