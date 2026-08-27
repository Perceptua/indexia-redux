#!/usr/bin/env python3
"""scheduler — the in-repo maintenance clock (spec §12.6).

Runs the difference-engine's maintenance jobs on a cadence, without an external cron. Mirrors the
embed-worker: a resilient loop (a job failure is logged and retried on the next tick, never
killing the loop); started by up.sh, stopped by down.sh. Cadences (nightly hour is UTC, matching
the id scheme §4):

  nightly ~02:00   knn-cache → provocation-digest
  weekly           resurface, link-expiry

Order matters for the nightly batch: refresh the k-NN cache first, so it absorbs the one
LSM_VECTOR rebuild, then build the digest, which reads the cache the first job just built.
Last-run state persists in ~/.indexia/scheduler-state.json so restarts don't double-fire. Prefer
OS cron instead? The scripts/*.sh wrappers are directly cron-usable — this daemon is the
zero-setup default. Managed by scripts/scheduler.sh.

**A failing job backs off; it does not hammer.** "Fail-open" means one bad run never kills the
loop, and that is right — but taken alone it also means a job that fails *every* time is retried
for as long as its cadence says it is due, and the nightly cadence says that from 02:00 until
midnight. For a job that fails by timing out, each retry costs the whole timeout. So a failure is
now recorded, and the job waits (15 min, doubling, capped at 6 h) before it is attempted again;
one success clears the record. See FAIL_BACKOFF_BASE and blocked_until.

**Every job here is an operation.** v0.8.0 removed four that were not: activation-decay,
fitness-recompute, community-detect and criticality-monitor existed to keep stored analytics from
going stale, and with nothing stored there is nothing to refresh. Those measurements did not
disappear — they moved to scripts/analytics.sh, which computes them on demand and writes nothing
(§13). The corpus is still watched at the edge of chaos; the watching is just done by whoever
runs `analytics.sh criticality`, and the loop closes through them (§11.3, §11.5).
"""
import argparse
import json
import os
import subprocess
import sys
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
STATE_DEFAULT = os.path.join(os.path.expanduser("~"), ".indexia", "scheduler-state.json")

HOUR, DAY, WEEK = 3600, 86400, 7 * 86400
NIGHTLY_HOUR_UTC = 2                       # prefer the daily batch just after 02:00 UTC
KINDS = ("hourly", "nightly", "weekly")    # the cadences _due and next_run both understand.
                                           # Nothing ships hourly; the arithmetic stays because
                                           # it is pinned by the grid test and costs nothing.

# How long a job may run before it is killed. This cap exists to catch a *hang*, not to bound
# cost — build_knn_cache commits in a single transaction at the end, so killing it at 99% throws
# the entire pass away and the next attempt starts from nothing.
DEFAULT_TIMEOUT = 1800.0

# knn-cache gets its own, far larger cap. It is O(N) round trips plus the one absorbed ANN
# rebuild, and the whole-job times recorded on its Ops are spread wildly — 133 s, 215 s, 663 s,
# 962 s, 1149 s — with the *slowest* of them being the 25-note run. What dominates is whether the
# vector index was cold, not how many notes there are, so a cap sized to a typical run is a cap
# that fires on an ordinary bad night. The job may also spend notelib.EMBED_SETTLE_TIMEOUT (600 s)
# waiting for the embed queue *inside* the same budget: 600 + 1149 = 1749 s, which left the old
# shared 1800 s cap fifty-one seconds of headroom.
KNN_CACHE_TIMEOUT = 4 * HOUR

# A failed job waits before it is tried again, instead of retrying on the next tick. Without this,
# a job that always fails runs every `interval` seconds — or, when it fails by timing out, every
# timeout + interval, which for knn-cache under the old cap worked out at roughly forty attempts a
# day, each one triggering a server-side ANN rebuild on a box that has no cores to spare. The
# cadence is no protection: _due stays true from 02:00 until midnight, a twenty-two-hour window.
FAIL_BACKOFF_BASE = 15 * 60.0              # wait this long after the first failure...
FAIL_BACKOFF_MAX = 6 * HOUR                # ...doubling each time, up to this
FAIL_KEY = "_failures"                     # reserved state key; '_'-prefixed keys are not jobs

# One row of the schedule. A namedtuple rather than a bare tuple because the table is unpacked in
# three places — here, the status panel in scripts/ui.py, and tests/test_scheduler_status.py — and
# widening a 4-tuple to carry `timeout` would have broken every one of them.
Job = namedtuple("Job", "name script kind args timeout", defaults=((), DEFAULT_TIMEOUT))

# Evaluated in order, so the nightly batch runs cache → digest.
JOBS = [
    Job("knn-cache", "knn_cache.py", "nightly", ["--if-stale", "--quiet"], KNN_CACHE_TIMEOUT),
    Job("provocation-digest", "provocation_digest.py", "nightly", []),
    Job("resurface", "resurface.py", "weekly", []),
    Job("link-expiry", "link_expiry.py", "weekly", []),
]


