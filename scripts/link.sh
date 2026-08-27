#!/usr/bin/env bash
# Manage associative BINDS edges — the ratification flow (spec §7, §12.3).
# Thin wrapper: load the URL/secret via lib.sh, then hand off to link.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/link.py" "$@"
