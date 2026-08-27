#!/usr/bin/env python3
"""provocation_digest._stage_order — which candidates reach the ratification queue, in what order.

Worth pinning down in a unit test because the live corpus cannot exercise it: it yields no move-2
candidates at all (every same-day note is either already linked or too similar to clear move 2's
max_score), so the interleave is invisible in integration. The subtle invariant is that moves 1 and 2
score on **opposite axes** — move 1's score is similarity (high = nearer = better), move 2's is the
same cosine read as distance (low = further apart = better) — so a single ranking or a shared floor
would silently silence move 2. No database needed.
"""
import lib

pd = __import__("provocation_digest")
check = lib.check


def cand(cid, score):
    return {"id": cid, "score": score}


def ids(order):
    return [c["id"] for _seed, c in order]


# --- the floor applies to move 1 only ---------------------------------------------------------
order = pd._stage_order([{"seed": "S1",
                          "move1": [cand("a", 0.90), cand("b", 0.72), cand("c", 0.40)],
                          "move2": [cand("x", 0.10), cand("y", 0.45)]}], 0.70)
check("a move-1 candidate below the floor is dropped; move-2 is never floored",
      ids(order) == ["a", "x", "b", "y"], str(ids(order)))

# --- each move ranked on its own axis ---------------------------------------------------------
order = pd._stage_order([{"seed": "S1",
                          "move1": [cand("m1lo", 0.75), cand("m1hi", 0.95)],
                          "move2": [cand("m2far", 0.05), cand("m2near", 0.49)]}], 0.70)
check("move 1 ranks similarity descending, move 2 ranks distance ascending",
      ids(order) == ["m1hi", "m2far", "m1lo", "m2near"], str(ids(order)))

# --- one move running dry must not truncate the other -----------------------------------------
order = pd._stage_order([{"seed": "S1",
                          "move1": [cand("a", 0.9), cand("b", 0.8), cand("c", 0.75)],
                          "move2": [cand("x", 0.2)]}], 0.70)
check("move 2 running dry does not cut move 1 short",
      ids(order) == ["a", "x", "b", "c"], str(ids(order)))

# --- a shared cap splits between the moves rather than starving one ---------------------------
order = pd._stage_order([{"seed": "S1",
                          "move1": [cand(f"m1_{i}", 0.9 - i / 100) for i in range(10)],
                          "move2": [cand(f"m2_{i}", i / 100) for i in range(10)]}], 0.70)
check("under a cap of 4 both moves get 2 — neither crowds the other out",
      ids(order)[:4] == ["m1_0", "m2_0", "m1_1", "m2_1"], str(ids(order)[:4]))

# --- candidates compete on score across seeds, not in seed order ------------------------------
order = pd._stage_order([{"seed": "S1", "move1": [cand("weak", 0.71)], "move2": []},
                         {"seed": "S2", "move1": [cand("strong", 0.99)], "move2": []}], 0.70)
check("the strongest candidate wins across seeds",
      [(s, c["id"]) for s, c in order] == [("S2", "strong"), ("S1", "weak")], str(order))

# --- degenerate inputs -------------------------------------------------------------------------
check("no seeds offers nothing", pd._stage_order([], 0.70) == [])
check("everything below the floor offers nothing",
      pd._stage_order([{"seed": "S1", "move1": [cand("a", 0.1)], "move2": []}], 0.70) == [])

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
