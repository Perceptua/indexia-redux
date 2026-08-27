#!/usr/bin/env bash
# Record walks — reading/thinking sessions through the graph (spec §11.1, §13). Writes an Op per
# event plus Note.visited; read them back with analytics.sh walk/walks/replay. Wrapper → walk.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/walk.py" "$@"
