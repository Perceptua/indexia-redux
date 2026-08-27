#!/usr/bin/env bash
# Stop the ArcadeDB container.
#   down.sh            stop + remove container; ./data (the corpus) persists
#   down.sh --backup   take a hot backup first, then stop
#   down.sh --reset    stop AND remove volumes + wipe ./data and ./backups (destructive)
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

do_backup=false; do_reset=false
for a in "$@"; do
  case "$a" in
    --backup) do_backup=true ;;
    --reset)  do_reset=true ;;
    *) die "unknown flag: $a (use --backup and/or --reset)" ;;
  esac
done

# Stop the background daemons — they poll the DB, so there's no point spinning once the
# server is down. (Ollama is left running; it's a general daemon.)
bash "$ROOT/scripts/embed-worker.sh" stop || true
bash "$ROOT/scripts/scheduler.sh" stop || true

if $do_backup; then
  load_env
  if wait_ready 3; then bash "$ROOT/scripts/backup.sh" || log "backup failed — continuing with shutdown"
  else log "server not reachable — skipping backup"; fi
fi

if $do_reset; then
  log "stopping and REMOVING volumes + local data (destructive)"
  dc down -v
  rm -rf "${ROOT:?}/data"/* "${ROOT:?}/backups"/* 2>/dev/null || true
  ok "reset complete — ./data and ./backups cleared"
else
  log "stopping ArcadeDB (./data persists)"
  dc down
  ok "down complete"
fi
