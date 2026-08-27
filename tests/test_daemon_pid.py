#!/usr/bin/env python3
"""A daemon is a process, not a substring. (scripts/lib.sh)

The daemon wrappers used to find their process with `pgrep -f scripts/<name>.py`, which matches
any command line *containing* that text — including the `bash -lc '… scripts/ui.py …'` that asked
the question. `stop` therefore killed the caller's own shell, twice in one sitting, silently
(exit 15 and no output, because the shell was gone before it could say anything).

So the check below is the whole point: a decoy process whose command line merely mentions the
script must NOT be mistaken for the daemon — and the test asserts the naive `pgrep` *would* have
matched that same decoy, so it can never quietly stop testing anything.

Needs no database.
"""
import os
import signal
import subprocess
import time

import lib

check = lib.check
LIB = os.path.join(lib.REPO, "scripts", "lib.sh")
RUN_DIR = os.path.join(lib.REPO, "tests", ".pidtest")     # never the real ~/.indexia
# A script name that exists nowhere and is never run. Naming a REAL daemon here would make the
# test find the live one — and then daemon_stop would stop the user's server, which is precisely
# the class of accident this file exists to prevent.
SCRIPT = "scripts/__pidtest_daemon__.py"
NAME = "__pidtest__"


def sh(snippet):
    """Run a snippet with lib.sh sourced and PID_DIR pointed at a scratch dir."""
    return subprocess.run(
        ["bash", "-c", f'INDEXIA_RUN_DIR="{RUN_DIR}"; source "{LIB}"; {snippet}'],
        capture_output=True, text=True)


os.makedirs(RUN_DIR, exist_ok=True)
decoy = None
try:
    # A process that is NOT the daemon but says the daemon's name. This is the shape of the
    # `wsl -d ubuntu -- bash -lc 'cd ~/indexia && ... scripts/ui.py ...'` that got killed.
    #
    # Two commands, not one: bash exec()s a lone simple command and takes over its argv, which
    # would drop the marker and leave the decoy named plain `sleep`. The `;` forces bash to stay
    # resident with its own command line — the thing being tested. Its own session, so the child
    # sleep goes down with it.
    decoy = subprocess.Popen(["bash", "-c", f"sleep 20; : {SCRIPT}"], start_new_session=True)
    time.sleep(0.4)

    naive = subprocess.run(["pgrep", "-f", SCRIPT], capture_output=True, text=True)
    check("the decoy is exactly what the old check would have matched",
          str(decoy.pid) in naive.stdout.split(), f"pgrep -f {SCRIPT} -> {naive.stdout.split()}")

    r = sh(f'daemon_running "{NAME}" "{SCRIPT}" && echo MATCHED || echo CLEAN')
    check("a shell merely mentioning the script is not mistaken for the daemon",
          "CLEAN" in r.stdout, r.stdout.strip() or r.stderr.strip())

    # A pid file naming a live process whose cmdline really does contain the script is believed.
    sh(f'daemon_write_pid "{NAME}" {decoy.pid}')
    r = sh(f'daemon_pid "{NAME}" "{SCRIPT}"')
    check("a pid file naming a live matching process is believed", r.stdout.strip() == str(decoy.pid),
          r.stdout.strip())

    # ...but one naming a process that does NOT match is discarded, not trusted blindly.
    sh(f'daemon_write_pid "{NAME}" {os.getpid()}')             # this test runner: alive, wrong cmdline
    r = sh(f'daemon_running "{NAME}" "{SCRIPT}" && echo MATCHED || echo CLEAN')
    check("a pid file whose process is something else is rejected (pid reuse)",
          "CLEAN" in r.stdout, r.stdout.strip())
    check("...and the stale pid file is cleaned up",
          not os.path.exists(os.path.join(RUN_DIR, f"{NAME}.pid")))

    # A pid file naming a dead process is likewise discarded.
    dead = subprocess.Popen(["bash", "-c", "true"])
    dead.wait()
    sh(f'daemon_write_pid "{NAME}" {dead.pid}')
    r = sh(f'daemon_running "{NAME}" "{SCRIPT}" && echo MATCHED || echo CLEAN')
    check("a pid file naming a dead process is rejected", "CLEAN" in r.stdout, r.stdout.strip())

    # daemon_stop must report honestly when there was nothing to stop — and kill nothing.
    r = sh(f'daemon_stop "{NAME}" "{SCRIPT}" && echo STOPPED || echo NOTHING')
    check("stopping a daemon that is not running reports so", "NOTHING" in r.stdout,
          r.stdout.strip())
    check("...and the decoy is still alive — stop killed nothing it did not own",
          decoy.poll() is None)
finally:
    if decoy and decoy.poll() is None:
        os.killpg(os.getpgid(decoy.pid), signal.SIGTERM)   # bash and the sleep it spawned
        decoy.wait(timeout=5)
    for f in os.listdir(RUN_DIR):
        os.remove(os.path.join(RUN_DIR, f))
    os.rmdir(RUN_DIR)

check("every daemon wrapper uses the pid-file helpers, none uses a bare pkill",
      all("pkill" not in open(os.path.join(lib.REPO, "scripts", s), encoding="utf-8").read()
          and "daemon_stop" in open(os.path.join(lib.REPO, "scripts", s), encoding="utf-8").read()
          for s in ("ui.sh", "scheduler.sh", "embed-worker.sh")))

lib.report_and_exit()
