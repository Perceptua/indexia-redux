#!/usr/bin/env python3
"""scheduler.next_run — reading the cadence rules backwards, so a panel can say "next: 02:00".

`_due` answers "now?" and `next_run` answers "when?", and the only thing that makes the second
trustworthy is that it agrees with the first everywhere rather than in the cases somebody thought
to check. So the bulk of this is three properties swept over every hour of two weeks:

    next_run(kind, last, now) >= now                          it never points into the past
    _due(kind, last, now)  ==  (next_run(...) == now)          "due" and "due now" are one claim
    _due(kind, last, next_run(kind, last, now))                the instant it names really is due

The first form tried here was `_due(...) == (next_run(kind, last) <= now)` — next_run without a
`now` at all — and the grid refused it, correctly. **`_due` is not monotonic in time.** Its
nightly test is `now.date() > last.date() and now.hour >= 2`, so a job that ran Monday is due at
02:00 Tuesday, stays due through Tuesday, and goes *un*-due at midnight, because Wednesday 00:30
clears the date half and fails the hour half. There is therefore no instant after which the job
is simply due, and a function returning one would have to be lying somewhere. Between midnight
and 02:00 UTC no nightly or weekly job is due at all.

No database, no daemon, no state file.
"""
from datetime import datetime, timedelta, timezone

import lib

scheduler = __import__("scheduler")
check = lib.check

KINDS = ("hourly", "nightly", "weekly")
UTC = timezone.utc


def at(day, hour, minute=0):
    return datetime(2026, 7, day, hour, minute, tzinfo=UTC)


# ---- the properties, over a grid --------------------------------------------
# Every hour of two weeks against several last-run instants, including the awkward ones: just
# before the nightly hour, exactly on it, just after, and late at night.
lasts = [at(1, 0, 30), at(1, 2), at(1, 3), at(1, 23, 30), at(8, 1, 59)]
nows = [at(1, 0) + timedelta(hours=h) for h in range(0, 24 * 16)]

# The same grid is swept again with a failure backoff in force, because backoff is the one thing
# that can make a job un-due at an instant its cadence calls due — exactly the shape that broke
# the first attempt at next_run. `None` is the no-backoff pass.
blocks = [None, at(1, 1), at(2, 1, 30), at(3, 0, 45), at(9, 5)]

backwards, disagree, unfulfilled, cells = [], [], [], 0
for kind in KINDS:
    for last in lasts:
        for blocked in blocks:
            for now in nows:
                if now < last:
                    continue
                cells += 1
                where = (kind, last.isoformat(), now.isoformat(),
                         blocked.isoformat() if blocked else None)
                nxt = scheduler.next_run(kind, last, now, blocked)
                if nxt < now:
                    backwards.append(where + (nxt.isoformat(),))
                if scheduler._due(kind, last, now, blocked) != (nxt == now):
                    disagree.append(where + (nxt.isoformat(),))
                if not scheduler._due(kind, last, nxt, blocked):
                    unfulfilled.append(where + (nxt.isoformat(),))

check("next_run never points into the past", not backwards,
      f"{cells} cells" if not backwards else f"{len(backwards)}, first {backwards[0]}")
check("'due' and 'the next run is now' are the same claim, on every hour of two weeks",
      not disagree, f"{cells} cells" if not disagree else f"{len(disagree)}, first {disagree[0]}")
check("and the instant next_run names really is one _due accepts — an answer nobody can act on "
      "would satisfy the other two properties and still be useless",
      not unfulfilled,
      f"{cells} cells" if not unfulfilled else f"{len(unfulfilled)}, first {unfulfilled[0]}")

# ---- the non-monotonicity, said out loud ------------------------------------
# This is the shape that made `next_run(kind, last)` impossible to write, so it is worth a check
# of its own rather than living only in the grid.
mon = at(1, 12)
check("a nightly job is due through the day after it ran",
      scheduler._due("nightly", mon, at(2, 2)) and scheduler._due("nightly", mon, at(2, 23)))
