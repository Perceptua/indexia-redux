"""Shared harness for the Indexia test suite.

Import this first in every test — it puts `scripts/` on the path, so tests run from anywhere:

    import lib
    notelib = lib.notelib

Three things every DB test here needs:

* **`check()`** — record one assertion. Tests print a readable line per check and exit non-zero if
  any failed, so `run.sh` can aggregate without a test framework (the repo is zero-dep).
* **`corpus_guard()`** — a context manager that snapshots the corpus counts, then verifies on exit
  that the test put everything back. These tests run against the *live* corpus (there is no fixture
  database), so "leaves no trace" is not politeness, it is the safety property.
* **`synthetic_links()`** — a context manager that stages real `BINDS{suggested}` edges between
  otherwise-unlinked notes, optionally back-dated, and deletes exactly those on exit. Anything that
  tests the expiry sweep must plant its own edges rather than risk a genuine suggestion.
"""
import contextlib
import os
import sys
from datetime import timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

import notelib  # noqa: E402  (import needs the path above)

_results = []


def check(label, ok, detail=""):
    """Record and print one assertion. Returns `ok` so it can be used inline."""
    _results.append((label, bool(ok)))
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def skip(label, why):
    print(f"  [skip] {label} — {why}")


def report_and_exit():
    """Print a one-line summary and exit 0/1. Call at the end of every test file."""
    failed = [label for label, ok in _results if not ok]
    print()
    if failed:
        print(f"FAILED {len(failed)}/{len(_results)}: " + "; ".join(failed))
    else:
        print(f"passed {len(_results)}/{len(_results)}")
    sys.exit(1 if failed else 0)


# ---- corpus preconditions ---------------------------------------------------
# These tests run against the live corpus, and a live corpus starts empty. A test whose subject
# does not exist yet has nothing to say: it should stand down and say what it is waiting for, not
# crash on an empty list and not pass vacuously. So every DB test declares the minimum corpus its
# first assertion needs, and a young corpus reads as a skip rather than a regression — which is
# what keeps `run.sh` honest on day one of a new corpus, when a wall of tracebacks would be
# indistinguishable from something actually being broken.
COUNT_SQL = {
    "notes": "SELECT count(*) AS n FROM Note WHERE status = 'active'",
    "binds": "SELECT count(*) AS n FROM BINDS",
    "begets": "SELECT count(*) AS n FROM BEGETS",
    "cached": "SELECT count(*) AS n FROM KnnCache",
}


def require(db, label, **minimums):
    """Skip the whole file unless the corpus is big enough for it to mean anything.

    Prints one `[skip]` line naming every shortfall, then exits 0. Call it *before* opening
    `corpus_guard`, so a skipped file's only output is the one line saying what it wants.
    """
    short = []
    for what, need in sorted(minimums.items()):
        have = notelib.first_row(db.query(COUNT_SQL[what])).get("n") or 0
        if have < need:
            short.append(f"{need}+ {what}, found {have}")
    if short:
        skip(label, "needs " + "; ".join(short))
        sys.exit(0)


# ---- corpus safety ----------------------------------------------------------
# The whole graph since v0.8.0 — one domain vertex type and two edge types (spec §13). Op is
# deliberately NOT counted: it is append-only by design, and any test that legitimately writes
# leaves an Op behind. Tests that need to assert "no Op was written" (the analytics read-only
# guarantee) check it themselves — see test_analytics_readonly.py.
COUNTED = ("Note", "BEGETS", "BINDS")


def corpus_counts(db):
    """{type: row count} for the types a test must not disturb."""
    out = {}
    for typ in COUNTED:
        out[typ] = notelib.first_row(db.query(f"SELECT count(*) AS n FROM {typ}")).get("n") or 0
    return out


@contextlib.contextmanager
def corpus_guard(db, allow=()):
    """Snapshot the corpus, run the test, then assert nothing changed.

    `allow` names types whose counts the test legitimately changes (rare — prefer cleaning up).
    A drift here is a failed check, not an exception, so one leaky test still reports its results.
    """
    before = corpus_counts(db)
    try:
        yield before
    finally:
        after = corpus_counts(db)
        drift = {t: (before[t], after[t]) for t in before
                 if before[t] != after[t] and t not in allow}
        check("corpus left exactly as found", not drift,
              "unchanged" if not drift else
              "; ".join(f"{t} {b}->{a}" for t, (b, a) in drift.items()))


# ---- the derived cache (outside COUNTED, but not outside repair) -------------
def knn_cache_snapshot(db):
    """{note_id: neighbors} for every cache row — what knn_cache_guard holds and restores."""
    return {r["note_id"]: r.get("neighbors") for r in notelib.rows(
        db.query("SELECT note_id, neighbors FROM KnnCache")) if r.get("note_id")}


