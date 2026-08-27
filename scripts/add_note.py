#!/usr/bin/env python3
"""Workflow 1 — new-note ingestion (spec §12.3 ADD_NOTE, human-committed).

Every optional flag maps to a human-facing Note field (ddl/schema.sql §20-25).
Required fields that aren't supplied are auto-generated (id, created_at) or
requested (body — from --body, --body-file, or stdin).

Ingestion is purely structural (spec §3.2): the only edge this can create is BEGETS, via
--parent/--mode. Correcting an existing note is not an ingestion act — add the corrected note
here, then bind it back with `link.sh suggest <new> <old> --mode inhibits` and ratify (§6, §7).

Examples:
  scripts/add-note.sh --body "A note carries exactly one idea, in full sentences."
  scripts/add-note.sh --title "Atomicity" --body "..." --author human --source-ref "Luhmann 1992"
  scripts/add-note.sh --parent 20260722T101500000Z --mode continues --body "..."
  echo "One atomic idea." | scripts/add-note.sh --title "Piped note"

Run it through the wrapper (scripts/add-note.sh) so BASE_URL / DB /
ARCADEDB_ROOT_PASSWORD are loaded from docker/.env.
"""
import argparse
import sys

import notelib


def read_body(args):
    if args.body:
        return args.body
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as fh:
            return fh.read().strip()
    # No body flag: take it from stdin (piped) or an interactive prompt.
    if sys.stdin.isatty():
        sys.stderr.write("Enter the note body (one atomic idea), then Ctrl-D:\n")
    body = sys.stdin.read().strip()
    if not body:
        sys.exit("[add-note] no body — pass --body, --body-file, or pipe text on stdin")
    return body


def main():
    p = argparse.ArgumentParser(
        prog="add-note", description="Capture a new note into the Indexia graph.")
    p.add_argument("--id", help="note id (default: generated, spec §4 compact UTC ms)")
    p.add_argument("--title", help="optional human label")
    p.add_argument("--body", help="the atomic note (one idea, full sentences)")
    p.add_argument("--body-file", dest="body_file",
                   help="read the body from this file instead of --body")
    p.add_argument("--created-at", dest="created_at",
                   help="ISO-8601 UTC, e.g. 2026-07-15T23:42:14.123Z (default: id's instant)")
    p.add_argument("--author", help=f"human | llm_assisted (default: {notelib.DEFAULT_AUTHOR})")
    p.add_argument("--source-ref", dest="source_ref", help="optional provenance pointer")
    p.add_argument("--parent", help="id of the note this one is begotten by (adds a BEGETS edge)")
    p.add_argument("--mode", help="lineage mode for --parent: continues | branches (spec §4)")
    p.add_argument("--correct", metavar="ID",
                   help="in-place cosmetic fix of ID's body (typos/formatting only; drift-checked, §6)")
    p.add_argument("--max-drift", type=float, default=notelib.CORRECT_COSMETIC_MAX_DRIFT,
                   dest="max_drift", help="reject --correct if embedding drift ≥ this "
                   "(a meaning change is a correction: add a note, then bind it back "
                   "with link.sh --mode inhibits)")
    p.add_argument("--embed", action="store_true",
                   help="embed inline now (blocks ~seconds), overriding async default")
    p.add_argument("--no-embed", action="store_true",
                   help="never embed inline (the default; the worker embeds it later)")
    p.add_argument("--json", action="store_true", help="print the commit result as JSON")
    args = p.parse_args()

    # Default (embed=None) follows INDEXIA_EMBED_ON_COMMIT (async): the note commits
    # now and the background worker embeds it. --embed forces inline; --no-embed forbids.
    embed = True if args.embed else (False if args.no_embed else None)

    fields = {
        "id": args.id,
        "title": args.title,
        "body": read_body(args),
        "created_at": args.created_at,
        "author": args.author,
        "source_ref": args.source_ref,
    }

    # Every workflow commits through the central Ingestor (atomic Note + Op + edges).
    ing = notelib.Ingestor()

    # CORRECT_COSMETIC is the one in-place path (spec §6): a typo/formatting fix, drift-checked.
    if args.correct:
        if args.parent:
            sys.exit("[add-note] --correct is standalone — not with --parent "
                     "(a meaning change is a new note, then a BINDS{inhibits} back to the old)")
        try:
            r = ing.correct_cosmetic(args.correct, fields["body"], max_drift=args.max_drift)
        except (notelib.CosmeticDriftError, ValueError) as e:
            sys.exit(f"[add-note] {e}")
        except notelib.ArcadeError as e:
            sys.exit(f"[add-note] correct failed: {e}\n  SQL: {e.sql}")
        if args.json:
            import json
            print(json.dumps(r._asdict(), indent=2, ensure_ascii=False))
        elif r.op_id is None:
            print(f"[add-note] no change — {args.correct} already has that body")
        else:
            drift = f"drift {r.drift}" if r.drift is not None else "drift unchecked (no embedder/embedding)"
            print(f"[add-note] CORRECT_COSMETIC Note {r.note_id}  ·  Op {r.op_id}  ·  {drift}"
                  f"{', re-embedded' if r.reembedded else ''}")
        return

    try:
        result = ing.commit(fields, embed=embed, parent=args.parent, mode=args.mode)
    except notelib.DuplicateNote as e:
        sys.exit(f"[add-note] {e}")
    except notelib.ArcadeError as e:
        sys.exit(f"[add-note] insert failed: {e}\n  SQL: {e.sql}")
    except ValueError as e:
        sys.exit(f"[add-note] {e}")

    if result.warning:   # an inline embed was attempted and failed (fail-open)
        sys.stderr.write(
            f"[add-note] not embedded: {result.warning}\n"
            f"[add-note] note is committed; the worker/backfill will embed it later\n")

    if args.json:
        import json
        print(json.dumps(result._asdict(), indent=2, ensure_ascii=False))
    else:
        if result.embedded:
            emb = f"embedded ({result.embedding_model})"
        elif result.warning:
            emb = "not embedded (embedder error — queued)"
        else:
            emb = "queued for embedding"
        rel = ""
        if result.begets:
            parent, m = result.begets
            rel = f"  ·  {m} from {parent}"
        print(f"[add-note] {result.rule} Note {result.note_id}  ·  Op {result.op_id}  ·  {emb}{rel}")


if __name__ == "__main__":
    main()
