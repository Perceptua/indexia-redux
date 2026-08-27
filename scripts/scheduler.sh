#!/usr/bin/env bash
# Manage the maintenance scheduler daemon — start | stop | status | run.
# The scheduler runs the maintenance jobs on a cadence (knn-cache then provocation-digest nightly;
# resurface and link-expiry weekly). Needs the DB up (jobs fail open and retry if it isn't).
# Every job here writes; the read-only measurements live in scripts/analytics.sh (spec §13).
# See scripts/scheduler.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
LOG="${INDEXIA_SCHEDULER_LOG:-$HOME/.indexia/scheduler.log}"
NAME="scheduler"
SCRIPT="$ROOT/scripts/scheduler.py"

running() { daemon_running "$NAME" "$SCRIPT"; }

with_env() { load_env; export BASE_URL DB ARCADEDB_ROOT_PASSWORD; }

case "${1:-status}" in
  start)
    running && { echo "[scheduler] already running"; exit 0; }
    mkdir -p "$(dirname "$LOG")"
    with_env
    # Detached so it outlives this wsl invocation (systemd PID 1 reaps it).
    setsid python3 "$ROOT/scripts/scheduler.py" "${@:2}" >>"$LOG" 2>&1 </dev/null &
    daemon_write_pid "$NAME" $!
    echo "[scheduler] started (pid $!), log: $LOG"
    ;;
  run)   # run in the foreground (for debugging); pass-through args (e.g. --once, --force)
    with_env
    exec python3 "$ROOT/scripts/scheduler.py" "${@:2}"
    ;;
  stop)
    daemon_stop "$NAME" "$SCRIPT" && echo "[scheduler] stopped" || echo "[scheduler] not running"
    ;;
  status)
    running && echo "[scheduler] UP (pid $(daemon_pid "$NAME" "$SCRIPT"))" || echo "[scheduler] DOWN"
    ;;
  *) echo "usage: scheduler.sh [start|stop|status|run]" >&2; exit 2 ;;
esac
