#!/usr/bin/env python3
"""link-expiry — weekly expiry sweep over the suggestion queue (spec §7, §8.2, §12.6).

The provocation digest proposes faster than any human ratifies: one measured run left 90
suggested BINDS edges standing against 18 ratified. An unbounded queue quietly erodes the
rule that the human ratifies everything — what nobody can read, nobody decides. So suggestions
are mortal: unratified for 30 days is an implicit decline, and the sweep clears them.

Ratified edges are never touched (they are corpus — only a *proposal* may be deleted, §2.4),
nothing is deleted at all while the queue is <= 10 edges long (a short list is a to-do list, not
a backlog), and an edge with no `created_at` is counted but never swept — those predate the
property, and the sweep only ever deletes what it knows is stale.

One Op(EXPIRE_LINKS) records the sweep. Run through the wrapper (scripts/link-expiry.sh);
meant to run weekly.
"""
import argparse
import json
import sys

import notelib


def main():
    p = argparse.ArgumentParser(
        prog="link-expiry", description="Expire stale suggested links (spec §7, §12.6).")
    p.add_argument("--max-age-days", type=int, default=notelib.SUGGESTION_MAX_AGE_DAYS,
                   dest="max_age_days",
                   help=f"expire suggestions older than this many days "
                        f"(default {notelib.SUGGESTION_MAX_AGE_DAYS})")
    p.add_argument("--keep-min", type=int, default=notelib.SUGGESTION_KEEP_MIN, dest="keep_min",
                   help=f"never sweep while the queue is this short or shorter "
                        f"(default {notelib.SUGGESTION_KEEP_MIN})")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="report what would be expired, delete nothing")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    db = notelib.Arcade()
    try:
        res = notelib.expire_suggested_links(db, max_age_days=args.max_age_days,
                                             keep_min=args.keep_min, dry_run=args.dry_run)
    except notelib.ArcadeError as e:
        sys.exit(f"[link-expiry] failed: {e}\n  SQL: {e.sql}")

    if args.json:
        print(json.dumps(res, indent=2))
        return

    if res["skipped_small_queue"]:
        print(f"[link-expiry] {res['total_suggested']} suggestion(s) queued — at or under the "
              f"keep-min of {args.keep_min}, nothing swept  ·  no Op (nothing changed)")
        return
    verb = "would expire" if res["dry_run"] else "expired"
    undated = f" · {res['undated']} undated (never swept)" if res["undated"] else ""
    # A sweep that finds nothing stale writes no Op, by the same rule the analytics layer obeys:
    # anything that appends no Op changed no state. That makes this line the only record the run
    # happened, so it says which of the two it is instead of leaving a silence to interpret.
    op = (f"  ·  Op {res['op_id']}" if res["op_id"] else
          "  ·  no Op (dry run)" if res["dry_run"] else "  ·  no Op (nothing changed)")
    print(f"[link-expiry] {verb} {res['expired']} of {res['total_suggested']} suggestion(s) "
          f"older than {res['cutoff']}{undated}{op}")
    for a, b in res["pairs"][:10]:
        print(f"    {a} → {b}")
    if len(res["pairs"]) > 10:
        print(f"    … and {len(res['pairs']) - 10} more")


if __name__ == "__main__":
    main()