def _log(msg):
    """One log line, stamped UTC.

    A maintenance log without timestamps cannot be read after the fact, and the failure mode is
    not theoretical: a long-running daemon holds the JOBS list it loaded at start, so removing a
    job from this file does not stop the *running* process from failing it every tick. Those
    failures then sit at the tail of the log until the next restart, and whoever reads them next
    diagnoses a bug that was fixed days before the lines were written. That happened here — the
    four v0.8.0 jobs below outlived their deletion by a restart. A stamp makes the log say when.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[scheduler] {stamp} {msg}", flush=True)


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)                  # atomic


def _due(kind, last_dt, now, blocked=None):
    """Is a job of `kind` due? last_dt is None (never run) => always due.

    `blocked` is the instant a failing job may next be attempted (see blocked_until); before it,
    nothing is due whatever the cadence says. It is tested *first*, ahead of the never-run case,
    because a job that fails on its very first run has no last_dt to reason from and would
    otherwise retry every tick forever — which is the exact shape the backoff exists to stop.
    """
    if blocked is not None and now < blocked:
        return False
    if last_dt is None:
        return True
    if kind == "hourly":
        return (now - last_dt).total_seconds() >= HOUR
    if kind == "nightly":
        return now.date() > last_dt.date() and now.hour >= NIGHTLY_HOUR_UTC
    if kind == "weekly":
        return (now - last_dt).total_seconds() >= WEEK and now.hour >= NIGHTLY_HOUR_UTC
    return False


def next_run(kind, last_dt, now, blocked=None):
    """The next instant at which the loop would run this job — the earliest `t >= now` for which
    `_due(kind, last_dt, t)` holds. `now` when it is due already; None for a cadence this
    scheduler does not know.

    A failure backoff (`blocked`) is applied by **advancing `now`**, not by clamping the answer.
    The earliest instant this can run is the earliest one that is both past the backoff and due on
    its own cadence, and asking the cadence question from `blocked` rather than from `now` is
    exactly that. `max(answer, blocked)` would be wrong: a backoff expiring at 01:00 does not make
    a nightly job due at 01:00, it makes it due at 02:00.

    **This needs `now`, and that is not an accident of the signature.** `_due` is not monotonic
    in time: its nightly test is `now.date() > last.date() and now.hour >= 2`, so a job that ran
    on Monday becomes due at 02:00 Tuesday, stays due all Tuesday, and then goes *un*-due at
    midnight — because Wednesday 00:30 satisfies the date half and fails the hour half. It is due
    again at 02:00 Wednesday. So "the instant after which this is due" does not exist to be
    returned; only "the next instant at which it is due, from here" does. Between midnight and
    02:00 UTC no nightly or weekly job is due at all, and the panel says so rather than showing a
    time in the past.

    The weekly case is the other one worth writing down. `_due` needs BOTH a full week elapsed
    AND the hour past 02:00, so neither half alone answers: `last + 7d` under-reports by up to
    two hours when the week is up at 00:30, and rounding every answer to the next 02:00
    over-reports by up to twenty-two when the week is up at 03:00, which is already due.

    Pinned against `_due` over a two-week grid in tests/test_scheduler_status.py.
    """
    if kind not in KINDS:
        return None
    if blocked is not None and blocked > now:
        now = blocked                    # ...then answer the cadence question from there
    if last_dt is None or _due(kind, last_dt, now):
        return now                       # never run, or overdue: the next tick takes it
    if kind == "hourly":
        return max(last_dt + timedelta(seconds=HOUR), now)
    if kind == "nightly":
        # The earliest day whose date clears last_dt's, not earlier than today. We are here only
        # because it is not due yet, so if that day is today, today's 02:00 is still ahead.
        day = max(last_dt.date() + timedelta(days=1), now.date())
        return _at_nightly_hour(datetime(day.year, day.month, day.day, tzinfo=timezone.utc))
    soonest = max(last_dt + timedelta(seconds=WEEK), now)
    return soonest if soonest.hour >= NIGHTLY_HOUR_UTC else _at_nightly_hour(soonest)


def _at_nightly_hour(when):
    """02:00 UTC on `when`'s own day."""
    return when.replace(hour=NIGHTLY_HOUR_UTC, minute=0, second=0, microsecond=0)


def last_run(state, name):
    """The last-run instant for a job from a loaded state file, or None.

    Tolerant on purpose. The file is plain JSON in the user's home directory, so a hand-edit or a
    string written by an older version can leave a value `fromisoformat` refuses — or, worse, one
    it accepts and returns *naive*, which then raises TypeError the moment it meets an aware
    `now`. Both read here as "no record of it having run", which is what an unreadable stamp
    actually tells you.
    """
    raw = (state or {}).get(name)
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _failures(state):
    """The failure ledger from a loaded state file — {job: {"count": n, "since": iso}}."""
    ledger = (state or {}).get(FAIL_KEY)
    return ledger if isinstance(ledger, dict) else {}


