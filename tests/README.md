# tests/

Regression tests for the parts of Indexia where a bug is quiet: the vector-access path, the
suggestion queue's lifecycle, and the date arithmetic. Zero-dependency, like the rest of the repo —
each file is a standalone script that prints one line per check and exits 0/1, and
[`run_all.py`](run_all.py) totals the exit codes. No pytest.

```bash
bash tests/run.sh                 # everything
bash tests/run.sh --unit          # only the tests that need no database (seconds)
bash tests/run.sh knn expiry      # only files matching these substrings
bash tests/run.sh --docs          # add the README/spec consistency lint
bash tests/run.sh --list          # show what would run

INDEXIA_TESTS_FAST=1 bash tests/run.sh --docs    # the whole suite in ~20s
```

## The one slow spot

Verifying the cache's **live fallback** means issuing a real `vector.neighbors` query, and if the ANN
index happens to be cold that single query rebuilds the entire graph before returning — ~10 minutes at
101 notes, which is exactly the cost the cache exists to avoid. A full run therefore takes either
~20 seconds or ~8 minutes depending on the index's mood.

`INDEXIA_TESTS_FAST=1` skips just those two checks and keeps everything else, including the assertion
that a digest pass makes no vector queries at all. Use it by default; run the full form when you have
changed anything about `neighbors_of`, `semantic_search`, or the cache's shape.

## These run against the live corpus

There is no fixture database. That is a deliberate trade — the bugs these tests exist to catch were
all found *because* the corpus was real (101 notes, a densely linked graph, a genuinely stale ANN
index) — but it makes cleanup a safety property rather than good manners:

- **[`lib.py`](lib.py) `corpus_guard()`** snapshots `Note`/`BEGETS`/`BINDS` counts — the whole graph
  since v0.8.0 — and fails the test if they differ afterwards. Every DB test is wrapped in it. `Op`
  is deliberately not counted: it is append-only by design, so any test that legitimately writes
  leaves one behind. Tests that must assert "no `Op` was written" check it themselves.
- **`synthetic_links()`** stages real `BINDS{suggested}` edges between otherwise-unlinked notes,
  optionally back-dated, and deletes exactly those on exit. Anything touching the expiry sweep plants
  its own edges — never a genuine suggestion.
- Cache tests write only to `KnnCache`, which is derived data, and restore every row they change.

Take a backup first if you are nervous: `bash scripts/backup.sh`.

## What each file covers

