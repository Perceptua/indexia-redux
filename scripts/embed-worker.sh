#!/usr/bin/env bash
# Manage the async embed worker — start | stop | status | run.
# The worker drains pending (unembedded) notes in the background so commits never
# block on the slow embedder (async embed-on-commit). Needs the DB + embedder up.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
LOG="${INDEXIA_EMBED_WORKER_LOG:-$HOME/.indexia/embed-worker.log}"
NAME="embed-worker"
SCRIPT="$ROOT/scripts/embed_worker.py"

running() { daemon_running "$NAME" "$SCRIPT"; }

with_env() { load_env; export BASE_URL DB ARCADEDB_ROOT_PASSWORD; }

case "${1:-status}" in
  start)
    running && { echo "[worker] already running"; exit 0; }
    mkdir -p "$(dirname "$LOG")"
    with_env
    # Detached so it outlives this wsl invocation (systemd PID 1 reaps it).
    setsid python3 "$ROOT/scripts/embed_worker.py" "${@:2}" >>"$LOG" 2>&1 </dev/null &
    daemon_write_pid "$NAME" $!
    echo "[worker] started (pid $!), log: $LOG"
    ;;
  run)   # run in the foreground (for debugging); pass-through args
    with_env
    exec python3 "$ROOT/scripts/embed_worker.py" "${@:2}"
    ;;
  stop)
    daemon_stop "$NAME" "$SCRIPT" && echo "[worker] stopped" || echo "[worker] not running"
    ;;
  status)
    running && echo "[worker] UP (pid $(daemon_pid "$NAME" "$SCRIPT"))" || echo "[worker] DOWN"
    if with_env 2>/dev/null; then
      python3 -c "import sys; sys.path.insert(0, '$ROOT/scripts'); import notelib; db=notelib.Arcade(); print('[worker] pending (unembedded):', notelib.first_row(db.query('SELECT count(*) AS n FROM Note WHERE embedding IS NULL')).get('n'))" 2>/dev/null || true
    fi
    ;;
  *) echo "usage: embed-worker.sh [start|stop|status|run]" >&2; exit 2 ;;
esac
