#!/usr/bin/env python3
"""The analytics metrics, as pure arithmetic — no database.

Two of the corpus's claims are conventions rather than data, so they are pinned here where a
live corpus cannot reach them:

* `fitness.score`             — catalyzes credits, inhibits debits, untyped is worth nothing,
                                lineage is purely positive, and the floor holds.
* `autocatalysis.autocatalytic_core` — autocatalysis as reciprocity: a cycle in the catalysis
                                relation, which a pure BEGETS chain can never have.
* `debt.score` / `debt.rank`  — lineage over attention (move 7): the two halves are read off
                                different relations, and a chain of neglected notes collapses to
                                its best member.

v0.8.0 dropped a third (`_propagate_deltas`, the activation wave) along with the wave itself: the
sign now lives on `BINDS.mode` and is read where it matters instead of accumulating onto notes.
What replaced its per-note number is `visited`, which is a count of walks rather than a derived
quantity — there is no arithmetic convention left to pin, only the walk-recording behaviour, and
that is covered live in test_walk_ops.py.
"""
import os
import sys

import lib
import notelib

sys.path.insert(0, os.path.join(lib.REPO, "scripts"))
from analytics import autocatalysis, common, debt, fitness  # noqa: E402

check = lib.check
core = autocatalysis.autocatalytic_core


# --- fitness: sign lives on BINDS.mode ---------------------------------------------------------
base = fitness.score()
check("an unbound, childless, unwalked note sits exactly on the floor", base == 0.0, str(base))

check("an inbound ratified BINDS{catalyzes} credits the target", fitness.score(catalyzes=3) == 3.0)
check("an inbound ratified BINDS{inhibits} debits the target",
      fitness.score(catalyzes=5, inhibits=2) == 3.0)
check("catalyzes and inhibits cancel exactly at equal weight",
      fitness.score(catalyzes=4, inhibits=4) == 0.0)
check("an untyped bind is worth nothing (it is not a verdict)",
      fitness.score(catalyzes=0, inhibits=0, descendants=2)
      == fitness.score(descendants=2) == 2.0)

check("BEGETS descendants are purely positive — lineage is never a verdict",
      fitness.score(descendants=7) == 7.0)
check("walks through a note credit it", fitness.score(visited=4) == 4.0)
check("visited is the only non-relational term, and it is additive with the rest",
      fitness.score(catalyzes=2, descendants=1, visited=3) == 6.0)

check("the floor holds: heavy inhibition cannot sink a note below it",
      fitness.score(inhibits=99) == fitness.FLOOR)
check("a raised floor is respected", fitness.score(inhibits=99, floor=0.5) == 0.5)
check("weights are honoured",
      fitness.score(catalyzes=2, inhibits=1, w_catalyzes=3.0, w_inhibits=0.5) == 5.5)
check("the visited weight is honoured too",
      fitness.score(visited=3, w_visited=0.5) == 1.5)

check("bind_sign maps the vocabulary to +1 / -1 / 0",
      (notelib.bind_sign("catalyzes"), notelib.bind_sign("inhibits"), notelib.bind_sign(None))
      == (1, -1, 0))


# --- autocatalysis as reciprocity ---------------------------------------------------------------
# BEGETS is a DAG, so a pure lineage can never close on itself, however long it is.
chain = [("a", "b"), ("b", "c"), ("c", "d")]
check("a pure BEGETS chain is not autocatalytic — descent is not closure",
      core(chain, {"a", "b", "c", "d"}) == [])

check("a one-way BINDS{catalyzes} across chains is still not closure",
      core(chain + [("d", "a2")], {"a", "b", "c", "d", "a2"}) == [])

# The minimal autocatalytic set: two chains that catalyze each other.
mutual = [("a", "b"), ("x", "y"),          # two BEGETS chains
          ("b", "x"), ("y", "a")]          # ratified BINDS{catalyzes} both ways
found = core(mutual, {"a", "b", "x", "y"})
check("reciprocal BINDS{catalyzes} closes two chains into an autocatalytic set",
      found == ["a", "b", "x", "y"], str(found))

check("the minimal case is a mutual pair", core([("a", "b"), ("b", "a")], {"a", "b"}) == ["a", "b"])
check("a self-loop is not a set (an edge to itself is ignored)", core([("a", "a")], {"a"}) == [])
check("closure is judged on the INDUCED subgraph — a cycle leaving the group doesn't count",
      core([("a", "b"), ("b", "out"), ("out", "a")], {"a", "b"}) == [])

# Two disjoint cycles plus loose members: the larger cycle is the reported core.
two = [("a", "b"), ("b", "a"),
       ("p", "q"), ("q", "r"), ("r", "p"),
       ("z", "a")]
