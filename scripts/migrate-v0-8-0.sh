#!/usr/bin/env bash
# migrate-v0-8-0 — one-time migration to the v0.8.0 purely-relational schema (spec §13):
# adds Note.visited + BEGETS.created_at, drops Note.fitness/activation, and drops the Trace and
# Cluster types with their edges. Irreversible — refuses to run without a recent backup unless
# --force. Also the repair path after restoring a pre-v0.8.0 backup. Wrapper → migrate_v0_8_0.py.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/scripts/migrate_v0_8_0.py" "$@"
