#!/usr/bin/env bash
# analytics — read-only reports over the graph (spec §13): fitness, criticality, communities,
# autocatalysis, visited, walks, walk, replay. Writes NOTHING to the database — no property, no
# edge, not even an Op. Most reports accept --as-of <timestamp> to ask the graph as it stood
# then. Wrapper → analytics/cli.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/analytics/cli.py" "$@"
