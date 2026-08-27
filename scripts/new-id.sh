#!/usr/bin/env bash
# Mint fresh, unique note ids (spec §4) for naming staging/<id>.md files.
# Offline (no DB / no env needed). Usage: new-id.sh [count]
exec python3 "$(dirname "${BASH_SOURCE[0]}")/new_id.py" "$@"
