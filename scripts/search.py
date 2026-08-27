#!/usr/bin/env python3
"""Find notes in the Indexia graph — the read side of the difference-engine (spec §8).

Two ways to search (kept separate in v1):
  * semantic (by meaning), over the vector layer —
      -q / --query "text"   embed the text, return the nearest notes
      --like <id>           return notes nearest an existing note (needs it embedded)
  * lexical / metadata, over the metadata + temporal layers (AND-combined) —
      --id-prefix / --text / --title / --body / --source / --author
      / --since / --until
  With no arguments at all, lists the most recent notes (a plain browse).

Run through the wrapper (scripts/search.sh) so BASE_URL / DB / ARCADEDB_ROOT_PASSWORD
and the embedder config are loaded from docker/.env.

`--since` / `--until` are both inclusive of the day named, so `--since D --until D`
returns everything written on day D.

Examples:
  scripts/search.sh -q "atomic notes stand on their own" -k 5
  scripts/search.sh --like 20260722T101500000Z
  scripts/search.sh --title Folgezettel --author human --since 2026-07-01
  scripts/search.sh --since 2026-07-19 --until 2026-07-19     # one whole day
  scripts/search.sh --inhibited                # notes a ratified BINDS{inhibits} points at
"""
import argparse
import json
import sys

import notelib

LEXICAL_FLAGS = ("id_prefix", "text", "title", "body", "source", "author",
                 "since", "until", "inhibited")


def run_search(db, args):
    """Dispatch to semantic or lexical search. Returns (mode, header, hits).
    Raises ValueError (bad input) or notelib.EmbedderError (semantic, no embedder)."""
    if args.query and args.like:
        raise ValueError("give either --query or --like, not both")
    semantic = bool(args.query) or bool(args.like)
    lexical = any(getattr(args, f) for f in LEXICAL_FLAGS)
    if semantic and lexical:
        raise ValueError("choose a semantic search (-q/--like) or lexical filters "
                         "(--text/--title/--author/…), not both (v1 keeps them separate)")

    if semantic:
        k = args.limit if args.limit is not None else 10
        if args.like:
            notelib.validate_id(args.like)
            vec = notelib.note_embedding(db, args.like)
            if vec is None:
                if not notelib.note_exists(db, args.like):
                    raise ValueError(f"no note with id {args.like}")
                raise ValueError(
                    f"note {args.like} has no embedding yet (pending-embed) — try again once "
                    f"the embed worker catches up (scripts/embed-worker.sh status), or use -q")
            hits = notelib.semantic_search(db, vec, k=k, ef=args.ef, exclude_ids=[args.like])
            return "semantic", f"notes most like {args.like}", hits
        vec = notelib.embed_query(args.query)
        hits = notelib.semantic_search(db, vec, k=k, ef=args.ef)
        return "semantic", f'notes semantically nearest "{args.query}"', hits

    # lexical / browse
    k = args.limit if args.limit is not None else 25
    hits = notelib.lexical_search(
        db, id_prefix=args.id_prefix, text=args.text, title=args.title, body=args.body,
        source=args.source, author=args.author, since=args.since,
        until=args.until, limit=(None if args.inhibited else k))
    if args.inhibited:
        # Being inhibited is derived from the graph, not a note field (§6), so it filters the
        # metadata result rather than joining in SQL — the corpus is small and this stays honest.
        inhibited = notelib.inhibited_note_ids(db)
        hits = [h for h in hits if h.get("id") in inhibited][:k]
    if lexical:
        parts = [f"{f.replace('_', '-')}={getattr(args, f)!r}"
                 for f in LEXICAL_FLAGS if getattr(args, f)]
        return "lexical", "notes matching " + ", ".join(parts), hits
    return "lexical", "most recent notes", hits


def render(mode, header, hits):
    if not hits:
        return f"[search] no matches — {header}"
    lines = [f"[search] {header} — {len(hits)} result(s):", ""]
    for h in hits:
        bits = [h["id"]]
        if mode == "semantic":
            bits.append(f"{h['score']:.3f}")
        bits.append(h.get("title") or "(untitled)")
        lines.append("  " + "  ·  ".join(bits))
        lines.append(f"      {h['snippet']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def main():
    p = argparse.ArgumentParser(
        prog="search", description="Find notes in the Indexia graph (semantic or lexical).")
    g = p.add_argument_group("semantic — by meaning (vector layer)")
    g.add_argument("-q", "--query", help="free text; returns the notes nearest in meaning")
    g.add_argument("--like", metavar="ID", help="return notes nearest an existing note")
    g.add_argument("--ef", type=int, default=100, help="ANN efSearch breadth (default 100)")
    g2 = p.add_argument_group("lexical / metadata — exact-ish filters, AND-combined")
    g2.add_argument("--id-prefix", dest="id_prefix",
                    help="ids starting with this (a day like 20260722 selects that day)")
    g2.add_argument("--text", help="case-insensitive substring in body or title")
    g2.add_argument("--title", help="case-insensitive substring in the title")
    g2.add_argument("--body", help="case-insensitive substring in the body alone")
    g2.add_argument("--source", help="case-insensitive substring in source_ref")
    g2.add_argument("--author", help="human | llm_assisted")
    g2.add_argument("--inhibited", action="store_true",
                    help="only notes corrected by a ratified BINDS{inhibits} (derived, §6)")
    g2.add_argument("--since", help="notes on/after this UTC day (YYYY-MM-DD, inclusive)")
    g2.add_argument("--until", help="notes on/before this UTC day (YYYY-MM-DD, inclusive — "
                                    "--since D --until D is all of day D)")
    p.add_argument("-k", "--limit", type=int, default=None,
                   help="max results (default 10 semantic / 25 lexical)")
    p.add_argument("--json", action="store_true", help="print raw result rows as JSON")
    args = p.parse_args()

    db = notelib.Arcade()
    try:
        mode, header, hits = run_search(db, args)
    except notelib.EmbedderError as e:
        sys.exit(f"[search] semantic search unavailable: {e}")
    except ValueError as e:
        sys.exit(f"[search] {e}")
    except notelib.ArcadeError as e:
        sys.exit(f"[search] query failed: {e}\n  SQL: {e.sql}")

    if args.json:
        print(json.dumps(hits, indent=2, ensure_ascii=False))
    else:
        print(render(mode, header, hits))


if __name__ == "__main__":
    main()
