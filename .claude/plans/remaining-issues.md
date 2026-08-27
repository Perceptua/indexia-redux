# Planned work — remaining test-report issues

> ## ✅ ALL BUILT AND LIVE-TESTED — 2026-07-24
> **I-3.1, I-5 and I-6 are done**, plus the requested `LINKS_TO.created_at` DDL addition. What
> landed: the `KnnCache` document type + `knn-cache` job + `notelib.neighbors_of` (digest 36 min
> unfinished → **6 s, zero vector queries**), `wait_for_embeddings` gating the digest and
> community-detect, `LINKS_TO.created_at` + `backfill-link-dates.sh` (191 edges dated) +
> `link-expiry.sh` weekly sweep, `STAGE_CAP`/`STAGE_MIN_SCORE` in the digest, and inclusive
> `--until`. Two decisions were changed by measurement — recorded in the sections below. Two new
> ArcadeDB gotchas were found (edge-property range queries, null DATETIME comparison); both are
> written up in `README.md` and `ddl/schema.sql`. Corpus verified intact: 101 notes, 77 CATALYZES,
> 7 SUPERSEDES, 11 Clusters, 1 Trace, 18 ratified links.
>
> Still open, deliberately: **applying** `C:\Users\aherk\.wslconfig` (needs `wsl --shutdown`, so it
> is the user's call) and the future ArcadeDB image bump. Kept for the decision record.

**Decided 2026-07-24; to be built in a later session (not started).** Source of the issue
numbers: [`recent/test-report-de-anima.md`](../../recent/test-report-de-anima.md). Already fixed and
re-tested this round: **I-1** (MVCC conflict retry), **I-2** (dry-run intra-batch lineage),
**I-3.2** (serialized vector queries), **I-4** (Folgezettel uniqueness), **I-7** (trace delete). This
file records the decisions for what's left, with enough implementation context to build directly.

**Working context for the build session**
- All core logic is in [`scripts/notelib.py`](../../scripts/notelib.py); every CLI is a thin
  `scripts/*.sh` wrapper (sources `lib.sh`, `load_env`, exports `BASE_URL DB ARCADEDB_ROOT_PASSWORD`,
  execs the `.py`). Run everything via `wsl -d ubuntu -- bash -lc 'cd ~/indexia && …'`.
- **Corpus is live and intact: 101 De Anima notes** (100 sample + 1 produced), 77 CATALYZES,
  7 SUPERSEDES, ~18 ratified + ~many suggested LINKS_TO, 11 Clusters, 1 saved Trace. Test against it;
  keep it intact (mirror the report's §7 approach: targeted scripts in a scratch dir, reversible edits).
- **Vector-rebuild gotcha still applies:** adding a vector invalidates the whole `LSM_VECTOR` graph;
  the next `vector.neighbors` rebuilds all N (~11 min at N=101). Searches are serialized by
  `notelib.vector_query_lock()`. Do not run concurrent searches; expect one rebuild after any embed.
- Editing scripts from Windows can add CRs / drop the exec bit — `sed -i 's/\r$//'` after edits; `.py`
  files run via `python3` so the exec bit doesn't matter for them.

---

## I-3.1 — vector-index rebuild cost  ·  **DECISION: implement proposals 1, 2, 3**

Proposal 4 (index `maxConnections` tuning) was **not** chosen. Proposal 5 is **future** (below).

1. **Gate the digest/detection jobs on `pending == 0`.** In
   [`scripts/provocation_digest.py`](../../scripts/provocation_digest.py) `build()` and
   [`scripts/community_detect.py`](../../scripts/community_detect.py), before the first vector query,
   wait until `notelib.Ingestor().pending_note_ids()` is empty (poll ~10 s with a bounded timeout;
   **fail-open** — proceed anyway if it never settles, and log it). Effect: the corpus is stable, so
   the run pays **one** rebuild then fast (~3 s) queries instead of a rebuild per query. This is the
   fix for the 36-min unfinished digest. Lowest effort, biggest practical win.
2. **`.wslconfig` for headroom.** Create `C:\Users\aherk\.wslconfig` granting more vCPU/RAM (VM is
   4 vCPU / 3.8 GiB, no `.wslconfig` today; build saturates ~2.6 vCPU). Cuts rebuild wall-clock
   ~linearly. **Env change, not code.** Needs `wsl --shutdown` to apply → **restarts the DB container,
   so coordinate with the user** and bring the stack back with `scripts/up.sh` after.

   *File written, NOT applied — and the premise does not hold on this hardware.* The host is a 4-core
   Pentium Silver N5000 with 7.8 GiB, and WSL 2 defaults to **all** logical processors, so the VM
   already had all 4 vCPU: there is no CPU headroom to grant and no ~linear win to collect. The file
   therefore only raises the RAM ceiling (3.7 → 5 GiB) with `autoMemoryReclaim=gradual` (so the memory
   returns to Windows when idle — necessary on an 8 GiB host) plus 4 GiB swap so a rebuild cannot
   OOM the JVM. Applying it still needs `wsl --shutdown` → user's call.
3. **k-NN adjacency cache.** Materialize top-k neighbours per note (a new table, or a JSON property on
   `Note`), refreshed once per nightly cycle (extend `community-detect`, or a new job). Point
   `notelib.move1_candidates` (and move-2) at the cache instead of `vector.neighbors`, so the six-move
   digest amortizes a single rebuild across all seeds/moves. Biggest code change of the three — design
   the cache shape + an invalidation rule (refresh when new embeddings landed since last build).

   *As built:* a `KnnCache` **DOCUMENT** type (derived data, never traversed), `KNN_CACHE_K=50` per
   note, refreshed by a **dedicated `knn-cache` job** running first in the nightly batch rather than
   folded into `community-detect` — community detection reads only the link graph, so mixing the two
   would have coupled unrelated concerns. Move **4** was pointed at the cache too: it queries once per
   sampled seed (up to 50), making it the digest's heaviest vector consumer. Move **2** needed no
   change — it never calls `vector.neighbors`, it pulls a time window and cosines in Python.

**Future (proposal 5), do NOT build now:** if a newer ArcadeDB gains *incremental* vector insertion
(no full rebuild per add), bump the pinned image in
[`docker/docker-compose.yml`](../../docker/docker-compose.yml) — that removes I-3.1 at the source.
Watch the ArcadeDB release notes for JVector/LSM_VECTOR incremental-insert support.

## I-5 — digest suggestion queue outgrows the human  ·  **DECISION: implement proposals 2 + 3**

Proposal 1 (global backpressure) and proposal 4 (rejection tombstones) were **not** chosen.

- **(2) Expiry sweep.** A job (weekly; fold into the scheduler like `resurface`) deletes
  `LINKS_TO WHERE status='suggested'` **older than 30 days** — **but if the total suggested count is
  `<= 10`, delete nothing** (don't prune a small queue). Log an Op for the sweep.
  - *Implementation note:* `LINKS_TO` has **no `created_at`**. Get a suggestion's age from its
    `Op(SUGGEST_LINK)` id (the Op id is the timestamp; the payload is `{a, b}` — join suggested edges
    to their SUGGEST_LINK Op by endpoints). Simpler going forward: add a `suggested_at` property to the
    edge in `LinkManager.suggest` and filter on that; for the ~90 pre-existing suggested edges, backfill
    from the Op log or treat undated ones as "now" (they'll age in from here). Decide which at build time.
- **(3) Per-run cap + score floor.** In `provocation_digest.build()` (currently stages up to
  `seeds_limit(10) × k(5)` = ~50/run, already skips existing pairs via `LinkExists`): stage only the
  **top-K by score** (add a cap constant, e.g. 10/run) and raise the staging **min-score floor (~0.7)**.

  *As built, with two measured deviations:* (a) the floor is **0.65, not 0.70** — measured on this
  corpus, move-1 scores run max 0.729 / median 0.644, so 0.70 admitted only **4 of 55** candidates and
  made the floor rather than the cap the binding constraint, effectively switching move 1 off. 0.65 is
  the knee (26 of 55) and leaves the cap in charge of volume; `--min-score` overrides. (b) "top-K by
  score" applies **within each move, not across them** — move 1's score is similarity (high = better)
  while move 2's is the same cosine read as distance (low = better, already capped at 0.5), so one
  ranking and one floor across both would have silenced move 2 entirely. Each is ranked on its own axis
  and the two lists are **interleaved** under the shared cap. Rendering stays uncapped: the digest is a
  surface to read, the queue is a list to decide.

## I-6 — `--until` is exclusive and undocumented  ·  **DECISION: implement the proposal**

Make `--until` **inclusive of the named day**, and document it. In
[`scripts/notelib.py`](../../scripts/notelib.py) `lexical_search` / `_day_bound`: keep the internal
`created_at < date(:until)` but bump the bound by one day (so `--since D --until D` = all of day D).
Update the README's Finding-notes section and `search.py --help` to say `--until` is inclusive.

## CONTRADICTS leftover  ·  **DONE this session** — dropped via console (`DROP TYPE CONTRADICTS`, 0 edges)

**Durable design principle (record + honour going forward): edges stay semantically neutral; the only
semantically-loaded edge is `SUPERSEDES`** (correction / the INHIBIT signal, spec §6). Do **not** add
typed *semantic* edges (CONTRADICTS / SUPPORTS / CONTRASTS / …). Semantic relationships are expressed
through **notes + untyped `LINKS_TO`**, never through edge labels — this reinforces spec §10
("`LINKS_TO` stays untyped") and "a keyword is just a hub note many notes link to." Structural edge
types (CATALYZES lineage, VISITED/PRODUCED/FORKED_FROM trace mechanics, CONTAINS/HUB_OF/DERIVES_FROM
cluster mechanics) are fine because they're structural, not semantic. This constrains future
`promote-type.sh` usage: promote structural edge types only.
