#!/usr/bin/env python3
"""backfill-link-dates — date the BINDS edges that predate `BINDS.created_at`.

A one-time schema migration, kept because it is also the repair path after restoring an older
backup. `LinkManager.suggest` stamps every new edge, but edges staged before the property
existed read null — and the expiry sweep deliberately never touches an undated edge, so without
this they would be immortal.

The Op log already holds each edge's birth instant: `Op(SUGGEST_LINK)` ids *are* timestamps
(spec §4, §11.2) and their payload carries the endpoints. This joins suggested/ratified edges to
their SUGGEST_LINK Op by endpoint pair and writes `created_at` from the Op id. Where a pair was
suggested more than once (suggested → rejected → suggested again) the *latest* Op is the birth of
the edge standing today, so that is the one used.

Idempotent and safe to re-run: only edges with a null created_at are touched, and an edge with no
matching Op is reported and left alone rather than guessed at. Logs Op(BACKFILL_LINK_DATES).
Run through the wrapper (scripts/backfill-link-dates.sh).
"""
import argparse
import json
import sys

import notelib


def undated_edges(db):
    """(a, b, status) for every BINDS edge with no created_at."""
    return [(r.get("a"), r.get("b"), r.get("status")) for r in notelib.rows(db.query(
        "SELECT outV().id AS a, inV().id AS b, status FROM BINDS WHERE created_at IS NULL"))
        if r.get("a") and r.get("b")]


def suggest_op_dates(db):
    """{(a, b): op_id} from the Op log, keeping the latest SUGGEST_LINK per endpoint pair."""
    latest = {}
    for r in notelib.rows(db.query(
            "SELECT id, payload FROM Op WHERE rule = 'SUGGEST_LINK' ORDER BY id")):
        op_id, raw = r.get("id"), r.get("payload")
        if not (op_id and raw):
            continue
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            continue
        a, b = payload.get("a"), payload.get("b")
        if a and b:
            latest[(a, b)] = op_id          # ordered by id, so the last write is the latest
    return latest


def main():
    p = argparse.ArgumentParser(
        prog="backfill-link-dates",
        description="Date pre-existing BINDS edges from the Op log (one-time migration).")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="report what would be dated, write nothing")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    db = notelib.Arcade()
    try:
        edges = undated_edges(db)
        dates = suggest_op_dates(db)
        matched = [(a, b, dates[(a, b)]) for a, b, _s in edges if (a, b) in dates]
        unmatched = [(a, b, s) for a, b, s in edges if (a, b) not in dates]

        if matched and not args.dry_run:
            op_id = notelib.unique_id(db, notelib.format_id(notelib.now_utc()), "Op")
            with db.transaction():
                for a, b, born in matched:
                    db.command(
                        "UPDATE BINDS SET created_at = :at "
                        "WHERE outV().id = :a AND inV().id = :b AND created_at IS NULL",
                        {"at": notelib.id_to_created_at(born), "a": a, "b": b})
                notelib.insert_op(db, op_id, "BACKFILL_LINK_DATES",
                                  {"dated": len(matched), "undatable": len(unmatched)})
        else:
            op_id = None
    except notelib.ArcadeError as e:
        sys.exit(f"[backfill-link-dates] failed: {e}\n  SQL: {e.sql}")

    if args.json:
        print(json.dumps({"undated": len(edges), "dated": len(matched),
                          "undatable": [{"a": a, "b": b, "status": s} for a, b, s in unmatched],
                          "dry_run": args.dry_run, "op_id": op_id}, indent=2))
        return
    verb = "would date" if args.dry_run else "dated"
    print(f"[backfill-link-dates] {len(edges)} undated edge(s) · {verb} {len(matched)} from the "
          f"Op log" + (f"  ·  Op {op_id}" if op_id else ""))
    if unmatched:
        print(f"[backfill-link-dates] {len(unmatched)} edge(s) have no SUGGEST_LINK Op and stay "
              f"undated (never swept):")
        for a, b, s in unmatched[:10]:
            print(f"    [{s}]  {a} → {b}")
        if len(unmatched) > 10:
            print(f"    … and {len(unmatched) - 10} more")


if __name__ == "__main__":
    main()
