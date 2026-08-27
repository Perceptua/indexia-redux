#!/usr/bin/env python3
"""Workflow 2 — staging-directory ingestion.

Drop a file into staging/ whose name is the note's id (spec §4, e.g.
`20260722T101500000Z` with an optional .md/.txt/.note suffix). Inside, give the
note's properties as `name: value` lines. Recognized keys: title, body,
created_at, author, source_ref, status, id. Any line that is not a recognized
`key:` continues the previous value, so a short body can span a couple of lines;
for a long or colon-heavy body, put a line containing only `---` and everything
after it becomes the body.

For each staged file this script:
  1. derives the id from the filename and validates its shape + uniqueness,
  2. parses the properties (body is required),
  3. inserts the Note, and
  4. moves the file to staging/processed/ (or staging/failed/ on any error).

Run via scripts/ingest-staging.sh so the DB env is loaded. `--dry-run` validates
and parses everything without inserting or moving files.

Example file  staging/20260722T101500000Z.md
--------------------------------------------
    title: Folgezettel is display-only
    author: human
    source_ref: docs/spec.md §4
    ---
    The Luhmann address is a derived projection of the BEGETS ancestry,
    computed on demand — never a stored field.
"""
import argparse
import os
import re
import shutil
import sys

import notelib

EXTS = (".md", ".txt", ".note")
# Note fields, plus the one relationship pair ingestion may express: parent/mode add a BEGETS
# edge (§3.2). Corrections are NOT an ingestion act — commit the note, then bind it back with
# `link.sh suggest <new> <old> --mode inhibits` and ratify (§6, §7).
RECOGNIZED = {"id", "title", "body", "created_at", "author", "source_ref", "status",
              "parent", "mode"}
LINK_KEYS = ("parent", "mode")
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s?(.*)$")
DEFAULT_STAGING = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "staging")


def id_from_filename(fname):
    lower = fname.lower()
    for ext in EXTS:
        if lower.endswith(ext):
            return fname[: -len(ext)]
    return fname


def parse_props(text):
    """Parse `name: value` lines into a dict, folding unrecognized lines into the
    current value and treating everything after a lone `---` as the body."""
    props, current = {}, None
    lines = text.splitlines()
    fence = None
    for idx, raw in enumerate(lines):
        if raw.strip() == "---":
            fence = idx
            break
        m = KEY_RE.match(raw)
        key = m.group(1).lower().replace("-", "_") if m else None  # source-ref -> source_ref
        if key in RECOGNIZED:
            current = key
            props[current] = m.group(2)
        elif current is not None:
            props[current] += "\n" + raw
        elif raw.strip():
            current = "body"
            props["body"] = raw
    if fence is not None:
        body = "\n".join(lines[fence + 1:]).strip()
        if body:
            props["body"] = body
    return {k: v.strip() for k, v in props.items()}


def staged_files(staging_dir):
    if not os.path.isdir(staging_dir):
        return []
    out = []
    for name in sorted(os.listdir(staging_dir)):
        path = os.path.join(staging_dir, name)
        if not os.path.isfile(path) or name.startswith(".") or name.lower() == "readme.md":
            continue
        out.append((name, path))
    return out


def parse_staged(name, path):
    """Validate the filename id, parse the file's properties, and return
    (note_id, props). DB-free — uniqueness + commit are the Ingestor's job."""
    note_id = notelib.validate_id(id_from_filename(name))
    props = parse_props(open(path, encoding="utf-8").read())
    if props.get("id") and props["id"] != note_id:
        raise ValueError(
            f"id in file ({props['id']!r}) does not match filename id ({note_id!r})")
    props["id"] = note_id
    return note_id, props


def move_into(path, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(path))
    if os.path.exists(dest):
        os.remove(dest)
    shutil.move(path, dest)


