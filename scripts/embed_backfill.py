#!/usr/bin/env python3
"""embed-backfill — one-shot pass that embeds notes lacking an embedding.

Notes commit without an embedding (async embed-on-commit, or a fail-open commit while
the embedder was down). This drains that backlog now, one transaction per note that
also appends an Op(EMBED). Idempotent and safe to re-run. The background worker
(scripts/embed-worker.sh) does the same thing continuously; this is the manual pass.

Run via scripts/embed-backfill.sh. Requires the embedder to be up
(scripts/embed-server.sh start); if it isn't, the run stops cleanly and reports.
"""
import argparse
import sys

import notelib

# Notes per Ollama call. embed_existing_many sends this many texts in one HTTP request, so a
# backlog (the default --limit 0 is "all pending") is chunked rather than sent as one arbitrarily
# large request — matches embed-worker's own --batch default, so a backlog drains the same way
# whichever path picks it up first.
BACKFILL_CHUNK = 20


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def main():
    p = argparse.ArgumentParser(
        prog="embed-backfill", description="Embed notes that lack an embedding.")
    p.add_argument("--limit", type=int, default=0, help="max notes to embed this run (0 = all)")
    p.add_argument("--dry-run", action="store_true", help="report what would be embedded; write nothing")
    args = p.parse_args()

    ing = notelib.Ingestor()
    if ing.embedder is None:
        sys.exit("[embed-backfill] embedding is disabled (INDEXIA_EMBED_BACKEND=none)")

    notes = ing.pending_note_ids(args.limit)
    if not notes:
        print("[embed-backfill] no unembedded notes — corpus is fully embedded")
        return
    print(f"[embed-backfill] {len(notes)} note(s) to embed via {ing.embedder.model}")

    if args.dry_run:
        for n in notes:
            print(f"  DRY  {n['id']}: would embed")
        print(f"[embed-backfill] embedded {len(notes)}, skipped 0")
        return

    done = skipped = 0
    for chunk in _chunks(notes, BACKFILL_CHUNK):
        try:
            embedded, skips = ing.embed_existing_many(
                [{"id": n["id"], "body": n.get("body") or ""} for n in chunk])
        except notelib.EmbedderUnavailable as e:
            print(f"  embedder unavailable: {e}")
            print("  stopping — start it with scripts/embed-server.sh start, then re-run")
            break
        for note_id, emb in embedded:
            print(f"  OK   {note_id} [{emb.model}]")
        for note_id, err in skips:
            print(f"  SKIP {note_id}: {err}")
        done += len(embedded)
        skipped += len(skips)

    print(f"[embed-backfill] embedded {done}, skipped {skipped}")
    sys.exit(1 if skipped and not done else 0)


if __name__ == "__main__":
    main()
