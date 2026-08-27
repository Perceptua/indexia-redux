#!/usr/bin/env python3
"""Run the Indexia test suite — discover `tests/test_*.py`, run each, aggregate pass/fail.

Deliberately not pytest: the repo is zero-dependency, and each test file is already a standalone
script that prints its checks and exits 0/1. This just runs them and totals the exit codes.

  tests/run.sh                 everything
  tests/run.sh --unit          only the tests that need no database
  tests/run.sh knn expiry      only files whose name contains one of these
  tests/run.sh --list          show what would run
"""
import argparse
import os
import subprocess
import sys
import time

TESTS = os.path.dirname(os.path.abspath(__file__))

# Tests that construct notelib.Arcade() and read/write the live database. Everything else is a
# pure unit test and runs anywhere, which is why --unit is worth having as a fast pre-flight.
NEEDS_DB = {"test_knn_cache.py", "test_link_expiry.py", "test_search_dates.py",
            "test_db_invariants.py", "test_analytics_readonly.py", "test_walk_ops.py",
            "test_ui_readonly.py", "test_ui_write.py"}


def discover():
    return sorted(f for f in os.listdir(TESTS)
                  if f.startswith("test_") and f.endswith(".py"))


def main():
    p = argparse.ArgumentParser(prog="run_all", description="Run the Indexia test suite.")
    p.add_argument("patterns", nargs="*", help="only run files matching these substrings")
    p.add_argument("--unit", action="store_true", help="skip tests that need the database")
    p.add_argument("--list", action="store_true", dest="list_only",
                   help="list the selected files and exit")
    p.add_argument("--docs", action="store_true",
                   help="also run check_docs.py (README/spec consistency lint)")
    args = p.parse_args()

    files = discover()
    if args.unit:
        files = [f for f in files if f not in NEEDS_DB]
    if args.patterns:
        files = [f for f in files if any(pat in f for pat in args.patterns)]
    if args.docs:
        files.append("check_docs.py")

    if not files:
        sys.exit("[tests] nothing selected")
    if args.list_only:
        for f in files:
            print(f"  {f}" + ("   (needs the database)" if f in NEEDS_DB else ""))
        return

    results, started = [], time.monotonic()
    for f in files:
        print(f"\n=== {f} " + "=" * max(0, 66 - len(f)))
        rc = subprocess.run([sys.executable, os.path.join(TESTS, f)]).returncode
        results.append((f, rc))

    width = max(len(f) for f, _ in results)
    print("\n" + "=" * 74)
    for f, rc in results:
        print(f"  {'PASS' if rc == 0 else 'FAIL'}  {f.ljust(width)}"
              + ("" if rc == 0 else f"  (exit {rc})"))
    bad = [f for f, rc in results if rc != 0]
    print(f"\n[tests] {len(results) - len(bad)}/{len(results)} file(s) passed "
          f"in {time.monotonic() - started:.1f}s")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