def _backoff(count):
    """Seconds to wait after `count` consecutive failures: 15 min, doubling, capped at 6 h."""
    return min(FAIL_BACKOFF_BASE * 2 ** max(0, int(count) - 1), FAIL_BACKOFF_MAX)


def blocked_until(state, name):
    """The instant a failing job may next be attempted, or None if it is not backing off.

    Tolerant for the same reason `last_run` is: this is JSON in the user's home directory, and a
    hand-edit or an older version's shape must not raise out of the loop. An entry this cannot
    read means "not backing off" — the job simply runs, which is the behaviour from before the
    backoff existed and the safe direction to fail in.
    """
    rec = _failures(state).get(name)
    if not isinstance(rec, dict):
        return None
    try:
        since = datetime.fromisoformat(rec["since"])
        count = int(rec["count"])
    except (KeyError, TypeError, ValueError):
        return None
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    return since + timedelta(seconds=_backoff(count))


def record_failure(state, name, now):
    """Note a failed run in `state`; returns the instant the job may next be attempted."""
    ledger = dict(_failures(state))
    rec = ledger.get(name)
    try:
        count = int(rec.get("count") or 0) if isinstance(rec, dict) else 0
    except (TypeError, ValueError):
        count = 0
    ledger[name] = {"count": count + 1, "since": now.isoformat()}
    state[FAIL_KEY] = ledger
    return now + timedelta(seconds=_backoff(count + 1))


def clear_failure(state, name):
    """Forget a job's failures. Called on every success, so a backoff never outlives the trouble
    that caused it, and an empty ledger leaves no key behind in the state file."""
    ledger = dict(_failures(state))
    if ledger.pop(name, None) is None:
        return
    if ledger:
        state[FAIL_KEY] = ledger
    else:
        state.pop(FAIL_KEY, None)


def run_job(name, script, extra_args=(), timeout=DEFAULT_TIMEOUT):
    """Run a job script as a subprocess (inherits the exported env). Returns True on success."""
    path = os.path.join(SCRIPTS, script)
    try:
        r = subprocess.run([sys.executable, path, *extra_args],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Said plainly rather than as a repr, because the consequence is not obvious: the child is
        # killed where it stands, and a job that commits its work at the end has lost all of it.
        _log(f"{name} TIMED OUT after {timeout:.0f}s and was killed — anything it had not "
             f"committed is lost")
        return False
    except Exception as e:                 # subprocess — never kill the loop
        _log(f"{name} error: {e}")
        return False
    if r.returncode == 0:
        tail = (r.stdout or "").strip().splitlines()
        _log(f"{name}: {tail[-1] if tail else 'ok'}")
        return True
    _log(f"{name} FAILED (rc={r.returncode}): "
         f"{(r.stderr or r.stdout or '').strip()[:300]}")
    return False


def main():
    p = argparse.ArgumentParser(prog="scheduler",
                                description="In-repo homeostatic clock (spec §12.6).")
    p.add_argument("--interval", type=float, default=60.0, help="tick seconds (default 60)")
    p.add_argument("--state", default=STATE_DEFAULT, help="last-run state file")
    p.add_argument("--once", action="store_true", help="run currently-due jobs once and exit")
    p.add_argument("--force", action="store_true", help="run every job now regardless of cadence, then exit")
    args = p.parse_args()

    os.makedirs(os.path.dirname(args.state), exist_ok=True)
    _log(f"up — {len(JOBS)} jobs, tick {args.interval}s, state {args.state}")
    known = {job.name for job in JOBS}
    while True:
        now = datetime.now(timezone.utc)
        state = _load(args.state)
        # Forget jobs that no longer exist, rather than carrying their last-run times forever.
        # The v0.8.0 removals left four dead keys here for a year of ticks; a retired job should
        # take its bookkeeping with it, so the state file always describes the current JOBS.
        # '_'-prefixed keys are the scheduler's own (FAIL_KEY) and are never job names.
        retired = sorted(k for k in set(state) - known if not k.startswith("_"))
        if retired:
            _log("dropping state for retired job(s): " + ", ".join(retired))
            for name in retired:
                state.pop(name, None)
                clear_failure(state, name)
        for job in JOBS:
            # Through last_run rather than fromisoformat directly: a stamp this cannot parse used
            # to raise straight out of the loop and take the daemon with it, for a file anyone can
            # edit. Unreadable now means "no record of a run", so the job simply runs.
            last_dt = last_run(state, job.name)
            if args.force or _due(job.kind, last_dt, now, blocked_until(state, job.name)):
                ok = run_job(job.name, job.script, job.args, job.timeout)
                if ok:
                    state[job.name] = now.isoformat()
                    clear_failure(state, job.name)
                else:
                    if args.force:         # --force records the attempt either way
                        state[job.name] = now.isoformat()
                    retry = record_failure(state, job.name, now)
                    _log(f"{job.name} backing off until "
                         f"{retry.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        _save(args.state, state)
        if args.once or args.force:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
