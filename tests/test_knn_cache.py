#!/usr/bin/env python3
"""The materialized k-NN layer: staleness detection, the live-fallback rules, and the property that
matters most — that a digest pass issues **zero** vector queries.

Why zero matters: adding one embedding invalidates the entire LSM_VECTOR index, and the next vector
query rebuilds all N vectors (~11 min at N=101) before returning. If any part of the nightly run
still reaches for the index, an ordinary note-taking session can put an eleven-minute stall in front
of it. The cache is what removes that coupling, so "no live queries" is the regression to guard.

Touches only `KnnCache` — derived, rebuildable data — and restores every row it changes. The notes
and edges are never written.

**One group of checks is slow on purpose.** Verifying the live fallback means issuing a real
`vector.neighbors` query, and if the ANN index happens to be cold that single query rebuilds the whole
graph first (~10 min at 101 notes) — the very cost the cache exists to avoid. Set
`INDEXIA_TESTS_FAST=1` to skip those and keep the rest, which run in seconds.
"""
import os

import lib

notelib, check = lib.notelib, lib.check
FAST = os.environ.get("INDEXIA_TESTS_FAST") == "1"

db = notelib.Arcade()
pd = __import__("provocation_digest")

# knn_cache_guard is the second half of the safety property here, and the half the corpus guard
# cannot supply: every mutation below has its own `finally`, but a `finally` is no use to a
# process that is killed, and this file's destructive window is also its slowest stretch. The
# guard restores anything a skipped `finally` left behind, and fails a check when it has to.
lib.require(db, "the k-NN cache", notes=1, cached=1)

