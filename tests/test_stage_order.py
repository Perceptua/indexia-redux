#!/usr/bin/env python3
"""provocation_digest._stage_order — which candidates reach the ratification queue, in what order.

Since move 2 (temporally adjacent, otherwise distant) was removed, staging is single-axis: move 1's
score is similarity (high = nearer = better), so candidates are simply ranked by score descending
across all seeds, floored at min_score. No database needed.
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
