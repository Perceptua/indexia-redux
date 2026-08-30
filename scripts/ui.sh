#!/usr/bin/env bash
# Manage the graph UI server — start | stop | status | run.
# Serves a graph view of the corpus at http://127.0.0.1:8420/ (loopback only; reachable from a
# Windows browser through WSL2's localhost forwarding). Needs the DB up.
# READING it changes nothing — no property, no edge, no Op, and never Note.visited (spec §13.2).
# WRITING goes through notelib's Ingestor/LinkManager, so every change lands with its Op (§12.3).
# Pass --read-only (to start or run) for the pre-v0.8.1 surface, where every write answers 403.
# Pass --tailscale (to start or run) to bind the tailnet IP over HTTPS instead — see ui.py and
# scripts/tailscale.py. --tailscale picks its own host/port at runtime, so the hints below only
# apply to the default loopback case.
# See scripts/ui.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
LOG="${INDEXIA_UI_LOG:-$HOME/.indexia/ui.log}"
PORT="${INDEXIA_UI_PORT:-8420}"
NAME="ui"
SCRIPT="$ROOT/scripts/ui.py"

running() { daemon_running "$NAME" "$SCRIPT"; }

with_env() { load_env; export BASE_URL DB ARCADEDB_ROOT_PASSWORD; }

# What to print for a server about to start (or just found running) with these args.
url_hint() {
  for a in "${@:2}"; do
    [ "$a" = "--tailscale" ] && { echo "check the log for the https:// tailnet URL"; return; }
  done
  echo "http://127.0.0.1:$PORT/"
}

# Whether the already-running daemon (if any) was started with --tailscale, by reading its own
# cmdline rather than remembering — a `status` invocation carries none of the args `start` did.
tailscale_running() {
  local pid
  pid="$(daemon_pid "$NAME" "$SCRIPT")" || return 1
  tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -qw -- --tailscale
}

case "${1:-status}" in
  start)
    running && { echo "[ui] already running — $(url_hint "$@")"; exit 0; }
    mkdir -p "$(dirname "$LOG")"
    with_env
    # Detached so it outlives this wsl invocation (systemd PID 1 reaps it).
    setsid python3 "$ROOT/scripts/ui.py" "${@:2}" >>"$LOG" 2>&1 </dev/null &
    daemon_write_pid "$NAME" $!
    echo "[ui] started (pid $!) — $(url_hint "$@") , log: $LOG"
    ;;
  run)   # foreground (for debugging); pass-through args (e.g. --snapshot --json, --port N, --tailscale)
    with_env
    exec python3 "$ROOT/scripts/ui.py" "${@:2}"
    ;;
  stop)
    daemon_stop "$NAME" "$SCRIPT" && echo "[ui] stopped" || echo "[ui] not running"
    ;;
  status)
    if running; then
      pid="$(daemon_pid "$NAME" "$SCRIPT")"
      if tailscale_running; then
        ip="$(tailscale ip -4 2>/dev/null)"
        echo "[ui] UP (pid $pid) — serving over Tailscale; check the log for the https:// URL"
        if [ -n "$ip" ]; then
          curl -s "https://$ip:$PORT/api/health" || echo "[ui] but /api/health did not answer"
        else
          echo "[ui] (tailscale ip -4 failed — is tailscale up?)"
        fi
      else
        echo "[ui] UP (pid $pid) — http://127.0.0.1:$PORT/"
        curl -s "http://127.0.0.1:$PORT/api/health" || echo "[ui] but /api/health did not answer"
      fi
      echo
    else
      echo "[ui] DOWN"
    fi
    ;;
  *) echo "usage: ui.sh [start|stop|status|run] [--read-only] [--tailscale] [--port N]" >&2; exit 2 ;;
esac
