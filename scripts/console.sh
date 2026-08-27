#!/usr/bin/env bash
# Open an interactive ArcadeDB SQL console connected to the database.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
exec dc exec arcadedb bin/console.sh "remote:localhost/$DB root $ARCADEDB_ROOT_PASSWORD"
