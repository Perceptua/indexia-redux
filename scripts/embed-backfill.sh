#!/usr/bin/env bash
# Embed notes that were committed without an embedding (fail-open catch-up).
# Thin wrapper: load the URL/secret via lib.sh, then hand off to embed_backfill.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/embed_backfill.py" "$@"
