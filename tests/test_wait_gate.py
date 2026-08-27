#!/usr/bin/env python3
"""notelib.wait_for_embeddings — the gate that lets a scheduled job query a corpus that has
stopped moving (adding one embedding invalidates the whole ANN index, so a job that interleaves
embedding with querying pays a rebuild per query).

The branch that matters most is the **fail-open timeout**: a stalled embed worker must degrade the
nightly batch's freshness, never stop it. Reproducing that against real data would mean deliberately
breaking the embedder, so `Ingestor` is stubbed with a scripted sequence of pending counts — the
control flow is what is under test. No database needed.
"""
import time

import lib

notelib, check = lib.notelib, lib.check


class FakeIngestor:
    """Stands in for Ingestor, reporting a scripted sequence of pending counts."""
    sequence = [0]
    calls = 0

    def __init__(self, db=None, embedder=None):
        pass

    def pending_note_ids(self, limit=0):
        i = min(FakeIngestor.calls, len(FakeIngestor.sequence) - 1)
        FakeIngestor.calls += 1
        return [None] * FakeIngestor.sequence[i]


def scripted(*counts):
    """Arm the stub with a sequence of pending counts; the last value repeats forever."""
    FakeIngestor.sequence, FakeIngestor.calls = list(counts), 0
    return []


real_ingestor = notelib.Ingestor
notelib.Ingestor = FakeIngestor
try:
    # --- already settled: returns at once, says nothing ---------------------------------------
    logged = scripted(0)
    t0 = time.monotonic()
    settled, pending = notelib.wait_for_embeddings(timeout=5, poll=0.1, log=logged.append)
    check("an already-empty queue returns settled immediately",
          settled and pending == 0 and time.monotonic() - t0 < 0.5)
    check("nothing is logged when there was nothing to wait for", logged == [], str(logged))

    # --- drains after a few polls -------------------------------------------------------------
    logged = scripted(3, 3, 0)
    settled, pending = notelib.wait_for_embeddings(timeout=5, poll=0.05, log=logged.append)
    check("a queue that drains reports settled", settled and pending == 0)
    check("the wait is announced once, then confirmed",
          len(logged) == 2 and "waiting for 3 pending" in logged[0] and "drained" in logged[1],
          str(logged))

    # --- never drains: fail open, do not hang -------------------------------------------------
    logged = scripted(7)
    t0 = time.monotonic()
    settled, pending = notelib.wait_for_embeddings(timeout=0.3, poll=0.05, log=logged.append)
    elapsed = time.monotonic() - t0
    check("a stuck queue times out instead of blocking forever",
          not settled and pending == 7, f"{elapsed:.2f}s")
    check("the timeout is honoured", elapsed < 2.0, f"{elapsed:.2f}s")
    check("the pending count is surfaced so an operator can act",
          any("still unembedded" in m for m in logged), str(logged))

    # --- zero timeout must not sleep out the poll interval -------------------------------------
    scripted(2)
    t0 = time.monotonic()
    settled, pending = notelib.wait_for_embeddings(timeout=0, poll=10, log=None)
    check("timeout=0 returns instantly rather than sleeping the poll interval",
          not settled and pending == 2 and time.monotonic() - t0 < 0.5)

    # --- the log callback is optional ---------------------------------------------------------
    scripted(1)
    notelib.wait_for_embeddings(timeout=0, poll=0.01, log=None)
    check("log=None is accepted", True)

    # --- shipped defaults are sane ------------------------------------------------------------
    check("EMBED_SETTLE_TIMEOUT is bounded and positive",
          0 < notelib.EMBED_SETTLE_TIMEOUT <= 3600, f"{notelib.EMBED_SETTLE_TIMEOUT}s")
    check("EMBED_SETTLE_POLL is shorter than the timeout",
          0 < notelib.EMBED_SETTLE_POLL < notelib.EMBED_SETTLE_TIMEOUT,
          f"{notelib.EMBED_SETTLE_POLL}s")
finally:
    notelib.Ingestor = real_ingestor

lib.report_and_exit()
