#!/usr/bin/env bash
# Follow ArcadeDB container logs. Optional arg: number of tail lines (default 100).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
exec dc logs -f --tail "${1:-100}" arcadedb
