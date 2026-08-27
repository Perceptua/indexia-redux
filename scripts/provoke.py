#!/usr/bin/env python3
"""Provocation move 1 — "semantically near, graph-far" (spec §8.1, §8.3).

For a seed note, surface notes close in meaning but with no short BINDS/BEGETS
path to it — exactly where an emergent associative link wants to form. The machine
proposes, the human disposes: with --stage, each surfaced candidate is written as a
SUGGEST_LINK (status:suggested) for you to ratify later with link.sh. Without --stage
it only previews; it never ratifies (spec §8.2).

Neighbours come from the nightly k-NN cache, so this returns in seconds. A seed embedded since
the last cache rebuild is always served live instead (it has no cached row), but for an older
seed a candidate written since last night's rebuild can be missing — pass --no-cache to query
the vector index directly, at the cost of a full ANN rebuild if anything was embedded since.

Run through the wrapper (scripts/provoke.sh).

Examples:
  scripts/provoke.sh --seed 20260722T101500000Z            # preview candidates
  scripts/provoke.sh --seed 20260722T101500000Z --stage    # stage them as suggested links
"""
import argparse
import json
import sys

import notelib


def render(seed, cands, staged, stage):
    if not cands:
        return (f"[provoke] no move-1 candidates for {seed} — either nothing is "
                f"semantically near, or everything near is already graph-connected")
    lines = [f"[provoke] move 1 for {seed} — {len(cands)} candidate(s) "
             f"(semantically near, graph-far):", ""]
    for c in cands:
        mark = "   ✓ staged" if c["id"] in staged else ""
        lines.append(f"  {c['id']}  ·  {c['score']:.3f}  ·  {c.get('title') or '(untitled)'}{mark}")
        lines.append(f"      {c['snippet']}")
    lines.append("")
    if stage and staged:
        lines.append(f"[provoke] staged {len(staged)} SUGGEST_LINK edge(s) — "
                     f"ratify with:  link.sh ratify {seed} <id>")
    elif not stage:
        lines.append("[provoke] preview only — re-run with --stage to propose these as suggested links")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(
        prog="provoke", description="Move 1: semantically near, graph-far (spec §8.1).")
    p.add_argument("--seed", required=True, help="the note to provoke from")
    p.add_argument("-k", "--limit", type=int, default=5, help="candidates to surface (default 5)")
    p.add_argument("--depth", type=int, default=2,
                   help="graph-far radius over BINDS/BEGETS (default 2)")
    p.add_argument("--ef", type=int, default=100, help="ANN efSearch breadth (default 100)")
    p.add_argument("--min-score", dest="min_score", type=float, default=0.0,
                   help="drop candidates below this cosine score")
    p.add_argument("--stage", action="store_true",
                   help="stage each candidate as a SUGGEST_LINK (default: preview only)")
    p.add_argument("--no-cache", action="store_true", dest="no_cache",
                   help="query the vector index live instead of the nightly k-NN cache "
                        "(freshest, but pays a full ANN rebuild if anything was embedded since)")
    p.add_argument("--json", action="store_true", help="print seed/candidates/staged as JSON")
    args = p.parse_args()

    db = notelib.Arcade()
    try:
        notelib.validate_id(args.seed)
        if not notelib.note_exists(db, args.seed):
            sys.exit(f"[provoke] no note with id {args.seed}")
        cands = notelib.move1_candidates(db, args.seed, k=args.limit, ef=args.ef,
                                        depth=args.depth, use_cache=not args.no_cache)
    except notelib.EmbedderError as e:
        sys.exit(f"[provoke] {e}")
    except ValueError as e:
        sys.exit(f"[provoke] {e}")
    except notelib.ArcadeError as e:
        sys.exit(f"[provoke] query failed: {e}\n  SQL: {e.sql}")

    cands = [c for c in cands if c["score"] >= args.min_score]

    staged = []
    if args.stage and cands:
        lm = notelib.LinkManager(db)
        for c in cands:
            rationale = (f"move-1: semantically near (score={c['score']:.3f}), "
                         f"no <= {args.depth}-hop BINDS/BEGETS path to the seed")
            try:
                lm.suggest(args.seed, c["id"], rationale=rationale)
                staged.append(c["id"])
            except notelib.LinkExists:
                pass   # already linked/suggested since the candidate query — skip

    if args.json:
        print(json.dumps({"seed": args.seed, "candidates": cands, "staged": staged},
                         indent=2, ensure_ascii=False))
    else:
        print(render(args.seed, cands, staged, args.stage))


if __name__ == "__main__":
    main()
