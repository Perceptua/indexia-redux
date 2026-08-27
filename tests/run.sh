#!/usr/bin/env bash
# Run the Indexia test suite. Same wrapper shape as scripts/*.sh: load the URL/secret via lib.sh,
# then hand off to the Python runner. Most tests read and write the LIVE corpus and clean up after
# themselves (see tests/lib.py corpus_guard) — there is no fixture database.
#
#   bash tests/run.sh                 # everything
#   bash tests/run.sh --unit          # only tests that need no database (fast)
#   bash tests/run.sh knn expiry      # only matching files
#   bash tests/run.sh --docs          # add the README/spec consistency lint
#
# A few checks issue a real vector query to verify the cache's live fallback, and if the ANN index is
# cold that single query rebuilds the whole graph first (~10 min at 101 notes). INDEXIA_TESTS_FAST=1
# skips just those:
#   INDEXIA_TESTS_FAST=1 bash tests/run.sh
source "$(dirname "${BASH_SOURCE[0]}")/../scripts/lib.sh"
load_env
wait_ready 5 || die "server not reachable at $BASE_URL — run scripts/up.sh first"
export BASE_URL DB ARCADEDB_ROOT_PASSWORD
exec python3 "$ROOT/tests/run_all.py" "$@"
