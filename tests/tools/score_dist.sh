#!/usr/bin/env bash
# Calibration tool: the move-1/move-2 score distribution, for choosing the digest's staging floor.
source "$(dirname "${BASH_SOURCE[0]}")/../../scripts/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/tests/tools/score_dist.py" "$@"
