#!/usr/bin/env python3
"""Record walks — reading/thinking sessions through the graph (spec §11.1, §13).

A walk is the human moving through the corpus: the notes you VISIT, and the notes it PRODUCES.
Recording one is an **operation**, and it writes exactly two things: an Op per event, and +1 to
each visited note's `visited` counter — once per walk, however many times you come back to a note
inside it.

The trail is the whole of what a run held; there is no separately-declared working set since
v0.8.3 (notelib.WALK_RULES says what it was and why it went), and `replay` re-seeds from the
trail itself.

There is no Trace vertex. The walk *is* its Op sequence, which is why reading a walk back,
listing walks and replaying one all live on the analytics side:

    scripts/analytics.sh walk   <id>     the trail, and what it produced
    scripts/analytics.sh walks           every walk, newest first
    scripts/analytics.sh replay <id>     re-provoke from a saved walk's trail
    scripts/analytics.sh visited         attention per note

Subcommands here are the ones that change something:
  start  --seed ID [--intent "…"]   open a walk and visit the seed          (START_WALK)
  visit  WALK NOTE                  append NOTE to the trail                (VISIT)
  produce WALK NOTE                 record a note the walk authored         (PRODUCE)
  save   WALK                       close it; saved walks replay and fork   (SAVE_WALK)
  fork   WALK                       open a new walk from this one's seed    (FORK_WALK)
  delete WALK                       retire a walk (tombstone + give back)   (DELETE_WALK)

Run through the wrapper (scripts/walk.sh) so BASE_URL / DB / ARCADEDB_ROOT_PASSWORD load.

Examples:
  scripts/walk.sh start --seed 20260722T101500000Z --intent "how does emergence recur?"
  scripts/walk.sh start --seed 20260722T101500000Z     # intent defaults to naming the seed
  scripts/walk.sh visit  <walk> 20260723T115732830Z
  scripts/walk.sh save   <walk>
  scripts/analytics.sh replay <walk>
"""
import argparse
import json
import sys

import notelib


def main():
    p = argparse.ArgumentParser(
        prog="walk",
        description="Record walks through the graph (spec §13). Read them back with analytics.sh.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pstart = sub.add_parser("start", help="open a walk at a seed note")
    pstart.add_argument("--seed", required=True, help="the note the walk begins at")
    pstart.add_argument("--intent", help="the goal for this run (top-down control, §11.3); "
                                         "defaults to naming the seed")

    pv = sub.add_parser("visit", help="append a note to the trail")
    pv.add_argument("walk")
    pv.add_argument("note")

    ppr = sub.add_parser("produce", help="record a note the walk authored")
    ppr.add_argument("walk")
    ppr.add_argument("note")

    psv = sub.add_parser("save", help="close the walk; saved walks replay and fork")
    psv.add_argument("walk")

    pfk = sub.add_parser("fork", help="open a new walk from this one's seed and intent")
    pfk.add_argument("walk")

    pdel = sub.add_parser("delete", help="retire a walk and give back the visits it counted")
    pdel.add_argument("walk")

    for q in (pstart, pv, ppr, psv, pfk, pdel):
        q.add_argument("--json", action="store_true", help="print the result as JSON")
    args = p.parse_args()

    wm = notelib.WalkManager(notelib.Arcade())
    try:
        if args.cmd == "start":
            res = wm.start(args.intent, args.seed)
        elif args.cmd == "visit":
            res = wm.visit(args.walk, args.note)
        elif args.cmd == "produce":
            res = wm.produce(args.walk, args.note)
        elif args.cmd == "save":
            res = wm.save(args.walk)
        elif args.cmd == "delete":
            res = wm.delete(args.walk)
        else:  # fork
            res = wm.fork(args.walk)
    except ValueError as e:
        sys.exit(f"[walk] {e}")
    except notelib.ArcadeError as e:
        sys.exit(f"[walk] failed: {e}\n  SQL: {e.sql}")

    if args.json:
        print(json.dumps(res._asdict(), indent=2, ensure_ascii=False))
    else:
        note = f"  {res.note_id}" if res.note_id else ""
        tail = f"  ·  Op {res.op_id}" if res.op_id else ""
        print(f"[walk] {res.rule}  {res.walk_id}{note}{tail}")
        if args.cmd == "start":
            print(f"[walk] record with: scripts/walk.sh visit {res.walk_id} <note-id>")


if __name__ == "__main__":
    main()
