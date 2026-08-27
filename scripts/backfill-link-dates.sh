#!/usr/bin/env bash
# backfill-link-dates — date BINDS edges that predate BINDS.created_at, from the Op log.
# One-time migration (also the repair path after restoring an older backup); idempotent.
# Wrapper → backfill_link_dates.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/backfill_link_dates.py" "$@"
