#!/usr/bin/env bash
# Replay an associative layer (BINDS) from a TSV manifest — spec §7.
# Thin wrapper: load the URL/secret via lib.sh, then hand off to seed_binds.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/seed_binds.py" "$@"
