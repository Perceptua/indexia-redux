#!/usr/bin/env python3
"""provocation_digest._stage_order — which candidates reach the ratification queue, in what order.

Since move 2 (temporally adjacent, otherwise distant) was removed, staging is single-axis: move 1's
score is similarity (high = nearer = better), so candidates are simply ranked by score descending
across all seeds, floored at min_score, once per pair. `_stage` then spends the budget, at most
STAGE_PER_NOTE per note. No database needed.
"""
import lib

pd = __import__("provocation_digest")
check = lib.check


def cand(cid, score):
    return {"id": cid, "score": score}


def ids(order):
    return [c["id"] for _seed, c in order]


# --- the floor applies to move 1 -------------------------------------------------------------
order = pd._stage_order([{"seed": "S1",
                          "move1": [cand("a", 0.90), cand("b", 0.72), cand("c", 0.40)]}], 0.70)
check("a candidate below the floor is dropped",
      ids(order) == ["a", "b"], str(ids(order)))

# --- ranked by similarity descending -----------------------------------------------------------
order = pd._stage_order([{"seed": "S1",
                          "move1": [cand("lo", 0.75), cand("hi", 0.95)]}], 0.70)
check("ranks similarity descending", ids(order) == ["hi", "lo"], str(ids(order)))

# --- candidates compete on score across seeds, not in seed order ------------------------------
order = pd._stage_order([{"seed": "S1", "move1": [cand("weak", 0.71)]},
                         {"seed": "S2", "move1": [cand("strong", 0.99)]}], 0.70)
check("the strongest candidate wins across seeds",
      [(s, c["id"]) for s, c in order] == [("S2", "strong"), ("S1", "weak")], str(order))

# --- degenerate inputs -------------------------------------------------------------------------
check("no seeds offers nothing", pd._stage_order([], 0.70) == [])
check("everything below the floor offers nothing",
      pd._stage_order([{"seed": "S1", "move1": [cand("a", 0.1)]}], 0.70) == [])

# --- a pair is offered once, whichever note found it -------------------------------------------
# With the whole corpus seeding, most pairs arrive from both ends. Counting them twice would inflate
# "eligible", and the second sighting is what used to rely on LinkExists to go quiet.
order = pd._stage_order([{"seed": "NEW", "move1": [cand("OLD", 0.80)]},
                         {"seed": "OLD", "move1": [cand("NEW", 0.80)]}], 0.70)
check("a pair found from both ends is offered once",
      [(s, c["id"]) for s, c in order] == [("NEW", "OLD")], str(order))
order = pd._stage_order([{"seed": "NEW", "move1": [cand("OLD", 0.80)]},
                         {"seed": "OLD", "move1": [cand("NEW", 0.81)]}], 0.70)
check("the seed listed first leads, even if the index scored the reverse a hair higher — seeds run "
      "newest first, and the direction should not depend on rounding",
      [(s, c["id"]) for s, c in order] == [("NEW", "OLD")], str(order))
order = pd._stage_order([{"seed": "A", "move1": [cand("B", 0.69)]},
                         {"seed": "B", "move1": [cand("A", 0.70)]}], 0.70)
check("the floor is applied per sighting, so a pair straddling it is still offered",
      [(s, c["id"]) for s, c in order] == [("B", "A")], str(order))


# --- the per-note share: no note fills the batch ------------------------------------------------
def staging(pairs, budget, refused=(), per_note=1):
    """Run _stage over (seed, candidate id) pairs in order, with a suggest stub that refuses the
    pairs in `refused` as already linked. Returns (count, what was attempted, what landed)."""
    offered = [(s, cand(c, 0.9)) for s, c in pairs]
    attempted = []

    def suggest(seed, candidate):
        attempted.append((seed, candidate["id"]))
        if (seed, candidate["id"]) in refused:
            raise lib.notelib.LinkExists(seed, candidate["id"], "suggested")

    n = pd._stage(offered, budget, suggest, per_note=per_note)
    return n, attempted, [(s, c["id"]) for s, c in offered if c.get("staged")]


n, _, landed = staging([("hub", "x"), ("hub", "y"), ("z", "w")], 5)
check("a seed that has had its share gives way to the next pair",
      n == 2 and landed == [("hub", "x"), ("z", "w")], str(landed))