def ingest_all(ing, files, embed=None, dry_run=False, on_record=None):
    """Commit each staged file in turn; return one record per file:

        {name, note_id, ok, title, embedded, begets, error}

    `begets` is (parent_id, mode) or None. `embedded` is None on a dry run, where nothing is
    embedded and nothing moves; otherwise each file lands in staging/processed/ or
    staging/failed/ before the next is tried, so a batch that dies halfway leaves the directory
    saying exactly how far it got. `on_record` is called with each record as it completes, which
    is what keeps the CLI's output streaming rather than arriving all at once at the end.

    Shared by the CLI below and the staging panel in scripts/ui.py, which renders these same
    records as JSON. The two have to agree about what a batch did, and the only way to be sure
    of that is for there to be one batch.
    """
    # Dry-run mirrors the real run's sorted-order commit: a parent target is valid if
    # it is already in the DB OR was an earlier file in this batch. Seed with the DB's ids, then
    # add each id as it validates — so intra-batch lineage no longer false-fails (I-2).
    would_exist = None
    if dry_run:
        would_exist = {r.get("id") for r in notelib.rows(
            ing.db.query("SELECT id FROM Note")) if r.get("id")}

    records = []
    for name, path in files:
        rec = {"name": name, "note_id": None, "ok": False, "title": None,
               "embedded": None, "begets": None, "error": None}
        try:
            note_id, props = parse_staged(name, path)
            rec["note_id"], rec["title"] = note_id, props.get("title")
            # Pull relationship keys out of the note fields.
            link = {k: props.pop(k, None) for k in LINK_KEYS}
            parent, mode = link["parent"], link["mode"]
            if dry_run:
                notelib.build_note(props)                      # validates shape + required body
                parent, mode = notelib.resolve_link(parent, mode)   # validates parent/mode pairing
                if note_id in would_exist:                     # DB id, or a duplicate earlier in the batch
                    raise notelib.DuplicateNote(note_id)
                if parent and parent not in would_exist:
                    raise ValueError(f"parent note {parent} does not exist "
                                     f"(not in the DB or earlier in this batch)")
                would_exist.add(note_id)                       # later files in the batch may reference it
                rec["begets"] = (parent, mode) if parent else None
            else:
                result = ing.commit(props, embed=embed, parent=parent, mode=mode)
                move_into(path, os.path.join(os.path.dirname(path), "processed"))
                rec["note_id"], rec["embedded"] = result.note_id, result.embedded
                rec["begets"] = result.begets
            rec["ok"] = True
        except (ValueError, notelib.ArcadeError) as e:
            rec["error"] = str(e)
            if not dry_run:
                move_into(path, os.path.join(os.path.dirname(path), "failed"))
        records.append(rec)
        if on_record:
            on_record(rec)
    return records


def main():
    p = argparse.ArgumentParser(
        prog="ingest-staging", description="Ingest id-named files from staging/ into the graph.")
    p.add_argument("--dir", default=DEFAULT_STAGING, help="staging directory (default: ./staging)")
    p.add_argument("--dry-run", action="store_true",
                   help="validate + parse only; do not embed, insert, or move files")
    p.add_argument("--embed", action="store_true",
                   help="embed inline now (blocks), overriding the async default")
    p.add_argument("--no-embed", action="store_true", help="never embed inline (default)")
    args = p.parse_args()

    # Default follows INDEXIA_EMBED_ON_COMMIT (async): the worker embeds later.
    embed = True if args.embed else (False if args.no_embed else None)

    files = staged_files(args.dir)
    if not files:
        print(f"[ingest-staging] nothing to ingest in {args.dir}")
        return

    ing = notelib.Ingestor()   # central commit path: embed-on-commit + Op(ADD_NOTE)

    def show(rec):
        title = f"  “{rec['title']}”" if rec.get("title") else ""
        if not rec["ok"]:
            print(f"  FAIL {rec['name']}: {rec['error']}")
        elif args.dry_run:
            print(f"  DRY OK   {rec['name']} -> Note {rec['note_id']}{title}")
        else:
            tag = "embedded" if rec["embedded"] else "pending-embed"
            rel = " ·begets" if rec["begets"] else ""
            print(f"  OK   {rec['name']} -> Note {rec['note_id']} [{tag}]{rel}{title}")

    records = ingest_all(ing, files, embed=embed, dry_run=args.dry_run, on_record=show)
    ok = sum(1 for r in records if r["ok"])
    fail = len(records) - ok
    pending = sum(1 for r in records if r["ok"] and r["embedded"] is False)

    verb = "would ingest" if args.dry_run else "ingested"
    print(f"[ingest-staging] {verb} {ok}, failed {fail} "
          f"({'dry run — nothing moved' if args.dry_run else 'processed/ and failed/ updated'})")
    if pending:
        print(f"[ingest-staging] {pending} note(s) queued for embedding — the embed worker "
              f"(scripts/embed-worker.sh) embeds them, or run scripts/embed-backfill.sh")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