def knn_cache_repair(db, before):
    """Restore the cache to `before`; return a list of what had to be put right (empty = clean).

    Split out from the guard below so the guard can be *proved* rather than trusted. A safety net
    nobody has watched catch anything is indistinguishable from one with a hole in it, and this
    one only fires on a path — a killed process — that a test cannot take and still make
    assertions afterwards. Handed a doctored snapshot, this is that path without the killing.
    """
    after = knn_cache_snapshot(db)
    missing = sorted(set(before) - set(after))
    altered = sorted(n for n in set(before) & set(after) if before[n] != after[n])
    extra = sorted(set(after) - set(before))
    for note_id in missing:
        db.command("INSERT INTO KnnCache (note_id, neighbors, built_at) VALUES (:n, :b, :t)",
                   {"n": note_id, "b": before[note_id],
                    "t": notelib.format_created_at(notelib.now_utc())})
    for note_id in altered:
        db.command("UPDATE KnnCache SET neighbors = :b WHERE note_id = :n",
                   {"b": before[note_id], "n": note_id})
    for note_id in extra:
        db.command("DELETE FROM KnnCache WHERE note_id = :n", {"n": note_id})
    return ([f"{len(missing)} row(s) restored"] if missing else []) \
        + ([f"{len(altered)} row(s) un-corrupted"] if altered else []) \
        + ([f"{len(extra)} stray row(s) removed"] if extra else [])


@contextlib.contextmanager
def knn_cache_guard(db):
    """Snapshot `KnnCache` and put back whatever the body removed or corrupted, then say so.

    KnnCache is deliberately absent from COUNTED above: it is derived, rebuildable data, not
    corpus, and a test that rebuilds it has changed nothing anyone wrote. But "rebuildable" costs
    a full ANN pass over every note, and that gap has already been paid for once. A test run was
    killed inside the one window where `test_knn_cache.py` holds a row deleted — the window that
    also contains its deliberately slow live-fallback query — and the live cache stayed one note
    short until a nightly `--if-stale` happened to notice, with every corpus count reading clean
    the whole time. The corpus guard could not have caught it, because by its definition nothing
    had happened.

    So the guard is here rather than in COUNTED, and it repairs as well as reports: a leaked row
    should not wait on a maintenance job to be discovered.

    Coverage and neighbour lists are what it holds, and `built_at` deliberately is not. A wrong
    `built_at` only makes `knn_cache_status` call the cache stale, which makes the next run
    rebuild it — damage that heals itself in the safe direction. A missing row or an unparseable
    neighbour list does the opposite: it silently sends a seed down the live-query path this whole
    layer exists to avoid, and nothing reports that it happened.
    """
    before = knn_cache_snapshot(db)
    try:
        yield before
    finally:
        damage = knn_cache_repair(db, before)
        check("the k-NN cache is left as it was found", not damage,
              "; ".join(damage) + " — repaired, but a run killed here would not have been"
              if damage else f"{len(before)} row(s)")


# ---- synthetic suggested links (reversible) ---------------------------------
SYNTHETIC_RATIONALE = "indexia-tests: synthetic edge, safe to delete"


def _linked_pairs(db):
    """Every note pair joined by a BINDS edge, in both directions."""
    out = set()
    for r in notelib.rows(db.query("SELECT outV().id AS a, inV().id AS b FROM BINDS")):
        a, b = r.get("a"), r.get("b")
        if a and b:
            out.add((a, b))
            out.add((b, a))
    return out


@contextlib.contextmanager
def synthetic_links(db, count, age_days=None):
    """Stage `count` suggested BINDS edges between currently-unlinked notes; delete them on exit.

    Goes through `LinkManager.suggest`, so the edges are indistinguishable from real ones (including
    the `created_at` stamp — which is the point when testing expiry). `age_days` back-dates them so
    an age-based sweep can be exercised without waiting a month. Yields the list of (a, b) pairs.

    Pairs are chosen by walking a widening index gap over the note ids, so the choice is
    deterministic and does not depend on which notes happen to be linked today.
    """
    ids = [r["id"] for r in notelib.rows(db.query(
        "SELECT id FROM Note WHERE status = 'active' ORDER BY id")) if r.get("id")]
    taken = _linked_pairs(db)
    lm = notelib.LinkManager(db)
    made = []
    try:
        for gap in range(len(ids) // 2, 0, -1):
            for i in range(len(ids) - gap):
                if len(made) >= count:
                    break
                a, b = ids[i], ids[i + gap]
                if (a, b) in taken:
                    continue
                try:
                    lm.suggest(a, b, rationale=SYNTHETIC_RATIONALE)
                except (notelib.LinkExists, ValueError):
                    continue
                taken.add((a, b))
                taken.add((b, a))
                made.append((a, b))
            if len(made) >= count:
                break
        if age_days is not None:
            when = notelib.format_created_at(notelib.now_utc() - timedelta(days=age_days))
            for a, b in made:
                db.command("UPDATE BINDS SET created_at = :at "
                           "WHERE outV().id = :a AND inV().id = :b", {"at": when, "a": a, "b": b})
        yield made
    finally:
        for a, b in made:
            db.command("DELETE FROM BINDS WHERE outV().id = :a AND inV().id = :b",
                       {"a": a, "b": b})


def undate(db, pairs):
    """Null out created_at on specific edges — for testing that undated edges are never swept."""
    for a, b in pairs:
        db.command("UPDATE BINDS SET created_at = null "
                   "WHERE outV().id = :a AND inV().id = :b", {"a": a, "b": b})
