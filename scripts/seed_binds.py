#!/usr/bin/env python3
"""seed-binds — replay an associative layer from a TSV manifest (spec §7).

Ingestion only ever creates BEGETS (§3.2), so a corpus rebuilt from `staging/` has lineage but
no binds. This replays them, through the real LinkManager, so `created_at` and the Op log are
genuine rather than back-dated fictions.

Manifest format — tab-separated, `#` comments and blank lines ignored:

    a <TAB> b <TAB> mode <TAB> rationale

`mode` may be empty (untyped), `catalyzes`, or `inhibits`. Every row is suggested and then
ratified, because a manifest is a record of decisions already made; use --suggest-only to
stage them for a fresh pass of human judgement instead.

Rows whose bind already exists are skipped, so re-running is safe.

Keep manifests in `staging/manifests/` — `ingest-staging` scans `staging/` itself and would try
to read a stray manifest as a note.

Run through the wrapper (scripts/seed-binds.sh).

Examples:
  scripts/seed-binds.sh staging/manifests/binds.tsv
  scripts/seed-binds.sh staging/manifests/binds.tsv --dry-run
"""
import argparse
import json
import sys

import notelib


def parse_manifest(path):
    """[(a, b, mode, rationale)] from a TSV manifest. Raises ValueError on a malformed row."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                raise ValueError(f"{path}:{lineno}: need at least 'a<TAB>b', got {line!r}")
            a, b = parts[0].strip(), parts[1].strip()
            mode = parts[2].strip() if len(parts) > 2 else ""
            rationale = parts[3].strip() if len(parts) > 3 else ""
            out.append((a, b, notelib.normalize_bind_mode(mode), rationale or None))
    return out


def main():
    p = argparse.ArgumentParser(
        prog="seed-binds", description="Replay BINDS edges from a TSV manifest (spec §7).")
    p.add_argument("manifest", help="path to the TSV manifest")
    p.add_argument("--suggest-only", action="store_true",
                   help="stage the binds as suggestions without ratifying them")
    p.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    try:
        rows = parse_manifest(args.manifest)
    except (OSError, ValueError) as e:
        sys.exit(f"[seed-binds] {e}")

    lm = notelib.LinkManager()
    created = skipped = failed = 0
    details = []
    for a, b, mode, rationale in rows:
        label = f"{a} → {b} [{mode or 'untyped'}]"
        if args.dry_run:
            details.append({"a": a, "b": b, "mode": mode, "action": "would-create"})
            print(f"  DRY  {label}")
            created += 1
            continue
        try:
            lm.suggest(a, b, rationale=rationale, mode=mode)
            if not args.suggest_only:
                lm.ratify(a, b, mode=mode)
            created += 1
            details.append({"a": a, "b": b, "mode": mode, "action": "created"})
            print(f"  OK   {label}")
        except notelib.LinkExists:
            skipped += 1
            details.append({"a": a, "b": b, "mode": mode, "action": "exists"})
            print(f"  SKIP {label} (already bound)")
        except (ValueError, notelib.ArcadeError) as e:
            failed += 1
            details.append({"a": a, "b": b, "mode": mode, "action": "failed", "error": str(e)})
            print(f"  FAIL {label}: {e}")

    summary = {"manifest": args.manifest, "rows": len(rows), "created": created,
               "skipped": skipped, "failed": failed, "dry_run": args.dry_run,
               "ratified": not args.suggest_only}
    if args.json:
        print(json.dumps({**summary, "details": details}, indent=2, ensure_ascii=False))
    else:
        state = "would create" if args.dry_run else (
            "suggested" if args.suggest_only else "suggested + ratified")
        print(f"[seed-binds] {created} {state}, {skipped} already present, {failed} failed "
              f"(of {len(rows)} rows)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
