#!/usr/bin/env bash
# Command-line launcher for the transcribe-notes skill.
#
# Starts a normal *interactive* `claude` session (not headless) opened straight into the skill,
# so staged scans get transcribed with note boundaries confirmed and each note ratified live, right there
# in the terminal — the same flow as invoking the skill from an existing chat, just started from
# the command line instead.
#
# Runs with --permission-mode bypassPermissions so routine tool calls (reading the scan, minting
# an id, writing the staging file) don't each stop for approval. That mode skips ALL permission
# checks for the session, not just the skill's known steps — reasonable here since it's your own
# machine, your own repo, and you're watching the whole interactive session anyway, but worth
# knowing if you ever point this at something less trusted.
#
# Usage:
#   bash scripts/transcribe-scans.sh                # let the skill find the image(s) in staging/scans/
#   bash scripts/transcribe-scans.sh IMG_1234.jpg    # point it at one specific scan
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"

CLAUDE_BIN="${CLAUDE_BIN:-claude}"
command -v "$CLAUDE_BIN" >/dev/null 2>&1 \
  || die "claude CLI not found (\$CLAUDE_BIN=$CLAUDE_BIN) — set CLAUDE_BIN to its full path"

if [[ -n "${1:-}" ]]; then
  target="$1"
  [[ "$target" != staging/scans/* ]] && target="staging/scans/$target"
  exec "$CLAUDE_BIN" --permission-mode bypassPermissions "/transcribe-notes Please transcribe $target."
else
  exec "$CLAUDE_BIN" --permission-mode bypassPermissions "/transcribe-notes Please transcribe the scan(s) waiting in staging/scans/."
fi
