#!/usr/bin/env python3
"""vector-reindex: dropping and recreating the LSM_VECTOR index keeps every embedded note
searchable, logs one Op, and works from the statement ddl/schema.sql carries.

**The one DB test that does not touch the live corpus.** Recreating the live index means a full ANN
rebuild (minutes, at full CPU) and a window with no semantic search at all, which is no thing for a
test run to inflict. So this creates a throwaway database, builds just enough schema in it (the
index statement itself comes from ddl/schema.sql, the way the script reads it), plants a handful of
synthetic notes, re-embeds some so the index holds deleted entries — the condition the script exists
to clear — runs the script against it, and drops the database on the way out.
"""
import json
import os
import random
import subprocess
import sys

import lib

notelib, check = lib.notelib, lib.check
vr = __import__("vector_reindex")

SCRATCH = "indexia_test_vector_reindex"
DIM = 1024
N = 6

admin = notelib.Arcade()


def server(cmd):
    return admin._request("server", {"command": cmd})


def vec(seed):
    rnd = random.Random(seed)
    v = [rnd.uniform(-1, 1) for _ in range(DIM)]
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


stmt = vr.create_statement()
check("ddl/schema.sql carries the LSM_VECTOR statement", "LSM_VECTOR" in stmt
      and "dimensions: 1024" in stmt.replace("  ", " "), stmt)

try:
    server(f"drop database {SCRATCH}")
except notelib.ArcadeError:
    pass
server(f"create database {SCRATCH}")
env = dict(os.environ, DB=SCRATCH)
os.environ["DB"] = SCRATCH
db = notelib.Arcade()
try:
    for st in ("CREATE VERTEX TYPE Note", "CREATE PROPERTY Note.id STRING",
               "CREATE PROPERTY Note.body STRING", "CREATE PROPERTY Note.embedding ARRAY_OF_FLOATS",
               "CREATE VERTEX TYPE Op", "CREATE PROPERTY Op.id STRING",
               "CREATE PROPERTY Op.rule STRING", "CREATE PROPERTY Op.payload STRING", stmt):
        db.command(st)
    ids = [f"2026010{i}T000000000Z" for i in range(1, N + 1)]
    for i, nid in enumerate(ids):
        db.command("INSERT INTO Note SET id = :id, body = :b, embedding = :e",
                   {"id": nid, "b": f"synthetic {i}", "e": vec(i)})
    for i, nid in enumerate(ids[:3]):          # re-embed: leaves deleted entries in the index
        db.command("UPDATE Note SET embedding = :e WHERE id = :id",
                   {"id": nid, "e": vec(100 + i)})

    st = subprocess.run([sys.executable, os.path.join(lib.REPO, "scripts", "vector_reindex.py"),
                         "--status", "--json"], env=env, capture_output=True, text=True)
    status = json.loads(st.stdout or "{}")
    check("--status sees the index and every embedded note",
          status.get("present") and status.get("embedded") == N, st.stdout + st.stderr)
    ops_before = notelib.first_row(db.query("SELECT count(*) AS n FROM Op")).get("n")
    check("--status writes nothing", ops_before == 0, f"{ops_before} Op(s)")

    run = subprocess.run([sys.executable, os.path.join(lib.REPO, "scripts", "vector_reindex.py"),
                          "--no-backup", "--no-wait", "--json"],
                         env=env, capture_output=True, text=True)
    res = json.loads(run.stdout or "{}") if run.returncode == 0 else {}
    check("the reindex runs to completion", run.returncode == 0, run.stderr.strip()[-400:])
    check("it recreates over every embedded note and warms the graph",
          res.get("embedded") == N and res.get("warmed") is True, json.dumps(res))
    check("the index exists afterwards", vr.index_present(db))

    ops = notelib.rows(db.query("SELECT rule, payload FROM Op"))
    check("exactly one Op(REINDEX_VECTOR) is logged",
          [o["rule"] for o in ops] == ["REINDEX_VECTOR"], json.dumps(ops))

    # Every note — the re-embedded ones included — is still its own nearest neighbour.
    found = 0
    for i, nid in enumerate(ids):
        q = vec(100 + i) if i < 3 else vec(i)
        hits = notelib.semantic_search(db, q, k=1)
        found += bool(hits) and hits[0]["id"] == nid
    check("every note is found by its current embedding", found == N, f"{found}/{N}")
    stale = notelib.semantic_search(db, vec(0), k=1)
    check("a superseded embedding no longer matches its old note exactly",
          not stale or stale[0]["score"] < 0.99, json.dumps(stale))
finally:
    os.environ["DB"] = admin.db
    try:
        server(f"drop database {SCRATCH}")
    except notelib.ArcadeError as e:
        print(f"  [warn] could not drop {SCRATCH}: {e}")

lib.report_and_exit()