| File | Needs DB | Covers |
|------|----------|--------|
| [`test_stage_order.py`](test_stage_order.py) | no | Which candidates reach the ratification queue. Moves 1 and 2 score on **opposite axes**, so one shared ranking or floor would silence move 2 — the live corpus produces no move-2 candidates, so only a unit test sees this. |
| [`test_analytics_metrics.py`](test_analytics_metrics.py) | no | The two analytics conventions, as pure arithmetic: `fitness.score` (catalyzes credits, inhibits debits, **untyped is worth 0**, lineage purely positive, floor holds) and `autocatalytic_core` (autocatalysis as a *cycle* in the catalysis relation — a pure `BEGETS` chain never closes). |
| [`test_analytics_readonly.py`](test_analytics_readonly.py) | yes | **The boundary, made enforceable** (spec §13): every report runs against the live corpus, then the corpus counts, the `Op` count and the `visited` total must all be unchanged. Also that the two community-detection code paths agree, and that `--as-of` bounds behave. |
| [`test_inbox.py`](test_inbox.py) | no | Dropping a file into `staging/` or `staging/scans/`. Mostly the header rewrite, asserted by running the **real** `parse_props` over the real output rather than predicting it — predicting it is the thing that goes wrong. Pins the three ways that parser loses text silently (a headerless file swallowing its body into an injected `source_ref`; a prose file with a markdown `---` divider losing everything above it; YAML front matter and a leading BOM eating the header), plus id minting, the same-id-different-extension refusal, scan de-duplication, and every `safe_name` rejection. |
| [`test_scheduler_status.py`](test_scheduler_status.py) | no | `next_run` against `_due`, over every hour of two weeks for every cadence: it never points into the past, "due" and "the next run is now" are one claim, and the instant it names really is one `_due` accepts. The grid is what showed `_due` is **not monotonic in time** — hence `next_run` takes a `now` at all. Also that an unreadable last-run stamp reads as "never ran" instead of raising out of the daemon's loop. |
| [`test_daemon_pid.py`](test_daemon_pid.py) | no | A daemon is a process, not a substring: a decoy whose command line merely *mentions* `scripts/<name>.py` must not be taken for the daemon, and the test asserts the old `pgrep -f` **would** have matched that same decoy, so it cannot quietly stop testing anything. Also that a pid file is disbelieved when its process is dead or is something else (pid reuse), and that `stop` on a daemon that isn't running kills nothing. |
| [`test_ui_readonly.py`](test_ui_readonly.py) | yes | The same boundary for the graph UI's **read** half, plus the payload contract: edge ids stable and endpoints resolvable, no note body on the wire, the window a flag rather than a filter (metrics stay corpus-wide). Exercises every `GET` route — including `/api/staging`, whose preview runs the real ingest batch in dry-run, and `/api/status`, which additionally must leave the **pid files** untouched: `lib.sh` deletes a stale one and writes a fresh one from `pgrep`, and a GET may do neither. Compares the `Op` and `visited` totals either side. Drives a real server on an ephemeral port, so the 404 on a path traversal out of `ui/` is the one that ships. |
| [`test_ui_write.py`](test_ui_write.py) | yes | The UI's **write** half *(v0.8.1)*. The CSRF guard first: a write is refused without `X-Indexia-Write`, without a JSON content type, from a foreign `Origin` or `Host`, over the 1 MiB cap, with a negative `Content-Length` (which clears a `> cap` test and then means "read to EOF"), or chunked — and `OPTIONS` is a 501, which is not a detail about an unimplemented verb but the reason the header checks are enforceable at all. Then the writes themselves: `ADD_NOTE` (never embedding inline), `BEGETS` dated from the child's id, the whole `suggest`→`ratify`→`retype`→`reject` round trip including ratifying with the endpoints reversed, `CORRECT_COSMETIC`, and the error codes each failure maps to. Every write's returned `op_id` is looked up in the log, which is a sharper claim than counting Ops. Then `/api/upload`: every refusal, and one real drop proving that **parking a file logs no `Op`** — nothing reaches the graph until ingest. Commits real notes and deletes them in a `finally` (and removes the parked file, which lands in the real `staging/` that `corpus_guard` cannot see); the server under test is built with `embedder=None` so no stray embedding invalidates the ANN index. Four source-level greps ride along: no raw SQL, no live vector query, no `do_OPTIONS`, no `run_job` — plus an **`ast`** check that `ui.py` reaches `analytics.walks` for `visits` and nothing else, since `walks.replay` falls through to a live vector query and a grep for `"replay"` would fail on the comment explaining why. |
| [`test_walk_ops.py`](test_walk_ops.py) | yes | Walks round-trip through the `Op` log with no vertex, and `visited` counts **walks, not visits** — a note visited three times in one walk moves the counter once, the working-set role never demotes, and `DELETE_WALK` gives back exactly what it took. |
| [`test_wait_gate.py`](test_wait_gate.py) | no | `wait_for_embeddings`, especially the **fail-open timeout**: a stalled embed worker must cost the nightly batch freshness, never stop it. `Ingestor` is stubbed. |
| [`test_knn_cache.py`](test_knn_cache.py) | yes | Staleness detection (coverage gaps *and* an in-place re-embed), live fallback, corrupt-row degradation, and the headline invariant: **a full digest pass issues zero vector queries**. |
| [`test_link_expiry.py`](test_link_expiry.py) | yes | The sweep's four guards — only aged `suggested` edges go; nothing goes while the queue is small; undated edges are counted but spared; `ratified` edges are corpus. Checked against ground truth computed in Python. |
| [`test_search_dates.py`](test_search_dates.py) | yes | `--since`/`--until` as a **closed** range, including the month rollover. Expectations are derived from the ids present, so it works on any corpus. |
| [`test_db_invariants.py`](test_db_invariants.py) | yes | ArcadeDB behaviours the code depends on, plus a live demonstration of the one confirmed bug it works around. Creates and drops an index on `BINDS.created_at` to provoke it. Also pins that `BINDS.mode` and `BEGETS.created_at` stay unindexed (nullable, same hazard), that the pre-v0.7.0 edge types and the v0.8.0-removed `Trace`/`Cluster` types are gone, that `Note` carries no stored analytics, and that every `BEGETS` edge is dated. |
| [`check_docs.py`](check_docs.py) | no | Docs lint: README anchors resolve, scripts documented both ways, every Op rule and DDL type reaches both README and spec. |
| [`tools/score_dist.py`](tools/score_dist.py) | yes | **Not a test** — prints the move-1/move-2 score distribution so the digest's staging floor can be chosen from data as the corpus grows. |

## The one check that asserts a bug exists

Most checks assert that things work. One asserts that something is *broken*, because two design
decisions depend on it:

> With an index on `BINDS.created_at`, an `UPDATE … SET created_at = null` does **not** remove the
> row's existing index entry, so a range predicate keeps matching a row whose stored value is null.

That is how the first version of the expiry sweep deleted the undated edges it promised to spare.
`test_db_invariants.py` demonstrates it on demand — it creates the index, provokes the stale entry,
checks that dropping the index makes the same predicate correct, and removes the index again. Hence
`created_at` stays unindexed and `expire_suggested_links` ages the queue in Python with an explicit
null guard.

**If that check ever fails, that is good news**: ArcadeDB has fixed the bug, and indexing `created_at`
can be reconsidered. The failure message says so. Don't "fix" it by loosening the assertion.

A second symptom observed at the same time — the predicate returning 17 rows where 12 matched, two
duplicated and three failing it outright — was never reproduced, including by replaying the entire
original procedure against a restore of the pre-backfill backup. Probably the same class of fault (a
stale entry attaching to a reused record id), but uncharacterized; the surrounding checks assert the
correct behaviour so its return would show up.

## A side effect worth knowing

Every mutation logs an `Op` (spec §11.2), and that includes the tests' own synthetic edges — so a run
leaves `SUGGEST_LINK`, `RATIFY_LINK` and `EXPIRE_LINKS` entries in the rewrite log even though the
edges themselves are cleaned up. The log is append-only by design, so this is not a leak, but it does
mean `SELECT rule, count(*) FROM Op GROUP BY rule` counts test activity alongside real work. Filter by
id range if you are auditing a specific period.
