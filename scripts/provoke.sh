#!/usr/bin/env bash
# Provocation move 1 — semantically near, graph-far (spec §8.1). Wrapper → provoke.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/provoke.py" "$@"
