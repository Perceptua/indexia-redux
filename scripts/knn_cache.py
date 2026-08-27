#!/usr/bin/env python3
"""knn-cache — materialize the k-NN adjacency cache the provocation moves read (spec §12.6).

Asking `vector.neighbors` for a note's neighbours is cheap; *reaching* the ANN graph is not.
Adding one embedding invalidates the whole LSM_VECTOR index, so the next vector query rebuilds
all N vectors (~11 min at N=101, measured). The six-move digest asks per seed, so interleaved
with ingest it paid that rebuild over and over — the run that never finished in 36 minutes.

This job pays the rebuild exactly once and writes every embedded note's top-k neighbours to the
KnnCache document type; notelib.neighbors_of then serves moves 1 and 4 from those rows, falling
back to a live query for anything the cache cannot answer. Two things make that affordable:

  * it waits for the embed queue to drain first (fail-open), so the corpus stops moving before
    the first vector query — otherwise this job would pay the rebuild repeatedly itself;
  * --if-stale skips the whole rebuild when the cache already covers every embedded note, so a
    quiet day costs one cheap comparison instead of an ANN rebuild.

Run nightly, before community-detect and provocation-digest. Logs Op(KNN_CACHE).
Run through the wrapper (scripts/knn-cache.sh).
"""
import argparse
import json
import sys
import time

import notelib


def main():
    p = argparse.ArgumentParser(
        prog="knn-cache", description="Rebuild the k-NN adjacency cache (spec §12.6).")
    p.add_argument("-k", "--neighbors", type=int, default=notelib.KNN_CACHE_K, dest="k",
                   help=f"neighbours cached per note (default {notelib.KNN_CACHE_K})")
    p.add_argument("--if-stale", action="store_true", dest="if_stale",
                   help="rebuild only when the cache no longer covers the embedded notes")
    p.add_argument("--status", action="store_true",
                   help="report coverage/staleness and exit without writing")
    p.add_argument("--no-wait", action="store_true", dest="no_wait",
                   help="do not wait for pending embeddings to settle first")
    p.add_argument("--timeout", type=float, default=notelib.EMBED_SETTLE_TIMEOUT,
                   help=f"seconds to wait for the embed queue "
                        f"(default {notelib.EMBED_SETTLE_TIMEOUT:.0f})")
    p.add_argument("--quiet", action="store_true", help="suppress per-note progress")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    def log(msg):
        print(f"[knn-cache] {msg}", flush=True)

    db = notelib.Arcade()
    try:
        status = notelib.knn_cache_status(db)
        if args.status:
            print(json.dumps(status, indent=2) if args.json else
                  f"[knn-cache] {status['cached']}/{status['embedded']} note(s) cached · "
                  f"built {status['built_at'] or 'never'} · "
                  + ("stale: " + "; ".join(status["reasons"]) if status["stale"] else "fresh"))
            return
        if args.if_stale and not status["stale"]:
            out = dict(status, rebuilt=False)
            # "no Op" is stated rather than left to be inferred from its absence. A run that
            # changes nothing appends nothing (§13), so the log line is the only record that the
            # job ran at all — and a reader who knows the rule should not have to wonder whether
            # the Op is missing or was never owed.
            print(json.dumps(out, indent=2) if args.json else
                  f"[knn-cache] fresh — {status['cached']} note(s) cached, nothing to rebuild "
                  f"·  no Op (nothing changed)")
            return
        if not args.no_wait:
            notelib.wait_for_embeddings(db=db, timeout=args.timeout, log=log)

        started = time.monotonic()
        progress = None
        if not (args.quiet or args.json):
            def progress(done, total, note_id):
                end = "\n" if done == total else "\r"
                print(f"[knn-cache] {done}/{total} {note_id}", end=end, flush=True)

        res = notelib.build_knn_cache(db, k=args.k, progress=progress)
        res["seconds"] = round(time.monotonic() - started, 1)
        res["reasons"] = status["reasons"]
        op_id = notelib.unique_id(db, notelib.format_id(notelib.now_utc()), "Op")
        notelib.insert_op(db, op_id, "KNN_CACHE",
                          {"cached": res["cached"], "embedded": res["embedded"], "k": res["k"],
                           "seconds": res["seconds"], "reasons": status["reasons"]})
        res["op_id"] = op_id
    except notelib.ArcadeError as e:
        sys.exit(f"[knn-cache] failed: {e}\n  SQL: {e.sql}")

    if args.json:
        print(json.dumps(dict(res, rebuilt=True), indent=2))
    else:
        print(f"[knn-cache] cached {res['cached']}/{res['embedded']} note(s) × k={res['k']} "
              f"in {res['seconds']}s  ·  Op {res['op_id']}")


if __name__ == "__main__":
    main()