n, _, landed = staging([("x", "hub"), ("y", "hub"), ("z", "w")], 5)
check("the share counts a note on either end, not just as the seed",
      n == 2 and landed == [("x", "hub"), ("z", "w")], str(landed))
n, attempted, landed = staging([("hub", "x"), ("hub", "y")], 5, refused={("hub", "x")})
check("a pair that already has an edge costs its notes nothing, so the next one still lands",
      n == 1 and landed == [("hub", "y")] and len(attempted) == 2, str(landed))
n, attempted, _ = staging([("a", "b"), ("c", "d"), ("e", "f")], 2)
check("the budget still stops the walk, without trying a third",
      n == 2 and len(attempted) == 2, str(attempted))
n, attempted, _ = staging([("a", "b")], 0)
check("no budget attempts nothing", n == 0 and not attempted, str(attempted))
n, _, landed = staging([("hub", "x"), ("hub", "y"), ("hub", "z")], 5, per_note=2)
check("a larger share admits that many and no more",
      n == 2 and landed == [("hub", "x"), ("hub", "y")], str(landed))

# --- the queue ceiling: how much this run may stage -------------------------------------------
# The cap is a rate and the ceiling is a level, and only the second bounds the queue. These are
# the cases the live corpus will not be in on any given night, which is the whole reason
# stage_budget is a pure function of three numbers.
CEILING = 50
check("an empty queue leaves the per-run cap in charge",
      pd.stage_budget(0, 10, CEILING) == 10)
check("a queue with more room than the cap still leaves the cap in charge",
      pd.stage_budget(20, 10, CEILING) == 10)
check("a queue near the ceiling is topped up exactly to it, not past it",
      pd.stage_budget(45, 10, CEILING) == 5)
check("a queue exactly at the ceiling stages nothing",
      pd.stage_budget(CEILING, 10, CEILING) == 0)
check("a queue already over the ceiling stages nothing, and never a negative budget — the state "
      "the corpus is actually in the first time this ships",
      pd.stage_budget(54, 10, CEILING) == 0)
check("one suggestion of room is one suggestion staged",
      pd.stage_budget(CEILING - 1, 10, CEILING) == 1)
check("a ceiling of zero switches staging off entirely, without switching the digest off",
      pd.stage_budget(0, 10, 0) == 0)
check("the budget never exceeds the cap, whatever the ceiling",
      all(pd.stage_budget(q, 10, 500) == 10 for q in (0, 1, 100, 400)))

# --- the shipped defaults are coherent --------------------------------------------------------
check("STAGE_CAP is a positive int", isinstance(pd.STAGE_CAP, int) and pd.STAGE_CAP > 0,
      f"{pd.STAGE_CAP}")
check("STAGE_PER_NOTE is a positive int",
      isinstance(pd.STAGE_PER_NOTE, int) and pd.STAGE_PER_NOTE > 0, f"{pd.STAGE_PER_NOTE}")
check("build() seeds from the whole corpus unless told otherwise",
      __import__("inspect").signature(pd.build).parameters["seeds_limit"].default is None)
check("STAGE_MIN_SCORE is a similarity in (0, 1)", 0.0 < pd.STAGE_MIN_SCORE < 1.0,
      f"{pd.STAGE_MIN_SCORE}")
check("the queue ceiling leaves room above the sweep's keep-min — inverted, a queue could "
      "neither be added to nor pruned",
      lib.notelib.SUGGESTION_KEEP_MIN < lib.notelib.SUGGESTION_MAX_QUEUE,
      f"keep-min {lib.notelib.SUGGESTION_KEEP_MIN}, "
      f"ceiling {lib.notelib.SUGGESTION_MAX_QUEUE}")
check("the ceiling admits more than one run's worth, or the cap would never bind",
      pd.STAGE_CAP < lib.notelib.SUGGESTION_MAX_QUEUE,
      f"cap {pd.STAGE_CAP}, ceiling {lib.notelib.SUGGESTION_MAX_QUEUE}")
check("build() defaults to the shipped ceiling rather than to no ceiling",
      __import__("inspect").signature(pd.build).parameters["max_queue"].default
      == lib.notelib.SUGGESTION_MAX_QUEUE)

lib.report_and_exit()
