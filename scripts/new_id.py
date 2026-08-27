#!/usr/bin/env python3
"""Mint fresh, valid, unique note ids (spec §4) for staging files.

Prints one id per line — a compact UTC timestamp to the millisecond, e.g.
20260723T142530123Z. Uniqueness is checked against the files already in staging/
(and staging/processed + staging/failed), so a batch minted for a multi-note card
never collides; same-millisecond ties get the spec §4 two-digit counter.

Offline and DB-free: used by the transcribe-notes skill to name staging/<id>.md
files. Uniqueness against the corpus itself is enforced later, at commit time, by
ingest-staging.
"""
import argparse
import os
import time

import notelib

STAGING = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "staging")
EXTS = (".md", ".txt", ".note")


def _strip_ext(name):
    lower = name.lower()
    for ext in EXTS:
        if lower.endswith(ext):
            return name[: -len(ext)]
    return name


def existing_ids(staging):
    ids = set()
    for sub in (staging, os.path.join(staging, "processed"), os.path.join(staging, "failed")):
        if os.path.isdir(sub):
            ids.update(_strip_ext(n) for n in os.listdir(sub))
    return ids


def mint(count, staging):
    used = existing_ids(staging)
    out = []
    for _ in range(max(1, count)):
        base = notelib.format_id(notelib.now_utc())
        candidate, i = base, 0
        while candidate in used or candidate in out:
            candidate = f"{base}{i:02d}"   # spec §4 sub-millisecond collision counter
            i += 1
        out.append(candidate)
        time.sleep(0.001)                  # nudge the clock so sequential ids differ
    return out


def main():
    ap = argparse.ArgumentParser(prog="new-id", description="Mint unique note ids for staging.")
    ap.add_argument("count", nargs="?", type=int, default=1, help="how many ids (default 1)")
    ap.add_argument("--dir", default=STAGING, help="staging directory to check for collisions")
    args = ap.parse_args()
    print("\n".join(mint(args.count, args.dir)))


if __name__ == "__main__":
    main()
