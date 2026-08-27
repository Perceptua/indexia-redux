#!/usr/bin/env bash
# link-expiry — weekly sweep of stale `suggested` BINDS edges, so the ratification queue
# stays human-sized (spec §7, §8.2, §12.6). Wrapper → link_expiry.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/link_expiry.py" "$@"
