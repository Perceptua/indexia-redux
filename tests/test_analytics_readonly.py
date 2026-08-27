#!/usr/bin/env python3
"""The analytics layer writes NOTHING. (spec §13)

This is the test that makes the operations/analytics split enforceable rather than aspirational.
Every report runs against the live corpus, and afterwards:

  * the corpus counts are unchanged (`corpus_guard`),
  * the `Op` count is unchanged — not even a "this report ran" entry,
  * `Note.visited` is unchanged in total, since only walk recording may move it.

The Op check is the one that matters most, because logging an Op is exactly the shortcut a future
change would reach for ("just record that the report ran"), and it is precisely the thing that
would make analytics part of the graph's history again.

Also checks that the analytics re-implementation of community detection agrees with the
operational one on the present-day graph — they are two code paths over the same algorithm (one
takes a Corpus so it can run --as-of, one takes the DB because the difference-engine needs it),
and a silent divergence would mean move 3 and the communities report describe different corpora.
"""
import os
import sys

import lib

sys.path.insert(0, os.path.join(lib.REPO, "scripts"))
import notelib  # noqa: E402
from analytics import autocatalysis, common, criticality, debt, fitness, walks  # noqa: E402

check = lib.check
db = notelib.Arcade()


def op_count():
    return notelib.first_row(db.query("SELECT count(*) AS n FROM Op")).get("n") or 0


def visited_total():
    return notelib.first_row(
        db.query("SELECT sum(visited) AS n FROM Note")).get("n") or 0


lib.require(db, "the analytics read-only guarantee", notes=1)

ops_before, visits_before = op_count(), visited_total()

with lib.corpus_guard(db):
    corpus = common.Corpus(db)
    check("the corpus reads back", len(corpus.notes) > 0, f"{len(corpus.notes)} note(s)")

    rows = fitness.report(corpus)
    check("fitness reports every note", len(rows) == len(corpus.notes), f"{len(rows)} row(s)")
    check("fitness is sorted highest-first",
          all(rows[i]["fitness"] >= rows[i + 1]["fitness"] for i in range(len(rows) - 1)))
    check("fitness never sinks below the floor",
          all(r["fitness"] >= fitness.FLOOR for r in rows))

    crit = criticality.report(corpus)
    check("criticality names a band", crit["band"] in ("sparse", "critical", "dense"),
          f"{crit['band']} @ mean degree {crit['mean_degree']}")

    clusters = autocatalysis.report(corpus)
    n_auto = sum(1 for c in clusters if c["autocatalytic"])
    check("communities are detected", isinstance(clusters, list),
          f"{len(clusters)} community/ies, {n_auto} autocatalytic")
    check("every community has a hub in the corpus",
          all(c["hub"] in corpus.ids for c in clusters))
    check("an autocatalytic core is always a subset of its own members",
          all(set(c["autocatalytic_core"]) <= set(c["members"]) for c in clusters))
    check("autocatalytic is exactly 'the core is non-empty'",
          all(c["autocatalytic"] == bool(c["autocatalytic_core"]) for c in clusters))

    owed = debt.report(corpus)
    check("debt returns no more than its cap", len(owed) <= debt.K, f"{len(owed)} prompt(s)")
    check("every debt candidate clears the descendant floor",
          all(r["descendants"] >= debt.MIN_DESCENDANTS for r in owed))
    check("debt is sorted richest-first",
          all(owed[i]["debt"] >= owed[i + 1]["debt"] for i in range(len(owed) - 1)))
    check("every debt candidate is a note in the corpus", all(r["id"] in corpus.ids for r in owed))
    # The de-duplication rule, asserted against the real lineage rather than a synthetic one.
    parent = {child: par for par, child in corpus.begets}
    check("no debt candidate is an ancestor of another",
          not any(a["id"] in set(debt._ancestors(parent, b["id"]))
                  for a in owed for b in owed if a["id"] != b["id"]))
    check("an empty debt list always says why",
          bool(owed) or bool(debt.diagnosis(corpus)), debt.diagnosis(corpus) if not owed else "")

    visit_rows = walks.visits(corpus)
    check("visited reports every note", len(visit_rows) == len(corpus.notes))
    found = walks.listing(db)
    check("walks list without error", isinstance(found, list), f"{len(found)} walk(s)")

    # The two community-detection code paths must agree on today's graph.
    check("analytics and operational community detection agree",
          common.detect_communities(corpus) == notelib.detect_communities(db))

    # --as-of: a cutoff before the corpus existed must yield an empty graph, and one in the
    # future must reproduce the present. Anything in between is a real historical view.
    empty = common.Corpus(db, as_of="20000101T000000000Z")
    check("as-of before the corpus began sees nothing", not empty.notes and not empty.begets)
    future = common.Corpus(db, as_of="20991231T235959999Z")
    check("as-of in the future reproduces the present graph",
          len(future.notes) == len(corpus.notes) and len(future.begets) == len(corpus.begets),
          f"{len(future.notes)} notes / {len(future.begets)} begets")

ops_after, visits_after = op_count(), visited_total()
check("no Op was written — analytics leave no trace in the log at all",
      ops_after == ops_before, f"{ops_before} -> {ops_after}")
check("Note.visited is untouched — only walk recording may move it",
      visits_after == visits_before, f"{visits_before} -> {visits_after}")

lib.report_and_exit()