with lib.corpus_guard(db), lib.knn_cache_guard(db):
    # --- baseline ----------------------------------------------------------------------------
    st = notelib.knn_cache_status(db)
    fresh_at_start = not st["stale"]
    check("the cache is fresh to begin with", fresh_at_start,
          f"{st['cached']}/{st['embedded']} cached" +
          ("" if fresh_at_start else
           " — " + "; ".join(st["reasons"]) + "; run scripts/knn-cache.sh first"))
    if not fresh_at_start:
        lib.report_and_exit()

    # a note from the middle of the corpus, so it has neighbours on both sides
    victim = notelib.rows(db.query(
        "SELECT note_id, neighbors, built_at FROM KnnCache ORDER BY note_id LIMIT 60"))[-1]
    vid, vneighbors = victim["note_id"], victim["neighbors"]
    vbuilt = notelib.safe_created_at(str(victim["built_at"]))
    cached_hits = notelib.move1_candidates(db, vid, k=5)

    # --- a missing row: stale, and served live -----------------------------------------------
    db.command("DELETE FROM KnnCache WHERE note_id = :n", {"n": vid})
    try:
        st = notelib.knn_cache_status(db)
        check("a missing cache row marks the cache stale", st["stale"], "; ".join(st["reasons"]))
        check("staleness explains itself in terms of coverage",
              any("not in the cache" in r for r in st["reasons"]), "; ".join(st["reasons"]))
        check("an uncached note reads as absent, not as an error",
              notelib.knn_cached_neighbors(db, vid) is None)

        if FAST:
            lib.skip("live-fallback equivalence",
                     "INDEXIA_TESTS_FAST=1 (one vector query can cost an ANN rebuild)")
        else:
            live_hits = notelib.move1_candidates(db, vid, k=5)
            check("move 1 still answers for an uncached seed (live fallback)", bool(live_hits),
                  f"{len(live_hits)} candidate(s)")
            check("the cache had been serving exactly what a live query returns",
                  [(h["id"], round(h["score"], 4)) for h in live_hits]
                  == [(h["id"], round(h["score"], 4)) for h in cached_hits])
    finally:
        db.command("INSERT INTO KnnCache (note_id, neighbors, built_at) VALUES (:n, :b, :t)",
                   {"n": vid, "b": vneighbors, "t": vbuilt})
    check("restoring the row clears the staleness",
          not notelib.knn_cache_status(db)["stale"])

    # --- an in-place re-embed is the one change the id-set comparison cannot see --------------
    # The op is made here rather than looked for. Reaching for the log's newest CORRECT_COSMETIC
    # picks up whatever some other test committed and then cleaned up, and notelib deliberately
    # stops counting those: a corrected note that was later deleted took its vector with it, so
    # the cache it would have invalidated is whole again. Both directions of that are the point.
    # embedder=None keeps Ollama out of it — and keeps a stray vector out of the ANN index.
    ing = notelib.Ingestor(db, embedder=None)
    victim_note = ing.commit({"title": "indexia-tests: cache staleness",
                              "body": "a note that exists only in order to be corrected"},
                             embed=False)
    stale_id = victim_note.note_id
    try:
        recut = ing.correct_cosmetic(stale_id, "a note that exists only to be corrected, revised")
        # back-date every built_at to before that CORRECT_COSMETIC op
        db.command("UPDATE KnnCache SET built_at = :t",
                   {"t": notelib.id_to_created_at(recut.op_id).replace("2026", "2025", 1)})
        st = notelib.knn_cache_status(db)
        check("a CORRECT_COSMETIC newer than the build marks the cache stale", st["stale"],
              "; ".join(st["reasons"]))
        check("and names the re-embed, not a coverage gap",
              any("re-embedded in place" in r for r in st["reasons"]), "; ".join(st["reasons"]))

        db.command("DELETE FROM Note WHERE id = :id", {"id": stale_id})
        st = notelib.knn_cache_status(db)
        check("but a correction to a note since deleted does not — its vector went with it, and "
              "the log alone would call the cache stale forever",
              not st["stale"], "; ".join(st["reasons"]) or "fresh")
    finally:
        db.command("DELETE FROM Note WHERE id = :id", {"id": stale_id})
        db.command("UPDATE KnnCache SET built_at = :t", {"t": vbuilt})
    check("restoring built_at clears it again", not notelib.knn_cache_status(db)["stale"])

    # --- a corrupt row must degrade, not raise -----------------------------------------------
    db.command("UPDATE KnnCache SET neighbors = :j WHERE note_id = :n",
               {"j": "{not valid json", "n": vid})
    try:
        check("a corrupt row reads as absent rather than raising",
              notelib.knn_cached_neighbors(db, vid) is None)
        if FAST:
            lib.skip("corrupt-row fallback", "INDEXIA_TESTS_FAST=1 (needs a live vector query)")
        else:
            check("move 1 falls back to live for a corrupt row",
                  bool(notelib.move1_candidates(db, vid, k=5)))
    finally:
        db.command("UPDATE KnnCache SET neighbors = :j WHERE note_id = :n",
                   {"j": vneighbors, "n": vid})

    # --- a short cached answer is served, NOT re-queried ---------------------------------------
    # Move 1 subtracts the seed's whole graph-near neighbourhood, which on a well-linked corpus
    # removes most of a 50-entry cached list. Falling back on "fewer than k survivors" is what
    # once turned an 8-second digest into 654 seconds.
    live_calls = []
    real_search = notelib.semantic_search

    def counting_search(db_, vector, k=10, ef=100, exclude_ids=()):
        live_calls.append(k)
        return real_search(db_, vector, k=k, ef=ef, exclude_ids=exclude_ids)

    notelib.semantic_search = counting_search
    try:
        seeds = pd._seeds(db, 10)
        shortest = None
        for sid in seeds:
            pairs = notelib.knn_cached_neighbors(db, sid)
            if pairs is None:
                continue
            near = notelib.graph_neighborhood(db, sid, 2)
            survivors = [p for p in pairs if p[0] != sid and p[0] not in near]
            if survivors and (shortest is None or len(survivors) < shortest[1]):
                shortest = (sid, len(survivors))
        if shortest and shortest[1] < 5:
            live_calls.clear()
            hits = notelib.move1_candidates(db, shortest[0], k=5)
            check("a seed with fewer than k survivors is served from cache, not re-queried",
                  not live_calls and 0 < len(hits) <= 5,
                  f"seed had {shortest[1]} survivor(s), got {len(hits)} hit(s), "
                  f"{len(live_calls)} live query(ies)")
        else:
            lib.skip("short-answer path", "no seed currently yields fewer than k survivors")

        # --- the headline invariant: a whole digest pass touches no vectors -------------------
        live_calls.clear()
        res = pd.build(db, seeds_limit=10, k=5, stage=False, wait=False)
        check("a full six-move digest pass issues zero live vector queries",
              not live_calls, f"{len(live_calls)} query(ies)"
              + (f" k={live_calls}" if live_calls else "")
              + f"; {len(res['per_seed'])} seed(s), {res['offered']} eligible pair(s)")
        check("and it still produced candidates", res["offered"] > 0, f"{res['offered']}")
    finally:
        notelib.semantic_search = real_search

    # --- the guard around all of this, proved rather than trusted -----------------------------
    # Every mutation above restores itself in a `finally`, so knn_cache_guard should never have
    # anything to do — which is exactly what makes it worth checking. It only fires when a run is
    # killed, and a killed run cannot come back to assert that the net held. So the leak is staged
    # by hand against a snapshot taken a moment earlier: the same inputs the guard would have had,
    # without the killing. (Same instinct as test_daemon_pid.py proving the naive pgrep really
    # would have matched its decoy.)
    snap = lib.knn_cache_snapshot(db)
    db.command("DELETE FROM KnnCache WHERE note_id = :n", {"n": vid})
    db.command("UPDATE KnnCache SET neighbors = :j WHERE note_id = :n",
               {"j": "{not valid json", "n": sorted(snap)[0]})
    damage = lib.knn_cache_repair(db, snap)
    check("the cache guard notices the row a killed run would have left missing",
          any("restored" in d for d in damage), "; ".join(damage) or "reported nothing")
    check("...and the list a killed run would have left corrupt",
          any("un-corrupted" in d for d in damage), "; ".join(damage) or "reported nothing")
    check("...and puts both back, so the next run does not inherit the damage",
          notelib.knn_cached_neighbors(db, vid) is not None
          and notelib.knn_cached_neighbors(db, sorted(snap)[0]) is not None
          and lib.knn_cache_snapshot(db) == snap)
    check("a clean run gives the guard nothing to report", not lib.knn_cache_repair(db, snap))

    # --- final coverage ----------------------------------------------------------------------
    st = notelib.knn_cache_status(db)
    check("the cache is left fresh and complete",
          not st["stale"] and st["cached"] == st["embedded"], f"{st['cached']}/{st['embedded']}")

lib.report_and_exit()
