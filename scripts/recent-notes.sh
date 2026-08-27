#!/usr/bin/env bash
# Startup digest — render the most recent day's notes to recent/recent-notes.md.
# Called by scripts/up.sh after the schema is applied; safe to run any time.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/recent_notes.py" "$@"
