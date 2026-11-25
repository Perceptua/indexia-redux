#!/usr/bin/env bash
# vector-reindex — drop + recreate the Note LSM_VECTOR index, compacting the deleted
# vectors that make every server start rebuild the ANN graph from scratch.
# Wrapper → vector_reindex.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/vector_reindex.py" "$@"
