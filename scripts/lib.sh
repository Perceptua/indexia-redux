#!/usr/bin/env bash
# Shared helpers for Indexia lifecycle scripts. `source` this — do not execute directly.
# Establishes ROOT, loads secrets from docker/.env, and wraps the ArcadeDB HTTP API.
set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$LIB_DIR/.." && pwd)"

DB="${INDEXIA_DB:-indexia}"
INDEXIA_HTTP_PORT="${INDEXIA_HTTP_PORT:-2480}"
BASE_URL="${INDEXIA_URL:-https://localhost:${INDEXIA_HTTP_PORT}}"
COMPOSE_FILE="$ROOT/docker/docker-compose.yml"
ENV_FILE="$ROOT/docker/.env"

c_info=$'\033[1;36m'; c_err=$'\033[1;31m'; c_ok=$'\033[1;32m'; c_off=$'\033[0m'
log() { printf '%s[indexia]%s %s\n' "$c_info" "$c_off" "$*"; }
ok()  { printf '%s[indexia]%s %s\n' "$c_ok"   "$c_off" "$*"; }
die() { printf '%s[indexia] ERROR:%s %s\n' "$c_err" "$c_off" "$*" >&2; exit 1; }

# Load docker/.env into the environment (secrets). Dies if missing/incomplete.
load_env() {
  [[ -f "$ENV_FILE" ]] || die "docker/.env not found — copy docker/.env.example to docker/.env and fill it in"
  set -a; . "$ENV_FILE"; set +a
  [[ -n "${ARCADEDB_ROOT_PASSWORD:-}" ]] || die "ARCADEDB_ROOT_PASSWORD not set in docker/.env"
  BASE_URL="${INDEXIA_URL:-https://localhost:${INDEXIA_HTTP_PORT:-2480}}"   # re-derive after .env may set the port
}

# docker compose wrapper. -f sets the project dir to docker/, so ../data etc. resolve to the repo root.
dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

# POST to the DB HTTP API. Prints response body; returns non-zero on HTTP >= 400.
# Usage: db_post <url> [json-body]
db_post() {
  local url="$1" data="${2:-}" out code
  local args=(-sk -u "root:$ARCADEDB_ROOT_PASSWORD" -H 'Content-Type: application/json' -w $'\n%{http_code}' -X POST "$url")
  [[ -n "$data" ]] && args+=(--data "$data")
  out="$(curl "${args[@]}")"
  code="${out##*$'\n'}"; out="${out%$'\n'*}"
  printf '%s' "$out"
  [[ "${code:-0}" -lt 400 ]]
}

# GET from the DB HTTP API. Prints response body; returns non-zero on HTTP >= 400.
db_get() {
  local url="$1" out code
  out="$(curl -sk -u "root:$ARCADEDB_ROOT_PASSWORD" -w $'\n%{http_code}' "$url")"
  code="${out##*$'\n'}"; out="${out%$'\n'*}"
  printf '%s' "$out"
  [[ "${code:-0}" -lt 400 ]]
}

# Encode stdin as {"language":"sqlscript","command":<stdin>} (jq-free; python3 is always present).
sqlscript_payload() {
  python3 -c 'import json,sys; print(json.dumps({"language":"sqlscript","command":sys.stdin.read()}))'
}

# ---- daemon process tracking ------------------------------------------------
# One pid file per daemon, so start/stop/status name exactly one process.
#
# This replaces `pgrep -f scripts/<name>.py`, which matched any command line *containing* that
# string — including the very `bash -lc '… scripts/ui.py …'` invocation asking the question. So
# `stop` could kill the caller's own shell, which it did, twice, in one sitting. A daemon should
# be identified by the process it is, not by a substring anyone might type.
#
# Pid files go stale (the process died; worse, the number was reused), so daemon_pid confirms the
# pid is alive AND that its command line still names the script before believing it.
#
# Callers MUST pass $ROOT/scripts/x.py, not the bare relative path: other projects on this
# machine (eliciter) run their own same-named scripts/ui.py, and a relative match doesn't
# distinguish whose process it is — indexia's ui-up once mistook eliciter's ui.py for its own
# and never started, while status happily reported UP against the wrong process.
PID_DIR="${INDEXIA_RUN_DIR:-$HOME/.indexia}"

daemon_pidfile() { printf '%s/%s.pid\n' "$PID_DIR" "$1"; }

# Print the live pid of a daemon, or nothing. Usage: daemon_pid <name> <$ROOT/scripts/x.py>
daemon_pid() {
  local name="$1" script="$2" pidfile pid=""
  pidfile="$(daemon_pidfile "$name")"
  if [[ -r "$pidfile" ]]; then
    read -r pid <"$pidfile" 2>/dev/null || pid=""
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null \
       && tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -Fq -- "$script"; then
      printf '%s\n' "$pid"
      return 0
    fi
    rm -f "$pidfile"                      # gone, or the number belongs to something else now
  fi
  # No usable pid file. Fall back to a scan that CANNOT match an ordinary shell: the command line
  # has to begin with a python interpreter running that script. This also adopts a daemon started
  # before pid files existed, so an upgrade does not orphan a running process.
  pid="$(pgrep -f "^[^ ]*python[0-9.]* [^ ]*${script}([[:space:]]|\$)" 2>/dev/null | head -n1)"
  [[ -n "$pid" ]] || return 1
  daemon_write_pid "$name" "$pid"
  printf '%s\n' "$pid"
}

daemon_running() { daemon_pid "$@" >/dev/null 2>&1; }

daemon_write_pid() {
  mkdir -p "$PID_DIR"
  printf '%s\n' "$2" >"$(daemon_pidfile "$1")"
}

# Stop a daemon: TERM, wait up to 5s, then KILL. Exit 0 only if something was actually stopped.
daemon_stop() {
  local name="$1" script="$2" pid i
  pid="$(daemon_pid "$name" "$script")" || return 1
  kill "$pid" 2>/dev/null || true
  for ((i = 0; i < 50; i++)); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
  done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
  rm -f "$(daemon_pidfile "$name")"
  return 0
}

# Poll GET /api/v1/ready until 204/200 or timeout. Arg: max_tries (default 60, 2s apart).
wait_ready() {
  local tries="${1:-60}" i code
  for ((i=1; i<=tries; i++)); do
    code="$(curl -sk -o /dev/null -w '%{http_code}' "$BASE_URL/api/v1/ready" 2>/dev/null || true)"
    [[ "$code" == "204" || "$code" == "200" ]] && return 0
    sleep 2
  done
  return 1
}
