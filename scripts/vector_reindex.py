#!/usr/bin/env python3
"""vector-reindex — drop and recreate the Note LSM_VECTOR index, so it holds live vectors only.

Every re-embed (a `CORRECT_COSMETIC`, an `EMBED` retry, a backfill) leaves the note's previous
vector in the index as a *deleted* entry. ArcadeDB never compacts those away, and on server start
it treats any deleted entry as grounds to rebuild the whole ANN graph from scratch. Observed
2026-11-25 at N=56: 1303 entries, 631 deleted — every restart spent ~4 minutes at full CPU and
~1.2 GB of heap rebuilding, and the UI's queue panel (which reaches the vector layer through
/api/provocations) hung until it finished. Restarting to "fix" the hang restarted the rebuild.

Recreating the index from the stored `Note.embedding` values is the compaction: the new index holds
exactly one entry per embedded note and nothing deleted. No embedding is recomputed and the corpus
is untouched — the index is derived from `Note.embedding`, the way `KnnCache` is derived from the
index. The CREATE statement is read from ddl/schema.sql, so the recreated index cannot drift from
the one `apply-ddl.sh` would build.

Steps: wait for the embed queue to settle → hot backup → under the vector-query lock, DROP and
CREATE the index, then pay the one ANN build with a warm-up query so the next reader does not →
log Op(REINDEX_VECTOR). Run through the wrapper (scripts/vector-reindex.sh).
"""
import argparse
import json
import os
import re
import sys
import time

import notelib

INDEX = "Note[embedding]"
SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ddl", "schema.sql")


def create_statement(path=SCHEMA):
    """The LSM_VECTOR CREATE INDEX statement exactly as ddl/schema.sql has it, comments stripped
    (the server's parser rejects `--` comments, see apply-ddl.sh)."""
    sql = re.sub(r"--.*", "", open(path, encoding="utf-8").read())
    for st in (s.strip() for s in sql.split(";")):
        if re.match(r"CREATE\s+INDEX\b.*\bON\s+Note\s*\(\s*embedding\s*\)\s+LSM_VECTOR\b", st,
                    re.S | re.I):
            return " ".join(st.split())
    raise SystemExit(f"[vector-reindex] no LSM_VECTOR index on Note(embedding) in {path}")


def index_present(db):
    return bool(notelib.rows(db.query(
        "SELECT name FROM schema:indexes WHERE name = :n", {"n": INDEX})))


def embedded_count(db):
    return int(notelib.first_row(db.query(
        "SELECT count(*) AS n FROM Note WHERE embedding IS NOT NULL")).get("n") or 0)


def probe_vector(db):
    """Any one stored embedding: the warm-up query's vector. None on an unembedded corpus."""
    return notelib.first_row(db.query(
        "SELECT embedding FROM Note WHERE embedding IS NOT NULL LIMIT 1")).get("embedding")


def main():
    p = argparse.ArgumentParser(
        prog="vector-reindex",
        description="Drop and recreate the Note LSM_VECTOR index (compacts deleted vectors).")
    p.add_argument("--status", action="store_true",
                   help="report the index and embedded-note count, and exit without writing")
    p.add_argument("--no-backup", action="store_true", dest="no_backup",
                   help="skip the hot backup taken before the index is dropped")
    p.add_argument("--no-wait", action="store_true", dest="no_wait",
                   help="do not wait for pending embeddings to settle first")
    p.add_argument("--no-warm", action="store_true", dest="no_warm",
                   help="skip the warm-up query (the next vector query then pays the ANN build)")
    p.add_argument("--timeout", type=float, default=notelib.EMBED_SETTLE_TIMEOUT,
                   help=f"seconds to wait for the embed queue "
                        f"(default {notelib.EMBED_SETTLE_TIMEOUT:.0f})")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    def log(msg):
        if not args.json:
            print(f"[vector-reindex] {msg}", flush=True)

    stmt = create_statement()
    db = notelib.Arcade()
    try:
        status = {"index": INDEX, "present": index_present(db), "embedded": embedded_count(db),
                  "create": stmt}
        if args.status:
            print(json.dumps(status, indent=2) if args.json else
                  f"[vector-reindex] {INDEX} {'present' if status['present'] else 'MISSING'} · "
                  f"{status['embedded']} embedded note(s)\n"
                  f"[vector-reindex] deleted-entry counts are only reported by the server, at "
                  f"start: `scripts/logs.sh | grep 'Deleted vectors'`")
            return

        if not args.no_wait:
            notelib.wait_for_embeddings(db=db, timeout=args.timeout, log=log)

        backup = None
        if not args.no_backup:
            log("hot backup (BACKUP DATABASE)…")
            backup = notelib.first_row(db.command("BACKUP DATABASE")).get("backupFile") or "ok"
            log(f"backup written: {backup}")

        started = time.monotonic()
        warmed = None
        with notelib.vector_query_lock():
            if status["present"]:
                log(f"dropping {INDEX}")
                db.command(f"DROP INDEX `{INDEX}`")
            log("recreating it from ddl/schema.sql")
            try:
                db.command(stmt)
            except notelib.ArcadeError:
                print(f"[vector-reindex] the index was DROPPED but could not be recreated — "
                      f"semantic search is down until it is. Re-run this, or "
                      f"`scripts/apply-ddl.sh`, which issues the same statement:\n  {stmt}",
                      file=sys.stderr)
                raise
            if not args.no_warm:
                vec = probe_vector(db)
                if vec is not None:
                    log(f"building the ANN graph over {status['embedded']} vector(s) "
                        f"(the one rebuild, paid now)…")
                    resp = db.query("SELECT count(*) AS n FROM (SELECT expand(vector.neighbors("
                                    f"'{INDEX}', :q, 1, 10)))", {"q": list(vec)})
                    warmed = int(notelib.first_row(resp).get("n") or 0)
                    if warmed < 1:
                        sys.exit("[vector-reindex] recreated, but the warm-up query found no "
                                 "neighbours — check `scripts/logs.sh` before relying on search")
        seconds = round(time.monotonic() - started, 1)

        res = {"index": INDEX, "embedded": embedded_count(db), "seconds": seconds,
               "warmed": warmed is not None, "backup": backup}
        op_id = notelib.unique_id(db, notelib.format_id(notelib.now_utc()), "Op")
        notelib.insert_op(db, op_id, "REINDEX_VECTOR",
                          {k: res[k] for k in ("index", "embedded", "seconds", "warmed")})
        res["op_id"] = op_id
    except notelib.ArcadeError as e:
        sys.exit(f"[vector-reindex] failed: {e}\n  SQL: {e.sql}")

    print(json.dumps(res, indent=2) if args.json else
          f"[vector-reindex] {INDEX} recreated over {res['embedded']} embedded note(s) in "
          f"{seconds}s{'' if res['warmed'] else ' (not warmed)'}  ·  Op {op_id}")


if __name__ == "__main__":
    main()
