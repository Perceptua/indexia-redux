#!/usr/bin/env python3
"""ArcadeDB behaviours the code *depends on* — pinned so an upgrade tells us instead of surprising us.

Includes a live demonstration of the one fault behind two of the codebase's design decisions.

**Confirmed and reproducible:** with an index on `BINDS.created_at`, an `UPDATE … SET created_at =
null` does not remove the row's existing index entry, so a range predicate keeps matching a row whose
stored value is null. That is how the first version of the expiry sweep deleted the undated edges it
promised to spare. `stale_index_entry_on_null()` below demonstrates it on demand by creating the index,
provoking it, and dropping the index again. Value-to-value updates are maintained correctly, and with
no index nulls are excluded correctly — hence the two decisions it justifies: `created_at` stays
unindexed, and `expire_suggested_links` ages the queue in Python with an explicit null guard.

**Not reproduced:** a second symptom seen at the same time — the predicate returning 17 rows where 12
matched, two duplicated and three failing it outright. A faithful replay of the original procedure
against a restore of the pre-backfill backup was exact at every step, as were delete-then-recreate with
RID reuse, 50-edge transactions, and a full-scale queue. Probably the same class of fault (a stale entry
attaching to a reused record id); not characterized. The checks here assert the correct behaviour so its
return would be noticed.

Read-only apart from its own synthetic edges and the index it creates and drops.
"""
import lib

notelib, check = lib.notelib, lib.check

db = notelib.Arcade()

# One BINDS edge is what the endpoint-projection checks read, and two notes is the smallest corpus
# `synthetic_links` can plant an edge between for the stale-index replay further down.
lib.require(db, "the ArcadeDB invariants", notes=2, binds=1)

