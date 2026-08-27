#!/usr/bin/env bash
# knn-cache — nightly rebuild of the k-NN adjacency cache the provocation moves read
# (spec §12.6). Runs before community-detect/provocation-digest so they pay no ANN rebuild.
# Wrapper → knn_cache.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/knn_cache.py" "$@"
