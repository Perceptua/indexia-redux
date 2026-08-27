#!/usr/bin/env bash
# Command-line launcher for the review-transcripts skill.
#
# Starts a normal *interactive* `claude` session (not headless) opened straight into the skill, so
# a staged audio transcript gets reviewed with corrections and note boundaries confirmed and each
# note ratified live, right there in the terminal — the same flow as invoking the skill from an
# existing chat, just started from the command line instead.
#
# Runs with --permission-mode bypassPermissions so routine tool calls (reading the transcript,
# minting an id, writing the staging file) don't each stop for approval. That mode skips ALL
# permission checks for the session, not just the skill's known steps — reasonable here since it's
# your own machine, your own repo, and you're watching the whole interactive session anyway, but
# worth knowing if you ever point this at something less trusted.
#
# Usage:
#   bash scripts/review-transcripts.sh                  # let the skill find the transcript(s) in staging/transcripts/
#   bash scripts/review-transcripts.sh memo-2026-08.txt  # point it at one specific transcript
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cd "$ROOT"

CLAUDE_BIN="${CLAUDE_BIN:-claude}"
command -v "$CLAUDE_BIN" >/dev/null 2>&1 \
  || die "claude CLI not found (\$CLAUDE_BIN=$CLAUDE_BIN) — set CLAUDE_BIN to its full path"

if [[ -n "${1:-}" ]]; then
  target="$1"
  [[ "$target" != staging/transcripts/* ]] && target="staging/transcripts/$target"
  exec "$CLAUDE_BIN" --permission-mode bypassPermissions "/review-transcripts Please review $target."
else
  exec "$CLAUDE_BIN" --permission-mode bypassPermissions "/review-transcripts Please review the transcript(s) waiting in staging/transcripts/."
fi