check("the largest cycle is reported as the catalytic core",
      core(two, {"a", "b", "p", "q", "r", "z"}) == ["p", "q", "r"],
      str(core(two, {"a", "b", "p", "q", "r", "z"})))

check("a group of one cannot be autocatalytic", core([("a", "a")], {"a"}) == [])
check("an edgeless group is not autocatalytic", core([], {"a", "b", "c"}) == [])

# A long cycle exercises the iterative Tarjan (a recursive one would be the risk at corpus scale).
ring = [(str(i), str((i + 1) % 500)) for i in range(500)]
check("a 500-node cycle is found without recursing",
      len(core(ring, {str(i) for i in range(500)})) == 500)


# --- structural debt: lineage over attention (move 7) -------------------------------------------
# The convention being pinned is that the two halves of the ratio are read off DIFFERENT relations
# — descendants from BEGETS (free, automatic) over binds + walks (expensive, human). Measure both
# on the link graph and "important but under-linked" becomes unsatisfiable.
check("a note with no descendants owes nothing, however neglected", debt.score() == 0.0)
check("an unattended note's debt is just its descendant count", debt.score(descendants=6) == 6.0)
check("a bind is attention and halves an unattended note's debt",
      debt.score(descendants=6, binds=1) == 3.0)
check("a walk is attention on the same footing as a bind",
      debt.score(descendants=6, visited=1) == debt.score(descendants=6, binds=1) == 3.0)
check("attention accumulates across both relations",
      debt.score(descendants=6, binds=1, visited=2) == 1.5)
check("a well-attended root self-cancels toward zero",
      debt.score(descendants=6, binds=60) < 0.1)
check("descendants and attention are independent axes — equal ratios tie",
      debt.score(descendants=4, binds=1) == debt.score(descendants=8, binds=3) == 2.0)

# The floor. A note with one child and no binds scores 1.0, which says nothing about neglect.
rows = [{"id": "a", "descendants": 2, "binds": 0, "visited": 0},
        {"id": "b", "descendants": 3, "binds": 0, "visited": 0}]
kept = debt.rank(rows, {})
check("the descendant floor drops notes too shallow to be owed anything",
      [r["id"] for r in kept] == ["b"], str([r["id"] for r in kept]))
check("a lowered floor lets them back in", len(debt.rank(rows, {}, min_descendants=2)) == 2)

# Ranking and determinism.
spread = [{"id": "low", "descendants": 4, "binds": 9, "visited": 0},
          {"id": "high", "descendants": 9, "binds": 0, "visited": 0},
          {"id": "mid", "descendants": 5, "binds": 1, "visited": 0}]
check("candidates are ordered by debt, richest first",
      [r["id"] for r in debt.rank(spread, {})] == ["high", "mid", "low"])
check("the attention term is reported alongside the score",
      debt.rank(spread, {})[0]["attention"] == 0)
tied = [{"id": "b2", "descendants": 4, "binds": 0, "visited": 0},
        {"id": "a1", "descendants": 4, "binds": 0, "visited": 0}]
check("ties break by id, so two runs over an unchanged graph agree",
      [r["id"] for r in debt.rank(tied, {})] == ["a1", "b2"])
check("the cap is honoured", len(debt.rank(spread, {}, limit=2)) == 2)

# Lineage de-duplication — the one constraint this metric actually needs. Neglect is inherited, so
# without this the whole list is one unbound chain rendered five ways.
chain_rows = [{"id": "root", "descendants": 9, "binds": 0, "visited": 0},
              {"id": "kid", "descendants": 8, "binds": 0, "visited": 0},
              {"id": "other", "descendants": 4, "binds": 0, "visited": 0}]
lineage = {"kid": "root"}            # {child: parent}
kept = [r["id"] for r in debt.rank(chain_rows, lineage)]
check("a parent and its descendant never both appear — only the higher scorer survives",
      kept == ["root", "other"], str(kept))

# ...and it must hold in both directions: the better score can sit at either end of the chain.
inverted = [{"id": "root", "descendants": 4, "binds": 0, "visited": 0},
            {"id": "kid", "descendants": 9, "binds": 0, "visited": 0}]
kept = [r["id"] for r in debt.rank(inverted, lineage)]
check("de-duplication works upward too — a child outscoring its parent suppresses the parent",
      kept == ["kid"], str(kept))

# Collision is by lineage, not by distance: an ancestor many hops up still suppresses.
deep = {"c1": "root", "c2": "c1", "c3": "c2"}
deep_rows = [{"id": "root", "descendants": 9, "binds": 0, "visited": 0},
             {"id": "c3", "descendants": 8, "binds": 0, "visited": 0}]