check("...and un-due again after midnight, because _due tests the date and the hour separately",
      not scheduler._due("nightly", mon, at(3, 0, 30)))
check("so between midnight and 02:00 the next run is 02:00 that same morning, not a stale time "
      "in the past", scheduler.next_run("nightly", mon, at(3, 0, 30)) == at(3, 2),
      str(scheduler.next_run("nightly", mon, at(3, 0, 30))))

# ---- the boundaries, said explicitly ----------------------------------------
# The grid would catch these, but a failure there reports a coordinate; these report a claim.
early = at(1, 0, 30)
check("a weekly job whose week elapses before 02:00 waits until 02:00 that day",
      scheduler.next_run("weekly", early, early) == at(8, 2),
      str(scheduler.next_run("weekly", early, early)))
check("a weekly job whose week elapses after 02:00 is due the moment the week is up, not at the "
      "next 02:00 — rounding up would hide it for another twenty-two hours",
      scheduler.next_run("weekly", at(1, 3), at(1, 3)) == at(8, 3),
      str(scheduler.next_run("weekly", at(1, 3), at(1, 3))))
check("a weekly job whose week elapses exactly at 02:00 is due then",
      scheduler.next_run("weekly", at(1, 2), at(1, 2)) == at(8, 2))

check("a nightly job is due at 02:00 the following day",
      scheduler.next_run("nightly", at(1, 14, 22), at(1, 14, 22)) == at(2, 2),
      str(scheduler.next_run("nightly", at(1, 14, 22), at(1, 14, 22))))
# _due compares dates, not elapsed time, so this really is three hours and not a day.
check("...including one that ran at 23:00, which _due makes due again three hours later",
      scheduler.next_run("nightly", at(1, 23), at(1, 23)) == at(2, 2),
      str(scheduler.next_run("nightly", at(1, 23), at(1, 23))))
check("an hourly job is due an hour later, with no hour-of-day condition",
      scheduler.next_run("hourly", at(1, 14, 22), at(1, 14, 22)) == at(1, 15, 22))

# ---- the answers that are not a future instant ------------------------------
check("a job that has never run is due now — the next tick takes it",
      all(scheduler.next_run(k, None, at(1, 0)) == at(1, 0) for k in KINDS))
check("...and _due agrees", all(scheduler._due(k, None, at(1, 0)) for k in KINDS))
check("a cadence this scheduler does not know is None rather than an exception — _due answers "
      "False for it, and neither should be a stack trace behind a panel",
      scheduler.next_run("fortnightly", at(1, 2), at(9, 3)) is None
      and scheduler._due("fortnightly", at(1, 2), at(9, 3)) is False)

# ---- last_run: the state file is a text file anyone can edit ----------------
check("a good stamp round-trips", scheduler.last_run({"j": at(1, 2).isoformat()}, "j") == at(1, 2))
check("a naive stamp is read as UTC — left naive it would raise TypeError against an aware now, "
      "out of the loop and through the route alike",
      scheduler.last_run({"j": "2026-07-01T02:00:00"}, "j") == at(1, 2))
for bad in ["", "yesterday", "2026-13-45T99:99:99", None, 17, []]:
    check(f"an unreadable stamp ({bad!r}) reads as no record of a run, not an exception",
          scheduler.last_run({"j": bad}, "j") is None)
check("a job absent from the state file has never run", scheduler.last_run({}, "j") is None)
check("and neither has one whose state file would not load at all",
      scheduler.last_run(None, "j") is None)

# ---- the failure backoff -----------------------------------------------------
# The grid above proves next_run and _due still agree under a backoff. These say what the backoff
# is *for*: a job that fails does not come straight back on the next tick.
ran = at(1, 12)
check("a backoff makes a job un-due at an instant its cadence calls due",
      scheduler._due("nightly", ran, at(2, 3))
      and not scheduler._due("nightly", ran, at(2, 3), blocked=at(2, 5)))
