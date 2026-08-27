#!/usr/bin/env python3
"""Manage associative BINDS edges — the ratification flow (spec §7, §12.3).

The machine SUGGESTs a bind; the human RATIFIes or REJECTs it. Links are emergent and
human-owned; suggesting one never ratifies it.

A bind's `mode` is the corpus's only semantics (§7), and it is optional:

  (untyped)   you recognize a relation but have not said which way it pushes — inert
  catalyzes   A feeds B across a generational gap: excitatory, credits B's lineage
  inhibits    A corrects B: the inhibitory signal that replaced SUPERSEDES in v0.7.0 (§6)

The machine only ever proposes untyped binds — it can spot affinity but not judge. Assigning
a mode is a human act, done at ratification (or later, with `retype`). Ratifying a *typed*
bind fires the signed activation wave back along the target's lineage (§12.5).

Subcommands:
  suggest A B [--rationale "…"] [--mode M]   stage BINDS{suggested} A→B   (SUGGEST_LINK ●)
  ratify  A B [--mode M] [--rationale "…"]   ratify it, setting the mode  (RATIFY_LINK ○)
  retype  A B --mode M                       change an existing bind's mode (RETYPE_LINK ○)
  reject  A B                                delete it (un-link)          (REJECT_LINK)
  list    [--status suggested|ratified] [--mode catalyzes|inhibits|none]

ratify/retype/reject accept the endpoints in either order — the edge is resolved whichever
way round it was stored, and the stored direction is reported back (it is the claim).

Run through the wrapper (scripts/link.sh) so BASE_URL / DB / ARCADEDB_ROOT_PASSWORD
are loaded from docker/.env.

Examples:
  scripts/link.sh suggest 20260722T101500000Z 20260723T115732830Z --rationale "both about emergence"
  scripts/link.sh list --status suggested
  scripts/link.sh ratify 20260722T101500000Z 20260723T115732830Z --mode catalyzes
  scripts/link.sh suggest <corrected> <old> --mode inhibits --rationale "conflated two transitions"
"""
import argparse
import json
import sys

import notelib

MODE_HELP = "catalyzes | inhibits | none (untyped, the default for a machine proposal)"


def _mode_label(mode):
    return mode or "untyped"


def render_list(edges, status, mode_filter):
    what = "BINDS edge"
    filters = []
    if status:
        filters.append(f"status={status}")
    if mode_filter is not notelib._UNSET:
        filters.append(f"mode={_mode_label(mode_filter)}")
    suffix = (" (" + ", ".join(filters) + ")") if filters else ""
    if not edges:
        return f"[link] no {what}s{suffix}"
    lines = [f"[link] {len(edges)} {what}(s){suffix}:", ""]
    for e in edges:
        at = e.get("a_title") or "(untitled)"
        bt = e.get("b_title") or "(untitled)"
        days = notelib.age_in_days(e.get("created_at"))
        age = f"  ·  {days}d old" if days is not None else "  ·  undated"
        why = f"\n        {e['rationale']}" if e.get("rationale") else ""
        lines.append(f"  [{e.get('status')}/{_mode_label(e.get('mode'))}]  "
                     f"{e.get('a')} ({at})  →  {e.get('b')} ({bt}){age}{why}")
    if status == "suggested":
        lines += ["", f"[link] suggestions are swept after "
                      f"{notelib.SUGGESTION_MAX_AGE_DAYS} days unless the queue is "
                      f"<= {notelib.SUGGESTION_KEEP_MIN} long (scripts/link-expiry.sh)"]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(prog="link", description="BINDS ratification flow (spec §7).")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("suggest", help="stage a suggested BINDS A→B")
    ps.add_argument("a", help="source note id")
    ps.add_argument("b", help="target note id")
    ps.add_argument("--rationale", help="why these two relate (stored on the edge)")
    ps.add_argument("--mode", help=MODE_HELP)

    pr = sub.add_parser("ratify", help="ratify a BINDS between A and B")
    pr.add_argument("a", help="one endpoint id")
    pr.add_argument("b", help="the other endpoint id")
    pr.add_argument("--mode", help=MODE_HELP + " — assign it now if you have a verdict")
    pr.add_argument("--rationale", help="record/replace why (stored on the edge)")

    pt = sub.add_parser("retype", help="change an existing bind's mode")
    pt.add_argument("a", help="one endpoint id")
    pt.add_argument("b", help="the other endpoint id")
    pt.add_argument("--mode", required=True, help=MODE_HELP)

    pj = sub.add_parser("reject", help="delete a BINDS between A and B (un-link)")
    pj.add_argument("a", help="one endpoint id")
    pj.add_argument("b", help="the other endpoint id")

    pl = sub.add_parser("list", help="list BINDS edges")
    pl.add_argument("--status", help="suggested | ratified")
    pl.add_argument("--mode", help=MODE_HELP)
    pl.add_argument("-k", "--limit", type=int, default=100, help="max edges (default 100)")

    for q in (ps, pr, pt, pj, pl):
        q.add_argument("--json", action="store_true", help="print the result as JSON")
    args = p.parse_args()

    lm = notelib.LinkManager()
    try:
        if args.cmd == "list":
            mode = notelib._UNSET if args.mode is None else args.mode
            edges = lm.list(status=args.status, mode=mode, limit=args.limit)
            print(json.dumps(edges, indent=2, ensure_ascii=False) if args.json
                  else render_list(edges, args.status, mode))
            return
        if args.cmd == "suggest":
            result = lm.suggest(args.a, args.b, rationale=args.rationale, mode=args.mode)
        elif args.cmd == "ratify":
            mode = notelib._UNSET if args.mode is None else args.mode
            result = lm.ratify(args.a, args.b, mode=mode, rationale=args.rationale)
        elif args.cmd == "retype":
            result = lm.retype(args.a, args.b, args.mode)
        else:
            result = lm.reject(args.a, args.b)
    except notelib.LinkExists as e:
        sys.exit(f"[link] {e}")
    except ValueError as e:
        sys.exit(f"[link] {e}")
    except notelib.ArcadeError as e:
        sys.exit(f"[link] failed: {e}\n  SQL: {e.sql}")

    if args.json:
        print(json.dumps(result._asdict(), indent=2, ensure_ascii=False))
    else:
        tail = f"  ·  Op {result.op_id}" if result.op_id else "  (no change — no-op)"
        print(f"[link] {result.rule}  {result.a} → {result.b}  ·  "
              f"{result.status}/{_mode_label(result.mode)}{tail}")


if __name__ == "__main__":
    main()
