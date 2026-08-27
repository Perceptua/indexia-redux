#!/usr/bin/env python3
"""analytics — read-only reports over the Indexia graph (spec §13).

    analytics.sh fitness       [--as-of TS] [--limit N] [--json]
    analytics.sh debt          [--as-of TS] [--limit N] [--min-descendants N] [--json]
    analytics.sh criticality   [--as-of TS] [--json]
    analytics.sh communities   [--as-of TS] [--members] [--json]
    analytics.sh autocatalysis [--as-of TS] [--json]      # communities, autocatalytic only
    analytics.sh visited       [--as-of TS] [--limit N] [--ascending] [--json]
    analytics.sh walks         [--status active|saved] [--limit N] [--json]
    analytics.sh walk    <id>  [--json]
    analytics.sh replay  <id>  [--k N] [--depth N] [--no-cache] [--json]

Every one of these READS AND NEVER WRITES — no property, no edge, no Op. Run them as often as
you like; the corpus cannot tell the difference.

`--as-of <note-id-shaped timestamp>` recomputes against the graph as it stood at that instant,
using created_at on Note, BEGETS and BINDS. Its one limit: BINDS.status has no history, so an
as-of view uses each surviving edge's current status (see analytics/common.py). `walks`, `walk`
and `replay` do not take it — a walk is an Op sequence, and a replay is by definition a question
about the corpus *now*.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analytics import autocatalysis, common, criticality, debt, fitness, walks  # noqa: E402

notelib = common.notelib


def build_parser():
    p = argparse.ArgumentParser(
        prog="analytics", description="Read-only reports over the Indexia graph (spec §13).")
    sub = p.add_subparsers(dest="report", required=True)

    def with_as_of(sp):
        sp.add_argument("--as-of", dest="as_of", metavar="TS",
                        help="recompute against the graph as of this timestamp (note-id shape)")
        sp.add_argument("--json", action="store_true")
        return sp

    f = with_as_of(sub.add_parser("fitness", help="note standing, computed from the graph"))
    f.add_argument("--limit", type=int, help="show only the top N")

    d = with_as_of(sub.add_parser(
        "debt", help="load-bearing notes you have stopped attending to (move 7)"))
    d.add_argument("--limit", type=int, default=debt.K,
                   help=f"prompts to show (default {debt.K})")
    d.add_argument("--min-descendants", type=int, default=debt.MIN_DESCENDANTS,
                   dest="min_descendants",
                   help=f"skip notes below this many descendants (default {debt.MIN_DESCENDANTS})")

    with_as_of(sub.add_parser("criticality", help="is the corpus at the edge of chaos?"))

    c = with_as_of(sub.add_parser("communities", help="detected communities + diagnostics"))
    c.add_argument("--members", action="store_true", help="list every member, not just the hub")
    c.add_argument("--min-size", type=int, dest="min_size",
                   help=f"minimum community size (default {notelib.COMMUNITY_MIN_SIZE})")

    a = with_as_of(sub.add_parser("autocatalysis",
                                  help="only the communities that cycle under catalysis"))
    a.add_argument("--members", action="store_true")
    a.add_argument("--min-size", type=int, dest="min_size")

    v = with_as_of(sub.add_parser("visited", help="human attention per note"))
    v.add_argument("--limit", type=int)
    v.add_argument("--ascending", action="store_true",
                   help="least-visited first — the notes you never go back to")

    w = sub.add_parser("walks", help="recorded walks, reconstructed from the Op log")
    w.add_argument("--status", choices=("active", "saved"))
    w.add_argument("--limit", type=int, default=50)
    w.add_argument("--json", action="store_true")

    s = sub.add_parser("walk", help="one walk in full")
    s.add_argument("walk_id")
    s.add_argument("--json", action="store_true")

    r = sub.add_parser("replay", help="re-run a saved walk against the corpus as it is now")
    r.add_argument("walk_id")
    r.add_argument("--k", type=int, default=5)
    r.add_argument("--depth", type=int, default=2)
    r.add_argument("--no-cache", action="store_true", dest="no_cache",
                   help="query the vector index live instead of the nightly k-NN cache")
    r.add_argument("--json", action="store_true")
    return p


def main():
    args = build_parser().parse_args()
    db = notelib.Arcade()
    as_of = getattr(args, "as_of", None)

    try:
        if args.report in ("walks", "walk", "replay"):
            out, payload = _walk_report(db, args)
        else:
            corpus = common.Corpus(db, as_of=as_of)
            out, payload = _graph_report(corpus, args, as_of)
    except (ValueError, notelib.ArcadeError) as e:
        sys.exit(f"[analytics] {e}")

    print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json else out)


def _graph_report(corpus, args, as_of):
    if args.report == "fitness":
        rows = fitness.report(corpus, limit=args.limit)
        return fitness.render(rows, as_of=as_of), rows
    if args.report == "debt":
        rows = debt.report(corpus, min_descendants=args.min_descendants, limit=args.limit)
        return debt.render(rows, corpus=corpus, as_of=as_of), rows
    if args.report == "criticality":
        rep = criticality.report(corpus)
        return criticality.render(rep, as_of=as_of), rep
    if args.report in ("communities", "autocatalysis"):
        clusters = autocatalysis.report(corpus, min_size=args.min_size)
        detected = len(clusters)
        if args.report == "autocatalysis":
            clusters = [c for c in clusters if c["autocatalytic"]]
        return (autocatalysis.render(clusters, corpus, as_of=as_of, members=args.members,
                                     detected=detected),
                clusters)
    if args.report == "visited":
        rows = walks.visits(corpus, limit=args.limit, ascending=args.ascending)
        return walks.render_visits(rows, as_of=as_of), rows
    raise ValueError(f"unknown report {args.report}")


def _walk_report(db, args):
    if args.report == "walks":
        found = walks.listing(db, status=args.status, limit=args.limit)
        return walks.render_list(found), found
    corpus = common.Corpus(db)                 # for labels only
    if args.report == "walk":
        walk = walks.show(db, args.walk_id)
        if not walk:
            raise ValueError(f"no walk {args.walk_id} (list them: analytics.sh walks)")
        return walks.render_show(walk, corpus), walk
    result = walks.replay(db, args.walk_id, k=args.k, depth=args.depth,
                          use_cache=not args.no_cache)
    return walks.render_replay(result, corpus), result


if __name__ == "__main__":
    main()
