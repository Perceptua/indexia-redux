#!/usr/bin/env bash
# Find notes in the graph — semantic (vector) or lexical/metadata search (spec §8 read side).
# Thin wrapper: load the URL/secret (and embedder config) via lib.sh, then hand off to search.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/search.py" "$@"
