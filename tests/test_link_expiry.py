#!/usr/bin/env python3
"""The suggestion expiry sweep — what it deletes, and the three things it must never delete.

Every case here plants its own synthetic `suggested` edges and removes them again, so a genuine
suggestion is never at risk. That is not paranoia: an earlier version of this sweep really did delete
52 real suggestions during testing, because the SQL it trusted was wrong (see test_db_invariants.py).

The guards under test:
  * only `suggested` edges older than the cutoff go;
  * nothing at all goes while the queue is at or under `keep_min`;
  * an edge with no `created_at` is counted but never swept;
  * `ratified` edges are corpus and are never touched.
"""
import lib

notelib, check = lib.notelib, lib.check

db = notelib.Arcade()
KEEP_MIN = 0            # most cases want the small-queue guard out of the way


def suggested_count():
    return notelib.first_row(db.query(
        "SELECT count(*) AS n FROM BINDS WHERE status = 'suggested'")).get("n") or 0


def ground_truth(cutoff_day):
    """The right answer, computed in Python from a full scan — never from the range SQL under test."""
    key = notelib._dt_digits(cutoff_day.replace("-", "") + "0" * 9)
    out = set()
    for r in notelib.rows(db.query("SELECT outV().id AS a, inV().id AS b, created_at "
                                   "FROM BINDS WHERE status = 'suggested'")):
        if r.get("a") and r.get("b") and r.get("created_at") \
                and notelib._dt_digits(r["created_at"]) < key:
            out.add((r["a"], r["b"]))
    return out


# The largest plant below is 8 edges, and `synthetic_links` walks a widening index gap, so it can
# offer sum(n - gap) for gap in 1..n//2 pairs — 8 notes is the smallest corpus that clears it with
# room for the pairs that are already linked.
lib.require(db, "the expiry sweep", notes=8)

