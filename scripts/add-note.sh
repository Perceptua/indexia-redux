#!/usr/bin/env bash
# Workflow 1 — capture a new note into the graph (spec §12.3 ADD_NOTE).
# Thin wrapper: load the URL/secret via lib.sh, then hand off to add_note.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/add_note.py" "$@"
