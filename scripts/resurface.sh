#!/usr/bin/env bash
# resurface — weekly re-encounter of orphan/inhibited/anniversary notes → recent/resurface.md
# (spec §8.1 move 5, §12.6). Run weekly by a scheduler. Wrapper → resurface.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/resurface.py" "$@"
