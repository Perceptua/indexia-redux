#!/usr/bin/env bash
# provocation-digest — render all six generativity moves to recent/provocations.md and stage
# move-1/2 suggestions (spec §8.1, §12.6). Run nightly by a scheduler. Wrapper → provocation_digest.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/provocation_digest.py" "$@"
