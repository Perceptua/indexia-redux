#!/usr/bin/env python3
"""Calibration tool (not a test): what do move-1 scores actually look like on this corpus?

The digest's staging floor (`provocation_digest.STAGE_MIN_SCORE`) only makes sense relative to the
scores a real corpus produces. Set it in the abstract and you get one of two failures: too low and the
queue fills with weak pairs, too high and move 1 stops proposing at all. This prints the distribution
and how many candidates each candidate floor would admit, so the constant can be chosen from data.

At ~100 notes this reported max 0.729 / median 0.644, which is why the shipped floor is 0.65 rather
than the 0.70 originally sketched — 0.70 admitted 4 of 55 candidates and made the floor, not the cap,
the binding constraint. Re-run it as the corpus grows.

  bash tests/tools/score_dist.sh
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "scripts"))

import notelib  # noqa: E402
import provocation_digest as pd  # noqa: E402

FLOORS = (0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50)


def report(label, scores, higher_is_better):
    if not scores:
        print(f"{label}: no candidates")
        return
    scores.sort(reverse=True)
    mid = scores[len(scores) // 2]
    print(f"{label}\n  n={len(scores)}  max={scores[0]:.3f}  median={mid:.3f}  min={scores[-1]:.3f}")
    for floor in FLOORS:
        kept = sum(1 for s in scores if (s >= floor if higher_is_better else s <= floor))
        bar = "#" * round(40 * kept / len(scores))
        print(f"    {'>=' if higher_is_better else '<='} {floor:.2f}: {kept:4d}  {bar}")


def main():
    db = notelib.Arcade()
    seeds = pd._seeds(db, 10)
    m1 = []
    for sid in seeds:
        try:
            m1 += [c["score"] for c in notelib.move1_candidates(db, sid, k=5)]
        except (ValueError, notelib.EmbedderError):
            pass
    print(f"{len(seeds)} seed(s); shipped floor = {pd.STAGE_MIN_SCORE}, "
          f"cap = {pd.STAGE_CAP}/run\n")
    report("move 1 — similarity (higher is nearer, the floor applies here)", m1, True)


if __name__ == "__main__":
    main()
