#!/usr/bin/env bash
# promote-type — register a new vertex/edge type (schema growth, spec §3.3, §12.3). Wrapper → promote_type.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/promote_type.py" "$@"