with lib.corpus_guard(db):
    ratified_before = notelib.first_row(db.query(
        "SELECT count(*) AS n FROM BINDS WHERE status = 'ratified'")).get("n")

    # --- fresh edges are not swept ------------------------------------------------------------
    with lib.synthetic_links(db, 6) as fresh:
        check("planted 6 fresh synthetic edges", len(fresh) == 6, f"{len(fresh)}")
        res = notelib.expire_suggested_links(db, keep_min=KEEP_MIN, dry_run=True)
        check("a fresh queue has nothing to expire", res["expired"] == 0,
              f"{res['expired']} of {res['total_suggested']}, cutoff {res['cutoff']}")

    # --- aged edges are swept, and only those ---------------------------------------------------
    with lib.synthetic_links(db, 8, age_days=45) as aged:
        aged_set = set(aged)
        res = notelib.expire_suggested_links(db, keep_min=KEEP_MIN, dry_run=True)
        found = set(res["pairs"])
        check("the sweep matches Python-computed ground truth exactly",
              found == ground_truth(res["cutoff"]), f"{len(found)} pair(s)")
        check("every aged synthetic edge is selected", aged_set <= found,
              f"{len(aged_set & found)}/{len(aged_set)}")
        check("nothing else is selected", found == aged_set,
              f"{len(found - aged_set)} unexpected")
        check("no pair is reported twice", len(res["pairs"]) == len(found),
              f"{len(res['pairs'])} rows vs {len(found)} distinct")

        # the small-queue guard, checked while there IS something to sweep
        big = notelib.expire_suggested_links(db, keep_min=10 ** 6, dry_run=True)
        check("a queue at or under keep_min is left entirely alone",
              big["skipped_small_queue"] and big["expired"] == 0, str(big["expired"]))

        # --- and now for real -----------------------------------------------------------------
        before = suggested_count()
        res = notelib.expire_suggested_links(db, keep_min=KEEP_MIN)
        after = suggested_count()
        check("the real sweep deletes exactly the aged edges",
              res["expired"] == len(aged_set) and before - after == len(aged_set),
              f"expired {res['expired']}, queue {before} -> {after}")
        check("the sweep logs one Op for the whole run", bool(res["op_id"]), str(res["op_id"]))
        op = notelib.first_row(db.query("SELECT rule FROM Op WHERE id = :i", {"i": res["op_id"]}))
        check("that Op is EXPIRE_LINKS", op.get("rule") == "EXPIRE_LINKS", str(op.get("rule")))

    # --- undated edges are counted but never swept ---------------------------------------------
    with lib.synthetic_links(db, 5, age_days=45) as pairs:
        lib.undate(db, pairs[:3])
        res = notelib.expire_suggested_links(db, keep_min=KEEP_MIN, dry_run=True)
        check("undated edges are reported", res["undated"] >= 3, f"{res['undated']}")
        check("and none of them is selected for deletion",
              not (set(pairs[:3]) & set(res["pairs"])),
              f"{len(set(pairs[:3]) & set(res['pairs']))} leaked in")
        check("while the dated ones still are", set(pairs[3:]) <= set(res["pairs"]))

        before = suggested_count()
        notelib.expire_suggested_links(db, keep_min=KEEP_MIN)
        still_there = notelib.first_row(db.query(
            "SELECT count(*) AS n FROM BINDS WHERE created_at IS NULL")).get("n") or 0
        check("undated edges survive a real sweep", still_there >= 3, f"{still_there} remain")
        check("only the dated ones were removed",
              before - suggested_count() == len(pairs[3:]), f"{before - suggested_count()}")

    # --- ratified edges are corpus ---------------------------------------------------------------
    ratified_after = notelib.first_row(db.query(
        "SELECT count(*) AS n FROM BINDS WHERE status = 'ratified'")).get("n")
    check("no ratified edge was touched by any sweep", ratified_before == ratified_after,
          f"{ratified_before} -> {ratified_after}")

    # --- every new edge is dated ---------------------------------------------------------------
    with lib.synthetic_links(db, 1) as one:
        a, b = one[0]
        row = notelib.first_row(db.query(
            "SELECT created_at FROM BINDS WHERE outV().id = :a AND inV().id = :b",
            {"a": a, "b": b}))
        check("LinkManager.suggest stamps created_at", bool(row.get("created_at")),
              str(row.get("created_at")))
        check("and that stamp reads back as today's date",
              notelib.age_in_days(row.get("created_at")) == 0,
              f"age {notelib.age_in_days(row.get('created_at'))}d")

    # --- the other end of the queue: the digest's standing-depth ceiling ------------------------
    # stage_budget is pinned as arithmetic in test_stage_order.py. What is checked here is that
    # build() actually *obeys* the number over a real corpus — that the staging loop stops at the
    # budget rather than at the per-run cap. Staging is counted, not performed: LinkManager.suggest
    # is swapped for a counter, so this exercises the whole path and writes nothing. (Same idiom as
    # test_knn_cache.py's counting_search.)
    queued = notelib.suggested_link_count(db)
    check("suggested_link_count agrees with a direct count — one definition of 'the queue'",
          queued == suggested_count(), f"{queued}")

    attempts = []
    real_suggest = notelib.LinkManager.suggest
    notelib.LinkManager.suggest = lambda self, a, b, **kw: attempts.append((a, b))
    try:
        pd = __import__("provocation_digest")
        for room in (0, 1, 3):
            attempts.clear()
            res = pd.build(db, seeds_limit=10, k=5, stage=True,
                           max_queue=queued + room, wait=False, use_cache=True)
            want = min(pd.STAGE_CAP, room)
            check(f"with room for {room}, the staging loop stops at {want}",
                  len(attempts) == want and res["staged"] == want and res["budget"] == want,
                  f"{len(attempts)} attempt(s), staged {res['staged']}, budget {res['budget']}")
            check(f"...and room for {room} still renders everything found",
                  res["offered"] > want, f"{res['offered']} eligible")
        attempts.clear()
        res = pd.build(db, seeds_limit=10, k=5, stage=True, max_queue=queued + 500, wait=False)
        check("with room to spare the per-run cap is what binds, not the ceiling",
              len(attempts) == pd.STAGE_CAP and res["budget"] == pd.STAGE_CAP,
              f"{len(attempts)} attempt(s), budget {res['budget']}")
    finally:
        notelib.LinkManager.suggest = real_suggest
    check("the queue is exactly where it was — the ceiling checks staged nothing",
          notelib.suggested_link_count(db) == queued, f"{notelib.suggested_link_count(db)}")

    # --- the shipped constants -----------------------------------------------------------------
    check("SUGGESTION_MAX_AGE_DAYS is a positive number of days",
          notelib.SUGGESTION_MAX_AGE_DAYS > 0, f"{notelib.SUGGESTION_MAX_AGE_DAYS}")
    check("SUGGESTION_KEEP_MIN is non-negative", notelib.SUGGESTION_KEEP_MIN >= 0,
          f"{notelib.SUGGESTION_KEEP_MIN}")
    check("SUGGESTION_MAX_QUEUE leaves room above SUGGESTION_KEEP_MIN — a ceiling at or below "
          "the sweep's floor would freeze the queue: nothing could be added and nothing pruned",
          notelib.SUGGESTION_KEEP_MIN < notelib.SUGGESTION_MAX_QUEUE,
          f"keep-min {notelib.SUGGESTION_KEEP_MIN}, ceiling {notelib.SUGGESTION_MAX_QUEUE}")

lib.report_and_exit()
