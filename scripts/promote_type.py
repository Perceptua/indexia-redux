#!/usr/bin/env python3
"""promote-type — register a new vertex/edge type (schema growth, spec §3.3, §12.3 PROMOTE_TYPE).

Kauffman's unprestatability made operational: when a pattern recurs enough to deserve its own
first-class type, promote it. Idempotent (safe to re-run); logs Op(PROMOTE_TYPE). Use this for
ad-hoc growth; fold the type into ddl/schema.sql once it's permanent.

Run through the wrapper (scripts/promote-type.sh).

Examples:
  scripts/promote-type.sh vertex Source --property "url:STRING" --property "retrieved_at:DATETIME"
  scripts/promote-type.sh edge CITES --property "locator:STRING"
"""
import argparse
import json
import sys

import notelib


def main():
    p = argparse.ArgumentParser(
        prog="promote-type", description="Register a new vertex/edge type (spec §12.3 PROMOTE_TYPE).")
    p.add_argument("kind", choices=["vertex", "edge"], help="vertex | edge")
    p.add_argument("name", help="the new type name (letters/digits/underscore, letter first)")
    p.add_argument("--property", action="append", default=[], dest="properties", metavar="NAME:TYPE",
                   help="a property, e.g. --property title:STRING (repeatable; type defaults to STRING)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    props = []
    for spec in args.properties:
        pname, _, ptype = spec.partition(":")
        props.append((pname.strip(), (ptype or "STRING").strip()))

    db = notelib.Arcade()
    try:
        res = notelib.promote_type(db, args.kind, args.name, props)
    except ValueError as e:
        sys.exit(f"[promote-type] {e}")
    except notelib.ArcadeError as e:
        sys.exit(f"[promote-type] failed: {e}\n  SQL: {e.sql}")

    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        shown = ", ".join(f"{pp['name']}:{pp['type']}" for pp in res["properties"]) or "(no new properties)"
        print(f"[promote-type] {res['kind']} type {res['name']}  ·  Op {res['op_id']}  ·  {shown}")


if __name__ == "__main__":
    main()
