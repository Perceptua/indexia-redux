#!/usr/bin/env bash
# Restore a backup zip into a NEW database (never clobbers the live corpus).
#   restore.sh <backup-zip> [target-db=${DB}_restore]
# <backup-zip> may be an absolute path, a path under ./backups, or just the zip filename
# (it is searched for under ./backups, including the per-database subdir).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

zip="${1:-}"; target="${2:-${DB}_restore}"
[[ -n "$zip" ]] || die "usage: restore.sh <backup-zip> [target-db]"
if [[ ! -f "$zip" ]]; then
  found="$(find "$ROOT/backups" -name "$(basename "$zip")" -type f 2>/dev/null | head -1 || true)"
  [[ -n "$found" ]] || die "backup not found under ./backups: $1"
  zip="$found"
fi
case "$zip" in
  "$ROOT"/backups/*) rel="${zip#"$ROOT"/backups/}" ;;   # ./backups maps to /home/arcadedb/backups
  *) die "backup must live under ./backups (got: $zip)" ;;
esac

wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
[[ "$target" != "$DB" ]] || die "refusing to restore over the live database '$DB' — choose another target name"

log "restoring '$rel' → database '$target' (live '$DB' untouched)"
# ArcadeDB's restore CLI: -f <backup url> -d <target database directory>. The server
# registers databases at startup, so the restored DB becomes queryable after a restart.
dc exec -T arcadedb bin/restore.sh \
  -f "file:///home/arcadedb/backups/$rel" -d "/home/arcadedb/databases/$target" \
  || die "restore failed — see output above"
ok "restored into databases/$target — run scripts/down.sh && scripts/up.sh to load it"