with lib.corpus_guard(db):
    # --- an un-LIMITed SELECT must return every row -------------------------------------------
    # notelib scans whole types un-LIMITed in several places (move5_candidates over all Notes,
    # _intra_cluster_edges, _undirected_adjacency, knn_cache_status). If the API ever capped rows,
    # all of those would silently see a prefix of the corpus.
    for typ, col in (("BINDS", "status"), ("Note", "id"), ("Op", "id")):
        counted = notelib.first_row(db.query(f"SELECT count(*) AS n FROM {typ}")).get("n")
        got = len(notelib.rows(db.query(f"SELECT {col} FROM {typ}")))
        check(f"an un-LIMITed SELECT over {typ} returns all rows", counted == got,
              f"count(*)={counted} rows={got}")

    # --- edge endpoints project through outV()/inV(), never out.id/in.id ----------------------
    row = notelib.first_row(db.query(
        "SELECT outV().id AS a, inV().id AS b FROM BINDS LIMIT 1"))
    check("outV()/inV() project edge endpoints", bool(row.get("a") and row.get("b")), str(row))
    row = notelib.first_row(db.query("SELECT out.id AS a, in.id AS b FROM BINDS LIMIT 1"))
    check("out.id/in.id still project null (why outV()/inV() is used everywhere)",
          not (row.get("a") or row.get("b")), str(row))

    # --- a date range over a VERTEX property is correct ---------------------------------------
    # This is what search --since/--until relies on, so it must stay true.
    day = "2026-07-19"
    bound_lo, bound_hi = notelib._day_bound(day, "d"), notelib._day_bound(day, "d", plus_days=1)
    sql_rows = notelib.rows(db.query(
        "SELECT id FROM Note WHERE created_at >= date(:lo) AND created_at < date(:hi)",
        {"lo": bound_lo, "hi": bound_hi}))
    truth = [r for r in notelib.rows(db.query("SELECT id FROM Note"))
             if (r.get("id") or "").startswith(day.replace("-", ""))]
    check("a date range over the vertex type Note is exact",
          len(sql_rows) == len(truth), f"sql={len(sql_rows)} truth={len(truth)}")

    with lib.synthetic_links(db, 4, age_days=45) as pairs:
        cutoff = notelib._day_bound(
            (notelib.now_utc() - __import__("datetime").timedelta(days=30)).strftime("%Y-%m-%d"),
            "cutoff")
        key = notelib._dt_digits(cutoff)
        truth = set()
        for r in notelib.rows(db.query("SELECT outV().id AS a, inV().id AS b, created_at "
                                       "FROM BINDS WHERE status = 'suggested'")):
            if r.get("created_at") and notelib._dt_digits(r["created_at"]) < key and r.get("a"):
                truth.add((r["a"], r["b"]))

        # --- the property the once-seen fault would break ------------------------------------
        got = [(r.get("a"), r.get("b")) for r in notelib.rows(db.query(
            "SELECT outV().id AS a, inV().id AS b FROM BINDS WHERE status = 'suggested' "
            "AND created_at IS NOT NULL AND created_at < date(:c)", {"c": cutoff}))]
        check("a range predicate over an edge property returns each matching edge exactly once",
              set(got) == truth and len(got) == len(truth),
              f"{len(got)} row(s) / {len(set(got))} distinct vs {len(truth)} true; "
              f"dup={len(got) - len(set(got))} extra={len(set(got) - truth)} "
              f"missed={len(truth - set(got))}")

        # the subquery form filters outside any index path, so it is the fallback if the above breaks
        got_sub = [(r.get("a"), r.get("b")) for r in notelib.rows(db.query(
            "SELECT a, b FROM (SELECT outV().id AS a, inV().id AS b, status, created_at "
            "FROM BINDS) WHERE status = 'suggested' AND created_at IS NOT NULL "
            "AND created_at < date(:c)", {"c": cutoff}))]
        check("the subquery-wrapped form agrees with it",
              set(got_sub) == truth and len(got_sub) == len(truth),
              f"{len(got_sub)} row(s) vs {len(truth)} true")

        # --- created_at must stay unindexed --------------------------------------------------
        # Not because indexing it is proven harmful — four attempts failed to reproduce that — but
        # because the one observation happened with the index present, and the sweep gains nothing
        # from it at this scale. Re-adding it should be a deliberate act that re-runs this file.
        indexes = {(r.get("name") or "") for r in notelib.rows(db.query(
            "SELECT name FROM schema:indexes"))}
        check("BINDS.created_at is not indexed (see ddl/schema.sql for why)",
              "BINDS[created_at]" not in indexes)
        # mode is nullable too — `link.sh retype … --mode none` nulls it — so it carries the
        # exact same stale-entry hazard and is deliberately left unindexed as well.
        check("BINDS.mode is not indexed (nullable: same hazard as created_at)",
              "BINDS[mode]" not in indexes)
        check("BINDS.status is indexed (the read layer filters on it constantly)",
              "BINDS[status]" in indexes)
        # BEGETS.created_at (v0.8.0) is nullable on any edge predating the migration, so it is
        # the same hazard a third time and is left unindexed for the same reason.
        check("BEGETS.created_at is not indexed (nullable: same hazard as BINDS.created_at)",
              "BEGETS[created_at]" not in indexes)

        # --- the graph is purely relational: Note + Op, BEGETS + BINDS (v0.8.0, §13) ---------
        types = {(r.get("name") or "") for r in notelib.rows(db.query(
            "SELECT name FROM schema:types"))}
        check("BEGETS and BINDS exist", {"BEGETS", "BINDS"} <= types)
        gone_v070 = {"CATALYZES", "LINKS_TO", "SUPERSEDES"}
        check("CATALYZES / LINKS_TO / SUPERSEDES are gone (v0.7.0)",
              not (gone_v070 & types),
              "absent" if not (gone_v070 & types) else f"still present: {sorted(gone_v070 & types)}")
        # Reified observations, removed in v0.8.0: a walk is its Op sequence and a cluster is a
        # query. If any of these come back, something is being stored that should be computed.
        gone_v080 = {"Trace", "Cluster", "VISITED", "PRODUCED", "FORKED_FROM",
                     "CONTAINS", "HUB_OF", "DERIVES_FROM"}
        check("the Trace and Cluster types and their edges are gone (v0.8.0)",
              not (gone_v080 & types),
              "absent" if not (gone_v080 & types) else f"still present: {sorted(gone_v080 & types)}")

        # --- Note carries no stored analytics -------------------------------------------------
        note_props = {p.get("name") for p in (notelib.first_row(db.query(
            "SELECT properties FROM schema:types WHERE name = 'Note'")).get("properties") or [])
            if isinstance(p, dict)}
        check("Note.fitness and Note.activation are gone (v0.8.0 — computed, not stored)",
              not ({"fitness", "activation"} & note_props),
              "absent" if not ({"fitness", "activation"} & note_props)
              else f"still present: {sorted({'fitness', 'activation'} & note_props)}")
        check("Note.visited exists — the one attention counter the note carries",
              "visited" in note_props)
        begets_props = {p.get("name") for p in (notelib.first_row(db.query(
            "SELECT properties FROM schema:types WHERE name = 'BEGETS'")).get("properties") or [])
            if isinstance(p, dict)}
        check("BEGETS.created_at exists — the structural layer is datable (v0.8.0)",
              "created_at" in begets_props)
        undated = notelib.first_row(db.query(
            "SELECT count(*) AS n FROM BEGETS WHERE created_at IS NULL")).get("n") or 0
        check("every BEGETS edge is dated (migration + link_begets)", undated == 0,
              f"{undated} undated — run scripts/migrate-v0-8-0.sh")

        # --- nulls must not satisfy a range predicate ----------------------------------------
        lib.undate(db, pairs[:2])
        total = notelib.first_row(db.query(
            "SELECT count(*) AS n FROM BINDS WHERE status = 'suggested'")).get("n")
        nulls = notelib.first_row(db.query(
            "SELECT count(*) AS n FROM BINDS "
            "WHERE status = 'suggested' AND created_at IS NULL")).get("n")
        tomorrow = notelib._day_bound(
            (notelib.now_utc() + __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d"),
            "d")
        matched = notelib.first_row(db.query(
            "SELECT count(*) AS n FROM BINDS WHERE status = 'suggested' "
            "AND created_at < date(:c)", {"c": tomorrow})).get("n")
        check("a null DATETIME does not satisfy `< date(...)` — undated edges cannot be swept "
              "by accident", nulls > 0 and matched == total - nulls,
              f"{nulls} null(s) of {total}; predicate matched {matched}, expected {total - nulls}")

        # --- and the sweep agrees, whatever SQL does ------------------------------------------
        # notelib ages the queue in Python, so this must hold even if the SQL above regresses.
        res = notelib.expire_suggested_links(db, keep_min=0, dry_run=True)
        check("expire_suggested_links never selects an undated edge",
              not (set(pairs[:2]) & set(res["pairs"])), f"{res['undated']} undated reported")

    # --- the confirmed fault, demonstrated live -----------------------------------------------
    # Creates the index, provokes the stale entry, drops the index again. This is the evidence for
    # why ddl/schema.sql leaves created_at unindexed; if it ever stops reproducing, ArcadeDB has
    # fixed the underlying bug and that decision can be revisited.
    IDX = "BINDS[created_at]"

    def index_present():
        return any((r.get("name") or "") == IDX for r in notelib.rows(db.query(
            "SELECT name FROM schema:indexes")))

    def matches_range(pair, cutoff):
        """Does the unguarded range predicate return this pair?"""
        return pair in [(r.get("a"), r.get("b")) for r in notelib.rows(db.query(
            "SELECT outV().id AS a, inV().id AS b FROM BINDS WHERE status = 'suggested' "
            "AND created_at < date(:c)", {"c": cutoff}))]

    check("the index is absent before this check runs", not index_present())
    cutoff = notelib._day_bound(
        (notelib.now_utc() - __import__("datetime").timedelta(days=30)).strftime("%Y-%m-%d"), "c")
    for with_index in (True, False):
        try:
            if with_index:
                db.command("CREATE INDEX ON BINDS (created_at) NOTUNIQUE")
            with lib.synthetic_links(db, 1, age_days=45) as one:
                pair = one[0]
                before = matches_range(pair, cutoff)
                lib.undate(db, [pair])
                stored = notelib.first_row(db.query(
                    "SELECT created_at FROM BINDS WHERE outV().id = :a AND inV().id = :b",
                    {"a": pair[0], "b": pair[1]})).get("created_at")
                after = matches_range(pair, cutoff)
                where = "with the index" if with_index else "without the index"
                check(f"{where}: an aged edge matches the range before nulling", before)
                check(f"{where}: its stored value really is null after the UPDATE", stored is None)
                if with_index:
                    check("CONFIRMED FAULT: with the index, a nulled row STILL matches the range "
                          "(if this fails, ArcadeDB fixed it — created_at could be indexed again)",
                          after, "stale index entry survived the UPDATE")
                else:
                    check("without the index, a nulled row correctly stops matching", not after)
        finally:
            if index_present():
                db.command(f"DROP INDEX `{IDX}`")
    check("the index is dropped again afterwards", not index_present())

    # --- deleting an edge leaves no dangling adjacency ----------------------------------------
    with lib.synthetic_links(db, 1) as one:
        a, b = one[0]
        db.command("DELETE FROM BINDS WHERE outV().id = :a AND inV().id = :b",
                   {"a": a, "b": b})
        left = notelib.first_row(db.query(
            "SELECT count(*) AS n FROM BINDS WHERE outV().id = :a AND inV().id = :b",
            {"a": a, "b": b})).get("n") or 0
        check("DELETE FROM <edge type> removes the edge cleanly", left == 0, f"{left} left")

lib.report_and_exit()
