#!/usr/bin/env bash
# Manage the graph UI server — start | stop | status | run.
# Serves a graph view of the corpus at http://127.0.0.1:8420/ (loopback only; reachable from a
# Windows browser through WSL2's localhost forwarding). Needs the DB up.
# READING it changes nothing — no property, no edge, no Op, and never Note.visited (spec §13.2).
# WRITING goes through notelib's Ingestor/LinkManager, so every change lands with its Op (§12.3).
# Pass --read-only (to start or run) for the pre-v0.8.1 surface, where every write answers 403.
# See scripts/ui.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
LOG="${INDEXIA_UI_LOG:-$HOME/.indexia/ui.log}"
PORT="${INDEXIA_UI_PORT:-8420}"
NAME="ui"
SCRIPT="$ROOT/scripts/ui.py"

running() { daemon_running "$NAME" "$SCRIPT"; }

with_env() { load_env; export BASE_URL DB ARCADEDB_ROOT_PASSWORD; }

case "${1:-status}" in
  start)
    running && { echo "[ui] already running — http://127.0.0.1:$PORT/"; exit 0; }
    mkdir -p "$(dirname "$LOG")"
    with_env
    # Detached so it outlives this wsl invocation (systemd PID 1 reaps it).
    setsid python3 "$ROOT/scripts/ui.py" "${@:2}" >>"$LOG" 2>&1 </dev/null &
    daemon_write_pid "$NAME" $!
    echo "[ui] started (pid $!) — http://127.0.0.1:$PORT/ , log: $LOG"
    ;;
  run)   # foreground (for debugging); pass-through args (e.g. --snapshot --json, --port N)
    with_env
    exec python3 "$ROOT/scripts/ui.py" "${@:2}"
    ;;
  stop)
    daemon_stop "$NAME" "$SCRIPT" && echo "[ui] stopped" || echo "[ui] not running"
    ;;
  status)
    if running; then
      echo "[ui] UP (pid $(daemon_pid "$NAME" "$SCRIPT")) — http://127.0.0.1:$PORT/"
      curl -s "http://127.0.0.1:$PORT/api/health" || echo "[ui] but /api/health did not answer"
      echo
    else
      echo "[ui] DOWN"
    fi
    ;;
  *) echo "usage: ui.sh [start|stop|status|run] [--read-only] [--port N]" >&2; exit 2 ;;
esac
