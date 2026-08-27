#!/usr/bin/env python3
"""`search --since/--until` day boundaries — the regression that made a closed range read as half-open.

`--since D --until D` used to return nothing, because the upper bound was exclusive of the day named.
The fix bumps the internal bound by one day; these checks pin the arithmetic, including the month
rollover that a naive string manipulation would get wrong.

Read-only. Derives its expectations from the id prefixes actually present, so it works on any corpus.
"""
import collections

import lib

notelib, check = lib.notelib, lib.check

db = notelib.Arcade()

with lib.corpus_guard(db):
    # how many notes per UTC day, straight from the ids (spec §4: the id *is* the timestamp)
    per_day = collections.Counter(
        (r.get("id") or "")[:8] for r in notelib.rows(db.query("SELECT id FROM Note")))
    per_day.pop("", None)
    if not per_day:
        lib.skip("date-range checks", "corpus has no notes")
        lib.report_and_exit()

    days = sorted(per_day)
    total = sum(per_day.values())

    def dashed(compact):
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"

    def n(**kw):
        return len(notelib.lexical_search(db, limit=10 ** 6, **kw))

    # --- a single day is a closed range -------------------------------------------------------
    busiest = max(days, key=lambda d: per_day[d])
    d = dashed(busiest)
    check("since D and until D returns exactly that day's notes",
          n(since=d, until=d) == per_day[busiest],
          f"{d}: got {n(since=d, until=d)}, expected {per_day[busiest]}")

    # --- two adjacent days ---------------------------------------------------------------------
    if len(days) >= 2:
        pairs = [(days[i], days[i + 1]) for i in range(len(days) - 1)]
        lo, hi = pairs[0]
        expected = per_day[lo] + per_day[hi]
        check("a two-day span includes both endpoints",
              n(since=dashed(lo), until=dashed(hi)) == expected,
              f"{dashed(lo)}..{dashed(hi)}: got {n(since=dashed(lo), until=dashed(hi))}, "
              f"expected {expected}")

    # --- an open-ended upper bound includes its own day ----------------------------------------
    first = days[0]
    check("until alone includes the day named",
          n(until=dashed(first)) == per_day[first],
          f"until {dashed(first)}: got {n(until=dashed(first))}, expected {per_day[first]}")

    # --- an open-ended lower bound --------------------------------------------------------------
    last = days[-1]
    check("since alone includes the day named",
          n(since=dashed(last)) == per_day[last],
          f"since {dashed(last)}: got {n(since=dashed(last))}, expected {per_day[last]}")

    # --- a range spanning everything, including a month rollover -------------------------------
    lo, hi = dashed(days[0]), dashed(days[-1])
    check("a range covering every day returns the whole corpus",
          n(since=lo, until=hi) == total, f"{lo}..{hi}: got {n(since=lo, until=hi)} of {total}")

    # month-end as the upper bound must roll into the next month, not overflow the day field
    y, m = days[-1][:4], days[-1][4:6]
    month_end = f"{y}-{m}-31" if m in {"01", "03", "05", "07", "08", "10", "12"} else \
                (f"{y}-{m}-30" if m != "02" else f"{y}-{m}-28")
    in_month = sum(v for k, v in per_day.items() if k[:6] == y + m)
    check("a month-end upper bound rolls over correctly",
          n(since=f"{y}-{m}-01", until=month_end) == in_month,
          f"{y}-{m}: got {n(since=f'{y}-{m}-01', until=month_end)}, expected {in_month}")

    # --- an empty range ------------------------------------------------------------------------
    check("an inverted range returns nothing",
          n(since=dashed(days[-1]), until=dashed(days[0])) == 0)

    # --- malformed input is still rejected ----------------------------------------------------
    for bad in ("2026-7-1", "20260701", "not-a-date", "2026-13-01", "2026-07-32"):
        try:
            notelib.lexical_search(db, until=bad, limit=1)
            ok = False
        except ValueError:
            ok = True
        check(f"a malformed --until is rejected ({bad!r})", ok)

    # An empty string means "no filter", not "malformed" — every filter in lexical_search is
    # applied under `if <value>:`, so "" is indistinguishable from omitting the argument.
    check("an empty --until is treated as absent rather than rejected",
          n(until="") == total, f"got {n(until='')} of {total}")

lib.report_and_exit()