check("a distant ancestor suppresses just as a direct parent does",
      [r["id"] for r in debt.rank(deep_rows, deep)] == ["root"])
check("unrelated notes never suppress each other",
      len(debt.rank([{"id": "x", "descendants": 5, "binds": 0, "visited": 0},
                     {"id": "y", "descendants": 5, "binds": 0, "visited": 0}], {})) == 2)

# Lineage is a forest, so this cannot happen — but a malformed restore must not hang the digest.
check("a cyclic parent map terminates instead of spinning, and never lists the note itself",
      debt._ancestors({"a": "b", "b": "a"}, "a") == ["b"])

# The rendered line: states the fact, and never the score (a displayed number is one to move).
zero = {"id": "n", "label": "Legibility", "descendants": 12, "binds": 0, "visited": 0,
        "attention": 0, "debt": 12.0}
check("the unattended template names the count and stops",
      debt.prompt(zero) == '12 notes descend from "Legibility". You have not been back.',
      debt.prompt(zero))
check("the prompt never prints the score", "12.0" not in debt.prompt(zero))
check("one act of attention reads as 'once', not '1 times'",
      debt.prompt(dict(zero, attention=1)).endswith("You have been back once."),
      debt.prompt(dict(zero, attention=1)))
check("several read as a count",
      debt.prompt(dict(zero, attention=3)).endswith("You have been back 3 times."))
check("the title appears verbatim", 'from "Legibility"' in debt.prompt(zero))


# --- debt.report: the glue between the Corpus and the ranking ------------------------------------
# `rank` above is pure, but the wiring around it is where the silent bugs live — above all the
# orientation of the parent map, which would invert de-duplication without changing any count.
# So: a real Corpus with its three reads bypassed. Every method exercised here is the shipping one.
def corpus_of(notes, begets=(), binds=()):
    """A Corpus with hand-built data and no database — real methods, no SQL."""
    c = object.__new__(common.Corpus)
    c.db, c.as_of, c._cutoff = None, None, None
    c.notes = {n["id"]: n for n in notes}
    c.ids = set(c.notes)
    c.begets, c.binds = list(begets), list(binds)
    return c


def note(nid, title=None, body="", visited=0, status="active"):
    return {"id": nid, "title": title, "body": body, "visited": visited, "status": status}


#   r ──> k1 ──> g1        s ──> s1, s2, s3        p (status: proposed) ──> p1, p2, p3
#     └─> k2
graph = corpus_of(
    notes=[note("r", "Root"), note("k1", "Kid"), note("k2", "Other kid"), note("g1", "Grandkid"),
           note("s", body="An untitled note whose label falls back to its body."),
           note("s1"), note("s2"), note("s3"),
           note("p", "Proposed", status="proposed"), note("p1"), note("p2"), note("p3")],
    begets=[("r", "k1"), ("r", "k2"), ("k1", "g1"),
            ("s", "s1"), ("s", "s2"), ("s", "s3"),
            ("p", "p1"), ("p", "p2"), ("p", "p3")],
    binds=[("s", "s1", None, "ratified"), ("x", "s", "catalyzes", "ratified"),
           ("r", "k2", None, "suggested")])

owed = debt.report(graph)
check("report ranks the neglected root above the attended one",
      [r["id"] for r in owed] == ["r", "s"], str([r["id"] for r in owed]))
check("descendants are counted transitively, not just direct children",
      owed[0]["descendants"] == 3, str(owed[0]["descendants"]))
check("an unattended root's debt is its descendant count", owed[0]["debt"] == 3.0)
check("ratified binds count as attention in either direction",
      owed[1]["binds"] == 2 and owed[1]["debt"] == 1.0, str(owed[1]))
check("a SUGGESTED bind is not attention — the digest's own output cannot cancel the debt",
      owed[0]["binds"] == 0, str(owed[0]["binds"]))
check("proposed notes are excluded — they are not yet corpus",
      "p" not in [r["id"] for r in owed])
check("an untitled note falls back to a snippet of its body",
      owed[1]["label"].startswith("An untitled note"), owed[1]["label"])
check("the parent map is built child->parent, so de-duplication suppresses downward",
      "k1" not in [r["id"] for r in debt.report(graph, min_descendants=1)])
check("an empty corpus is diagnosed, not reported as 'nothing owed'",
      "empty" in debt.diagnosis(corpus_of([])))
check("a corpus with no lineage at all says so rather than going quiet",
      "no note has a BEGETS parent" in debt.diagnosis(corpus_of([note("a"), note("b")])))
check("a corpus with lineage but nothing owed says that instead",
      "nothing is owed" in debt.diagnosis(graph))


lib.report_and_exit()