check("...and next_run then names the backoff instant, not the cadence's",
      scheduler.next_run("nightly", ran, at(2, 3), blocked=at(2, 5)) == at(2, 5))
check("a backoff expiring before the nightly hour does not make a nightly job due then — "
      "clamping the cadence answer instead of asking it afresh would have said 01:00",
      scheduler.next_run("nightly", ran, at(3, 0, 15), blocked=at(3, 1)) == at(3, 2),
      str(scheduler.next_run("nightly", ran, at(3, 0, 15), blocked=at(3, 1))))
check("a job that has never run still respects a backoff — otherwise one that fails on its first "
      "ever run has no last_dt to slow it down and retries forever",
      not scheduler._due("nightly", None, at(1, 3), blocked=at(1, 6))
      and scheduler.next_run("nightly", None, at(1, 3), blocked=at(1, 6)) == at(1, 6))
check("an expired backoff is no backoff", scheduler._due("nightly", ran, at(2, 9), blocked=at(2, 5)))

# The ledger, which like the last-run stamps is JSON anyone can hand-edit.
st = {}
first = scheduler.record_failure(st, "j", at(1, 2))
check("the first failure backs off by the base interval",
      first == at(1, 2) + timedelta(seconds=scheduler.FAIL_BACKOFF_BASE), str(first))
check("and blocked_until reads back what record_failure returned",
      scheduler.blocked_until(st, "j") == first)
second = scheduler.record_failure(st, "j", at(1, 3))
check("consecutive failures double the wait",
      second == at(1, 3) + timedelta(seconds=2 * scheduler.FAIL_BACKOFF_BASE), str(second))
for _ in range(20):
    scheduler.record_failure(st, "j", at(1, 4))
check("the wait is capped rather than growing without bound",
      scheduler.blocked_until(st, "j") == at(1, 4) + timedelta(seconds=scheduler.FAIL_BACKOFF_MAX))
scheduler.clear_failure(st, "j")
check("one success clears the record entirely, leaving no key behind",
      scheduler.blocked_until(st, "j") is None and scheduler.FAIL_KEY not in st, str(st))
check("a job with no failures is not backing off", scheduler.blocked_until({}, "j") is None)
for bad in [None, 17, [], "soon", {}, {"count": "many", "since": at(1, 2).isoformat()},
            {"count": 1, "since": "yesterday"}, {"since": at(1, 2).isoformat()}]:
    check(f"an unreadable ledger entry ({bad!r}) reads as not backing off, not an exception",
          scheduler.blocked_until({scheduler.FAIL_KEY: {"j": bad}}, "j") is None)
check("and neither a ledger that is not a mapping at all",
      scheduler.blocked_until({scheduler.FAIL_KEY: "corrupt"}, "j") is None)
check("the ledger key is reserved — the retired-job sweep must not mistake it for a job",
      scheduler.FAIL_KEY.startswith("_")
      and not any(j.name.startswith("_") for j in scheduler.JOBS))

# ---- the shipped job table is coherent --------------------------------------
check("every shipped job names a cadence next_run can answer for",
      all(job.kind in scheduler.KINDS for job in scheduler.JOBS),
      str(sorted({job.kind for job in scheduler.JOBS})))
check("every shipped job names a script that exists",
      all(__import__("os").path.isfile(__import__("os").path.join(scheduler.SCRIPTS, job.script))
          for job in scheduler.JOBS),
      str([job.script for job in scheduler.JOBS]))
check("every shipped job names a timeout that clears the embed-settle wait it may sit through "
      "first — a cap under that would kill the job for waiting, not for hanging",
      all(job.timeout > 600 for job in scheduler.JOBS),
      str({job.name: job.timeout for job in scheduler.JOBS}))
check("knn-cache is given more than the default, being the only O(N) job here",
      next(j for j in scheduler.JOBS if j.name == "knn-cache").timeout
      > scheduler.DEFAULT_TIMEOUT)

lib.report_and_exit()
