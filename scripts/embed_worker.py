#!/usr/bin/env python3
"""embed-worker — background loop that embeds pending notes (async embed-on-commit).

This is what makes embed-on-commit non-blocking: commits store the note immediately
with no embedding, and this worker polls for notes with a null embedding and embeds
them (Op(EMBED) each) a few seconds later — so note-taking never waits on the slow
embedder. Resilient to the DB or embedder being briefly down (it logs and retries).
Managed by scripts/embed-worker.sh.
"""
import argparse
import sys
import time
from datetime import datetime, timezone

import notelib


def _log(msg):
    """One log line, stamped UTC — the same reason scheduler.py stamps its own.

    This log is read after the fact, usually because something looks wrong now, and an unstamped
    line cannot answer the only question being asked of it: when. Both of the interesting lines
    here are transient by nature — an embedder that was briefly unreachable, a note skipped on a
    concurrent-modification retry — so undated they read as live faults long after they healed.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[embed-worker] {stamp} {msg}", flush=True)


def drain(ing, batch):
    """Embed up to `batch` pending notes in one Ollama call. Returns the count embedded;
    stops early and returns None if the embedder is unavailable (so the caller backs off)."""
    notes = ing.pending_note_ids(batch)
    if not notes:
        return 0
    try:
        embedded, skipped = ing.embed_existing_many(
            [{"id": n["id"], "body": n.get("body") or ""} for n in notes])
    except notelib.EmbedderUnavailable as e:
        _log(f"embedder unavailable: {e}")
        return None
    for note_id, _ in embedded:
        _log(f"embedded {note_id}")
    for note_id, err in skipped:
        # Not fatal to the note: it keeps its null embedding, so it is still pending and the
        # next poll picks it up again. Skipping is a deferral, never a decision.
        _log(f"skip {note_id} (stays pending, retried next poll): {err}")
    return len(embedded)


def main():
    p = argparse.ArgumentParser(prog="embed-worker", description="Background async embedder.")
    p.add_argument("--interval", type=float, default=10.0, help="seconds between polls (default 10)")
    p.add_argument("--batch", type=int, default=20, help="max notes per poll (default 20)")
    p.add_argument("--once", action="store_true", help="drain once and exit")
    args = p.parse_args()

    ing = notelib.Ingestor()
    if ing.embedder is None:
        sys.exit("[embed-worker] embedding disabled (INDEXIA_EMBED_BACKEND=none)")
    _log(f"up — polling every {args.interval}s (batch {args.batch}) via {ing.embedder.model}")

    while True:
        try:
            drain(ing, args.batch)
        except SystemExit as e:            # notelib raises SystemExit when the DB is unreachable
            _log(f"db unreachable: {e}; retrying")
        except Exception as e:             # never let the loop die on a transient error
            _log(f"error: {e}; retrying")
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
