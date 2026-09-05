# Indexia

A personal **Zettelkasten knowledge graph** — a human-curated, append-only corpus with a machine that
*provokes* rather than synthesizes ([docs/spec.md](docs/spec.md)). This repo is the whole running
system: a containerized **ArcadeDB** server + schema, the ingestion pipeline (embed-on-commit), the
read/difference-engine (search, links, the six provocation moves), walk recording, a read-only
analytics layer, and a maintenance scheduler — all driven from `scripts/`.

> **Operations vs. analytics** *(v0.8.0, spec §13)*. The graph is **purely relational**: `Note`
> vertices joined by `BEGETS` (lineage) and `BINDS` (association). A note is value-neutral — it does
> not know its own fitness, its community, or whether it is critical. Those are **observations**,
> computed on demand by `scripts/analytics.sh`, which writes nothing to the graph. Anything that
> changes the corpus is an operation and appends an `Op`; anything that only measures it appends
> nothing at all.

> **Where things live.** The design is the spec ([docs/spec.md](docs/spec.md), currently **v0.8.6**);
> phase plans are under `.claude/plans/`. Every script is a thin `bash` wrapper over the shared Python
> core [`scripts/notelib.py`](scripts/notelib.py); the analytics live in `scripts/analytics/`. Build
> order (spec §12.8) **steps 1–7 are complete** and integration-tested; see
> [Status & roadmap](#status--roadmap) at the end for what's built and what's open.

> **Running scripts.** Docker + `python3` live in WSL Ubuntu, so run everything from the repo root in
> WSL — e.g. `wsl -d ubuntu -- bash -lc 'cd ~/indexia && bash scripts/status.sh'`. The examples below
> drop the WSL prefix for brevity.

## Prerequisites

- **Docker + Docker Compose** (tested with Docker 29 / Compose v5 in WSL Ubuntu).
- `openssl`, `python3`, `curl` on the host running the scripts.
- **WSL 2 memory headroom.** The ArcadeDB JVM and the Ollama embedder share the VM, and rebuilding the
  vector index is memory-hungry. `C:\Users\aherk\.wslconfig` raises the ceiling to 5 GB with 4 GB swap
  and `autoMemoryReclaim=gradual` (so the memory returns to Windows when idle — it is a ceiling, not a
  reservation). Note that WSL 2 already grants *all* logical processors by default, so on a 4-core host
  there is no CPU headroom to add. Changing that file needs `wsl --shutdown`, which stops the
  container; bring it back with `scripts/up.sh`.

## Quickstart

```bash
bash scripts/up.sh      # start server, wait for TLS ready, create DB + apply schema
bash scripts/down.sh    # stop (the corpus in ./data persists)
```

On first run, `up.sh` bootstraps everything it needs:
- generates `docker/.env` with strong random dev secrets (`scripts/gen-env.sh`),
- generates a self-signed TLS keystore `certs/keystore.p12` (`scripts/gen-cert.sh`),
- creates the `indexia` database and applies [`ddl/schema.sql`](ddl/schema.sql) (idempotent).

Then open **Studio / REST** over HTTPS as user `root` (password in `docker/.env`) — at
`https://localhost:2480` by default, or **`https://localhost:12480`** in this checkout (see Ports
below). The cert is self-signed, so accept it in the browser or use `curl -k`.

> **Ports.** Defaults are ArcadeDB's standard `2480` (HTTPS/Studio) + `2424` (binary), bound to
> `127.0.0.1` only. This checkout overrides them to **`12480` / `12424`** in `docker/.env`
> (`INDEXIA_HTTP_PORT` / `INDEXIA_BINARY_PORT`) to avoid clashing with another local ArcadeDB.

**Serving ArcadeDB over Tailscale.** `bash scripts/up.sh --tailscale` (or `make up
ARGS=--tailscale`) *adds* a second publish of **HTTPS/Studio (2480) only** on this machine's
Tailscale IP, serving it with a `tailscale`-issued cert for the node's MagicDNS name — so
Studio/REST is reachable, and trusted with no browser warning, from another device on the
tailnet. The loopback publish stays up alongside it, unmodified, so every local script that
defaults to `https://localhost:2480` (`search.sh`, `add-note.sh`, the graph UI backend, …) keeps
working exactly as before. The **binary protocol (2424) always stays loopback-only**, with no
flag able to widen it. This holds the same root password that guards the DB either way —
Tailscale changes who can *reach* the login prompt, not whether one still stands there — so treat
it the same as any other credential exposed to your tailnet. Nothing here is persisted in
`docker/.env`: a plain `bash scripts/up.sh` next time drops the tailnet publish again, and
`scripts/status.sh` reports whether it's currently there. See Security posture below.

## Guided tour — exercise every feature

A runnable end-to-end pass through the whole system. Each step links to its detailed section below.

> **Heads-up:** this writes real notes/edges into your corpus. To keep it in a throwaway instead,
> prefix every command with `INDEXIA_DB=indexia_demo` (and run `apply-ddl.sh` first with that set) —
> `lib.sh` reads `INDEXIA_DB`. Delete the sandbox later with the console, or just ignore it.

```bash
# 1. Bring the stack up: DB + schema, embedder + worker, and the maintenance scheduler.
bash scripts/up.sh
bash scripts/status.sh                      # containers, readiness, database list

# 2. Add a couple of atomic notes, the second continuing the first's lineage (BEGETS).
bash scripts/add-note.sh --title "Atomicity" --body "One idea per note, stated in full sentences."
# grab the id it prints, then continue from it:
bash scripts/add-note.sh --parent <id-from-above> --mode continues \
  --body "Atomic notes recombine freely because each stands alone."

# 3. Let the embedder catch up (async embed-on-commit), then confirm nothing is pending.
bash scripts/embed-worker.sh status         # "pending (unembedded): 0" when done
```
```bash
# 4. Read side — find notes by meaning or metadata.
bash scripts/search.sh -q "notes that recombine"          # semantic
bash scripts/search.sh --title Atomicity                   # lexical

# 5. Provoke + link — the machine proposes associative links; you ratify.
bash scripts/provoke.sh --seed <id> --stage                # stage move-1 SUGGEST_LINKs
bash scripts/link.sh list --status suggested               # review them
bash scripts/link.sh ratify <a> <b>                        # accept one (or reject)

# 6. Reading session — record a walk, save it, replay it against the live corpus.
bash scripts/walk.sh start --seed <id> --intent "follow the lineage"
bash scripts/walk.sh visit <walk> <other-id>
bash scripts/walk.sh save <walk> && bash scripts/analytics.sh replay <walk>
```
```bash
# 7. Analytics — measure the graph without touching it (spec §13). Add --as-of <ts> for the past.
bash scripts/analytics.sh communities        # detected themes, each with its hub
bash scripts/analytics.sh autocatalysis      # which of them cycle under catalysis
bash scripts/analytics.sh fitness --limit 15 # note standing, computed from the graph
bash scripts/analytics.sh criticality        # sparse / critical / dense
bash scripts/analytics.sh visited --ascending # what you keep but never revisit
bash scripts/analytics.sh debt               # load-bearing notes you stopped attending to

# 8. Maintenance jobs (these also run on a schedule — see Maintenance loop).
bash scripts/knn-cache.sh                   # materialize the k-NN cache the moves read (do this first)
bash scripts/provocation-digest.sh          # → recent/provocations.md (all six moves)
bash scripts/resurface.sh                   # → recent/resurface.md
bash scripts/link-expiry.sh --dry-run       # what the suggestion sweep would prune

# 9. Inspect the rewrite log and the graph directly.
bash scripts/console.sh                     # interactive SQL; e.g. SELECT rule, id FROM Op ORDER BY id DESC LIMIT 10

# 10. Snapshot, then stop (the corpus in ./data persists).
bash scripts/backup.sh
bash scripts/down.sh
```

Each capability has a fuller how-to below: [Ingestion](#ingestion) · [Embedding](#embedding) ·
[Finding notes](#finding-notes) · [Links & provocations](#links--provocations) ·
[Reading sessions](#reading-sessions-walks) · [Analytics](#analytics-measuring-the-graph) ·
[Graph UI](#graph-ui) · [Maintenance loop](#maintenance-loop) ·
[Operating & inspecting](#operating--inspecting).

## Scripts

| Script | What it does |
|--------|--------------|
| `up.sh [--tailscale]` | Start, wait for readiness, apply schema, start the embedder + worker + maintenance scheduler, refresh the recent-notes digest. (Nightly backups run automatically — see Backups.) `--tailscale` adds Studio/REST (2480 only) over the tailnet, alongside loopback — see [Security posture](#security-posture). |
| `down.sh [--backup] [--reset]` | Stop. `--backup` first; `--reset` wipes `./data` + `./backups` (destructive). |
| `status.sh` | Container state + server readiness + database list. |
| `logs.sh [N]` | Follow server logs (last `N` lines, default 100). |
| `apply-ddl.sh` | Create DB if absent + apply `ddl/schema.sql` (idempotent; safe to re-run). |
| `promote-type.sh` | Register a new vertex/edge type at runtime — schema growth (spec §3.3, §12.3 `PROMOTE_TYPE`). |
| `backup.sh` | On-demand hot backup → `./backups/indexia/…zip`. |
| `restore.sh <zip> [db]` | Restore a backup into a **new** DB (default `indexia_restore`); never clobbers `indexia`. |
| `drop-db.sh <db>` | Drop a scratch/restored database; refuses to touch the live one. |
| `console.sh` | Interactive ArcadeDB SQL console. |
| `smoke-test.sh` | End-to-end check: insert + `vector.neighbors` + `BEGETS` traversal (self-cleaning). |
| `add-note.sh` | Ingest one note (workflow 1); also `--correct` (in-place cosmetic fix). See Ingestion. |
| `ingest-staging.sh [--dry-run]` | Ingest id-named files dropped in `staging/` (workflow 2). |
| `transcribe-scans.sh [scan]` | Workflow 3 from the shell: opens an interactive `claude` session straight into the `transcribe-notes` skill, over everything waiting in `staging/scans/` or one named scan; ratified notes land in `staging/`. Runs with `--permission-mode bypassPermissions`. See Ingestion and the `staging/scans/` drop zone under [Graph UI](#graph-ui). |
| `review-transcripts.sh [transcript]` | Workflow 4 from the shell: opens an interactive `claude` session straight into the `review-transcripts` skill, over everything waiting in `staging/transcripts/` or one named transcript; ratified notes land in `staging/`. Runs with `--permission-mode bypassPermissions`. See Ingestion. |
| `recent-notes.sh` | Render the most recent day's notes to `recent/recent-notes.md` (run by `up.sh`). |
| `search.sh` | Find notes — semantic (`-q`/`--like`) or lexical/metadata (`--title`/`--author`/`--since`…). See Finding notes. |
| `link.sh` | Associative `BINDS` ratification flow: `suggest`/`ratify`/`retype`/`reject`/`list` (spec §7). |
| `seed-binds.sh` | Replay an associative layer from a TSV manifest after a from-scratch rebuild (spec §7). See [Links & provocations](#links--provocations). |
| `provoke.sh` | Provocation move 1 — semantically near, graph-far; `--stage` proposes suggested links (spec §8.1). |
| `walk.sh` | Record a reading session: `start`/`visit`/`produce`/`save`/`fork`/`delete`. Writes an `Op` per event + `Note.visited`. See Reading sessions. |
| `analytics.sh` | **Read-only** reports (spec §13): `fitness`/`debt`/`criticality`/`communities`/`autocatalysis`/`visited`/`walks`/`walk`/`replay`, most with `--as-of`. See Analytics. |
| `ui.sh [start\|stop\|status\|run]` | Graph view in a browser: serves `http://127.0.0.1:8420/`. Reads the corpus — including a `search` panel over title/body/source/author/date *(v0.8.3)* — and writes to it: add a note, ratify a bind, correct a typo, drain `staging/`, or record a walk *(v0.8.3)*, each through the same `notelib` path the CLI uses *(v0.8.1)*. `run --read-only` restores the reads-only surface; `run --tailscale` serves the tailnet over HTTPS instead of loopback; `run --snapshot --json` prints the payload without serving. See [Graph UI](#graph-ui). |
| `knn-cache.sh` | Nightly rebuild of the k-NN adjacency cache the provocation moves read; `--status`, `--if-stale`. See Maintenance loop. |
| `provocation-digest.sh` | Nightly digest of all six moves → `recent/provocations.md`; stages the strongest move-1 suggestions (spec §8.1). |
| `resurface.sh` | Weekly re-encounter of orphan/inhibited/anniversary notes → `recent/resurface.md` (spec §8.1). |
| `link-expiry.sh [--dry-run]` | Weekly sweep of stale `suggested` links, so the ratification queue stays human-sized (spec §7). See Links & provocations. |
| `backfill-link-dates.sh [--dry-run]` | One-time migration: date `BINDS` edges predating `BINDS.created_at`, from the `Op` log. |
| `migrate-v0-8-0.sh [--dry-run]` | One-time migration to the v0.8.0 purely-relational schema: adds `Note.visited` + `BEGETS.created_at`, drops `Note.fitness`/`activation` and the `Trace`/`Cluster` types. Also the repair path after restoring an older backup. |
| `scheduler.sh` | The maintenance scheduler daemon: runs the jobs above on a cadence. `start`/`stop`/`status`/`run`. See Maintenance loop. |
| `setup-ollama.sh` | One-time (no-sudo) install of the local Ollama embedder + model. See Embedding. |
| `embed-server.sh [start\|stop\|status\|warm]` | Manage the local Ollama embedding daemon (keeps the model warm). |
| `embed-worker.sh [start\|stop\|status\|run]` | Background worker that embeds pending notes (async embed-on-commit). |
| `embed-backfill.sh [--limit N] [--dry-run]` | One-shot: embed any notes still lacking an embedding. |
| `new-id.sh [N]` | Mint N fresh, unique spec §4 note ids (used by the transcribe-notes skill). |
| `gen-env.sh` / `gen-cert.sh [--force\|--tailscale]` | (Re)generate dev secrets / TLS keystore. `--tailscale` provisions a tailscale-issued keystore alongside the self-signed one, for `up.sh --tailscale`. |

## Schema

[`ddl/schema.sql`](ddl/schema.sql) defines the graph from spec §12.1. It is deliberately small
*(v0.8.0)*: **one domain vertex type and two edge types.**

Vertices: `Note` (with a 1024-dim `ARRAY_OF_FLOATS` embedding indexed by `LSM_VECTOR`) and `Op`
(the append-only rewrite log — infrastructure, not corpus). Edges: `BEGETS` (lineage) and `BINDS`
(associative, carrying the corpus's only sign). Plus one document type, `KnnCache`.
`id`/`created_at` are `READONLY` and `id`/`body` `MANDATORY`+`NOTNULL` (append-only immutability,
§4/§6).

- **A note is value-neutral** *(v0.8.0, spec §13)*. `Note.fitness` and `Note.activation` are gone,
  along with the `Trace` and `Cluster` vertex types and their six edge types. All of it is computed
  on demand by [Analytics](#analytics-measuring-the-graph) instead. `Note.fitness` had been
  *write-only* — a nightly job rewrote it for every note and nothing ever read it, which is the
  clearest sign it was never a fact about the note.
- **`Note.visited`** is the one exception, and it earns it: it counts human-directed **walks**
  through a note, which nothing else in the graph records. Written only by `walk.sh`, once per walk
  per note.
- **Everything is dated.** `Note`, `BEGETS` *(new in v0.8.0)* and `BINDS` all carry `created_at`,
  so any report can be recomputed `--as-of` a past instant. That is what makes storing derived
  structure unnecessary: a cluster from last month is a query, not a record you had to keep.

- **Idempotency** lives in `apply-ddl.sh` (applies statements individually, tolerates "already
  exists") because this ArcadeDB build rejects `IF NOT EXISTS` on `CREATE PROPERTY`.
- **Vector dim (1024)** is the one knob to change when the embedding model is finalized (spec §10);
  changing it means dropping/recreating the `LSM_VECTOR` index and re-embedding.
- **`BINDS.created_at`** stamps each edge at `CREATE` (its birth instant), which is what ages the
  suggestion queue for the weekly expiry sweep. Unlike `Note.created_at` it is *not* `READONLY`, so
  `backfill-link-dates.sh` can date edges that predate the property; undated edges are never swept.
- **`KnnCache`** is a `DOCUMENT` type, not a vertex — a rebuildable index over the corpus, never part
  of the graph. Dropping it costs nothing but the time to rebuild (`knn-cache.sh`).

## Ingestion

Two ways to add notes (both run the `ADD_NOTE` rule, spec §12.3). Every workflow commits through
one central path — `Ingestor.commit` in `scripts/notelib.py` — so each note is **embedded on
commit** (spec §7) and the insert is atomic with an appended **`Op(ADD_NOTE)`** rewrite-log entry
(§11.2, §12.3). There are matching Claude Code skills in `.claude/skills/` (`add-note`,
`ingest-staging`).

**One note (workflow 1)** — flags map to the human-facing note fields (`ddl/schema.sql` §20-25).
`--body` is required (or piped on stdin / `--body-file`); `id` and `created_at` are auto-generated
(compact UTC ms, spec §4) unless you pass them for a back-dated import; `author` defaults to `human`.

```bash
bash scripts/add-note.sh --title "Atomicity" --author human --source-ref "Luhmann 1992" \
  --body "A note should carry exactly one idea, stated in full sentences so it stands alone."
```

**Lineage.** A new note can be placed in the lineage (spec §4). This is the **only** edge ingestion
creates — the ingestion layer is purely structural (§3.2):

```bash
# BEGETS: the new note continues (or branches) from an existing note
bash scripts/add-note.sh --parent 20260722T101500000Z --mode continues --body "…the next step in the thread."
```

`--parent`+`--mode` (`continues`|`branches`) add a `BEGETS` edge parent→new. Staged files accept the
same as `parent:` / `mode:` keys.

**Corrections** *(v0.7.0 — no longer an ingestion act)*. Correcting a note is a **judgement**, so it
goes through the same suggest-then-ratify gate as every other relationship (spec §6, §7): commit the
corrected note, then bind it back as `inhibits`.

```bash
NEW=$(bash scripts/add-note.sh --body "…the corrected idea." --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["note_id"])')
bash scripts/link.sh suggest "$NEW" 20260722T101500000Z --mode inhibits --rationale "clarified wording"
bash scripts/link.sh ratify  "$NEW" 20260722T101500000Z
```

The old note is untouched — there is no `status = superseded`. Being corrected is *derived*: a note
is inhibited exactly while a ratified `BINDS{inhibits}` points at it, so withdrawing the bind
withdraws the correction. Ratifying writes the edge and nothing else *(v0.8.0)*: the sign lives on
`BINDS.mode` and is read where a question is asked, rather than accumulating into a number on the
notes. What the correction means for the lineage that produced the target is a question for
[Analytics](#analytics-measuring-the-graph).

**Cosmetic fixes (in-place).** A typo or formatting fix that doesn't change *meaning* is the one allowed
in-place edit (spec §6, `CORRECT_COSMETIC`):

```bash
bash scripts/add-note.sh --correct 20260722T101500000Z --body "…the same idea, typo fixed."
```

It's sanity-checked by **embedding drift**: if the new body is too semantically different (drift ≥
`--max-drift`, default 0.15) the edit is refused with a nudge to issue a correction instead — a change
of meaning is a new note plus a ratified `BINDS{inhibits}`, not an edit. On success the note is re-embedded and an `Op(CORRECT_COSMETIC)` logged.

**Staged files (workflow 2)** — drop a file named as the note `id` into `staging/` (e.g.
`20260722T101500000Z.md`) with `name: value` property lines, then ingest. The id is validated and
checked for uniqueness; successes move to `staging/processed/`, failures to `staging/failed/`.
Format reference: [`staging/README.md`](staging/README.md).

```bash
bash scripts/ingest-staging.sh --dry-run   # validate + parse, move nothing
bash scripts/ingest-staging.sh             # ingest for real
```

`--dry-run` resolves each note's `parent` target against the DB **plus the earlier
files in the same batch** (in sorted-id order, exactly as the real run commits them), so a batch
whose notes reference each other validates cleanly instead of false-failing; a target that is
genuinely absent, or that sorts *after* the note referring to it, is still reported. Commits are
also **retried on a transient `ConcurrentModificationException`** (ArcadeDB's MVCC optimistic-lock
conflict when the embed-worker writes a note while ingestion inserts one) — the whole transaction
is replayed a few times before surfacing, so concurrent ingest + embedding no longer drops a note.

**Handwritten capture (workflow 3)** — drop a photo/scan of a handwritten notecard or page into
`staging/scans/` and start a Claude session with the **`transcribe-notes`** skill (`.claude/skills/`). Claude
transcribes the text **verbatim** (no re-wording or summarizing), proposes note boundaries for
multi-note pages, and — after you **ratify** — writes each approved note to `staging/<id>.md`
(`new-id.sh` mints the ids). From there it is just workflow 2: revise if needed, then
`ingest-staging.sh` commits + embeds. See [`staging/scans/README.md`](staging/scans/README.md).

**Audio transcript review (workflow 4)** — drop a machine transcript of a voice memo or audio note
into `staging/transcripts/` and invoke the **`review-transcripts`** skill (`.claude/skills/`).
Claude corrects only clear mis-transcriptions (flagging anything it can't confidently recover as
`[unclear]` — there's no audio to check against), proposes note boundaries for rambling recordings,
and — after you **ratify** — writes each approved note to `staging/<id>.md`. From there it is just
workflow 2: revise if needed, then `ingest-staging.sh` commits + embeds. `review-transcripts.sh`
runs this from the shell the same way `transcribe-scans.sh` does for workflow 3. See
[`staging/transcripts/README.md`](staging/transcripts/README.md).

**Recent-notes digest (startup).** `recent-notes.sh` finds the calendar date (UTC) of the last
note and renders every note from that date to `recent/recent-notes.md`, overwritten on each run.
`up.sh` calls it after applying the schema, so it refreshes whenever the DB comes up.

> Placing a note in the `BEGETS` lineage (parent + `continues`/`branches`) is a separate, later
> step — ingestion creates each note as a root vertex.

## Finding notes

Read side of the difference-engine (spec §8): find notes by **meaning** or by **metadata**, via
`scripts/search.sh` (and the matching `search-notes` skill). Read-only — it never writes or ratifies.

**Semantic (vector) search** — nearest notes by meaning, over the embedding layer:

```bash
bash scripts/search.sh -q "structure emerges from links, not folders"   # nearest to free text
bash scripts/search.sh --like 20260722T101500000Z                        # nearest to an existing note
```

Each hit carries a cosine-similarity `score` (`1.000` = identical). Needs the embedder up
(`scripts/embed-server.sh`); notes still `pending-embed` don't match until embedded. Vector
queries are **serialized** across processes by a file lock (`~/.indexia/vector-query.lock`,
override with `INDEXIA_VECTOR_LOCK`): adding a note invalidates the ANN graph, so two searches
at once would each rebuild it — the lock makes the second wait rather than duplicate the work.

**Lexical / metadata search** — exact-ish filters (AND-combined) over the metadata + temporal layers:

```bash
bash scripts/search.sh --title Folgezettel --author human --since 2026-07-01
bash scripts/search.sh --id-prefix 20260722          # everything from one UTC day (spec §4)
bash scripts/search.sh --inhibited                   # corrected notes stay searchable (§6)
bash scripts/search.sh                               # no args: browse the most recent notes
```

Filters: `--id-prefix`, `--text` (body or title), `--title`, `--body`, `--source`, `--author`,
`--since` / `--until` (YYYY-MM-DD, UTC). Common: `-k/--limit`, `--json`, and `--ef`
(semantic breadth). Semantic and lexical are mutually exclusive in v1. `--title` / `--body` /
`--source` are the same substring match as `--text`, narrowed to one field — the same four scopes
the [graph UI's search panel](#graph-ui) offers, from the same function.

`--since` and `--until` are **both inclusive of the day named**, so a closed range reads the way it
looks — `--since 2026-07-19 --until 2026-07-19` returns everything written on the 19th:

```bash
bash scripts/search.sh --since 2026-07-19 --until 2026-07-19   # one whole UTC day
bash scripts/search.sh --since 2026-07-01 --until 2026-07-31   # one whole month
```

The id a search returns is exactly what the write tools take next: `search.sh --like <id>` then
`add-note.sh --parent <id> …` (lineage) or `link.sh suggest … --mode inhibits` (correction) is the
find→link loop.

## Links & provocations

Associative links (`BINDS`) are the emergent, human-ratified graph (spec §7) — distinct from the
`BEGETS` lineage that `add-note --parent` builds. The machine only ever **proposes** a link; you
**ratify** or **reject** it (the "communication partner, not compiler" boundary, §8.2).

**Ratification flow — `scripts/link.sh`** (and the underlying `SUGGEST_LINK ● / RATIFY_LINK ○ / REJECT_LINK`
rules, §12.3):

```bash
bash scripts/link.sh suggest <a> <b> --rationale "both about emergence"   # stage a suggested bind a→b
bash scripts/link.sh list --status suggested                              # review pending proposals
bash scripts/link.sh ratify <a> <b> --mode catalyzes                      # accept it, and say how
bash scripts/link.sh retype <a> <b> --mode inhibits                       # change your mind later
bash scripts/link.sh reject <a> <b>                                       # delete it (a proposal isn't corpus, §2.4)
```

**Modes carry the corpus's only semantics** *(v0.7.0, spec §7)*. A bind's `mode` is optional:

| mode | sign | reading |
|------|------|---------|
| *(untyped)* | 0 | you recognized a relation but reached no verdict — **all the machine may ever propose** |
| `catalyzes` | + | A feeds B across a generational gap (from one line of descent into another) |
| `inhibits` | − | A corrects B — what replaced the old `SUPERSEDES` edge (§6) |

The machine can see that two notes are near each other; it cannot see whether one *feeds* the other or
*corrects* it. So typing is a human act, done at `ratify` time, later via `retype`, or never — an
untyped ratified bind is a perfectly good answer, and is weighted exactly 0 everywhere sign is read.
`ratify`, `retype` and `reject` each write the edge and log an `Op`, and that is all they do
*(v0.8.0)* — nothing has to be compensated elsewhere, because nothing accumulated the sign in the
first place.

Only **ratified** binds count toward fitness, community detection, and the criticality band. Endpoints
may be given in either order — `ratify`/`retype`/`reject` resolve the edge whichever way round it was
stored, and report the stored direction back (with `inhibits`, the direction *is* the claim). `list`
shows each edge's mode and its age from `BINDS.created_at` (or `undated` for older edges), and takes
`--mode` as a filter.

**Seeding after a rebuild.** Ingestion only ever creates `BEGETS`, so a corpus rebuilt from `staging/`
has lineage but no binds. `scripts/seed-binds.sh` replays them from a tab-separated manifest
(`a ⇥ b ⇥ mode ⇥ rationale`, `#` comments ignored), suggesting and then ratifying each row through the
real flow so `created_at` and the `Op` log are genuine rather than back-dated fictions:

```bash
bash scripts/seed-binds.sh staging/manifests/binds.tsv --dry-run   # parse and report, write nothing
bash scripts/seed-binds.sh staging/manifests/binds.tsv             # suggest + ratify each row
```

**Suggestions are mortal; ratified links are not.** The digest proposes faster than anyone ratifies —
an unbounded queue quietly erodes the rule that you decide every link, because what nobody can read,
nobody decides. So three limits hold the queue at human size:

- the digest **stages nothing once 50 suggestions are already standing** (`--max-queue`). This is
  the one that actually bounds it, and it binds from the first run;
- the digest **stages at most 10 per run** and only above a similarity floor (it still *renders*
  everything it found — see [Maintenance loop](#maintenance-loop));
- `scripts/link-expiry.sh` **sweeps `suggested` edges older than 30 days**, weekly. Unratified for a
  month counts as implicitly declined.

**Why the ceiling had to exist** *(v0.8.5)*. The per-run cap is a **rate** and expiry is a **delay**;
neither is a **level**, and only a level bounds a queue. Against a reader who ratifies in occasional
bursts, 10 a night grows until expiry removes as fast as the digest adds — which at a 30-day age
settles near **300** standing suggestions, and does nothing whatsoever for the first month. That is
not a projection: measured on this corpus, five nightly runs staged 50, the sweep expired **0**, and
the earliest date it *could* have removed anything was four weeks out. The ceiling replaces a
calendar with a fact about the reader — how much is standing undecided right now — so the machine's
output rate becomes a function of your ratification rate, which is what "the machine proposes, you
dispose" claimed all along.

A full queue is **not** a stalled job, and the digest says so in the places you would look: the
header of `recent/provocations.md` carries a "the queue is full" note, and the scheduler log names
the limit that bound the run (`queue full at 50` / `room for 3 under the ceiling of 50` / `cap 10`).
Nothing is lost while it is full — every candidate is still rendered, and staging resumes the next
run after you ratify or reject enough to make room.

```bash
bash scripts/link-expiry.sh --dry-run     # what would be swept, and the cutoff
bash scripts/link-expiry.sh               # sweep (Op EXPIRE_LINKS)
```

The sweep is deliberately timid: it deletes nothing at all while the queue is **10 edges or shorter**
(a short list is a to-do list, not a backlog), it never touches a **ratified** edge (that is corpus —
only a proposal may be deleted, §2.4), and it never touches an edge with **no `created_at`**, since it
only deletes what it can prove is stale. Tune with `--max-age-days` / `--keep-min`.

**Provocation move 1 — `scripts/provoke.sh`** — "semantically near, graph-far": for a seed note it
surfaces notes close in meaning but with no short `BINDS`/`BEGETS` path (exactly where an emergent
link wants to form, §8.1). It previews by default; `--stage` writes the candidates as `suggested` links
for you to ratify — it never ratifies on its own.

```bash
bash scripts/provoke.sh --seed <id>                        # preview candidates
bash scripts/provoke.sh --seed <id> --stage                # stage them as SUGGEST_LINKs
bash scripts/provoke.sh --seed <id> -k 8 --depth 2 --min-score 0.5   # tune breadth / graph radius / floor
bash scripts/provoke.sh --seed <id> --no-cache             # bypass the k-NN cache (freshest, slow)
```

Neighbours come from the nightly [k-NN cache](#maintenance-loop), so this returns in seconds instead of
waiting out an ANN rebuild. A seed embedded since the last rebuild is served live automatically (it has
no cached row); for an older seed, a *candidate* written since last night's rebuild can be missing until
the next one — `--no-cache` asks the vector index directly when that matters.

This closes the loop the search tools open: **find → provoke → ratify**. Moves 2–6 (temporal, bridges,
themes, contradictions, re-encounter) run in the nightly [provocation digest](#maintenance-loop).

## Reading sessions (walks)

A **walk** is a read/think session through the graph (spec §11.1, §13): the notes you visit and the
notes the run produces. Saving a walk makes it **replayable** — re-running it against a corpus that has since changed surfaces new provocations (the
stored-program loop; the stored graph is dormant *first entelechy*, a replay is *second entelechy*, §11.6).

**Recording is an operation; reading a walk back is an analytic.** `walk.sh` writes; `analytics.sh`
looks. A walk has no vertex of its own — it *is* its sequence of `Op`s, folded back on demand.

You can also record one **from the map**: the [graph UI's `walk` button](#graph-ui) *(v0.8.3)*
opens a run at the selected note and turns every subsequent selection into a `VISIT` and every note
you compose into a `PRODUCE`, through this same `WalkManager`. It covers the common shape of a
sitting — start, read around, write something, save. `--intent`, `fork` and `delete` stay here,
where they read better than a second click would.

```bash
# open a run at a seed note, record what you visit / produce
bash scripts/walk.sh start --seed 20260722T101500000Z --intent "how does emergence recur?"
bash scripts/walk.sh start --seed 20260722T101500000Z                # no intent: named after the seed
bash scripts/walk.sh visit   <walk> 20260723T115732830Z             # append to the trail
bash scripts/walk.sh produce <walk> <new-note-id>                   # a note the run authored
bash scripts/walk.sh save    <walk>                                 # close it (replayable)
bash scripts/walk.sh fork    <walk>                                 # branch a new run from its seed
bash scripts/walk.sh delete  <walk>                                 # retire a run you don't want

# read them back (read-only)
bash scripts/analytics.sh walks                # every walk, newest first
bash scripts/analytics.sh walk   <walk>        # the ordered trail + what it produced
bash scripts/analytics.sh replay <walk>        # re-provoke from the saved trail
```

Recording writes exactly two things: an `Op` per event, and **+1 to each visited note's `visited`
counter — once per walk, not once per visit**. Coming back to a note three times in one sitting is one
encounter. And no machine job ever touches `visited`, which is what makes it a measure of the reader
rather than of the system.

**Every walk carries an intent** *(v0.8.3)*. Given none, it is named after its seed. Nothing in the
grammar amends a `START_WALK` payload, so a walk that begins anonymous stays anonymous for good and
`walks` can only list it as a timestamp — a derived name is not the stated goal §11.3 means by
top-down control, but it is enough to find the run again. `--intent` still states the real one.

**The trail is the working set** *(v0.8.3)*. Walks used to carry a second, narrower set — the
"registers", nominated by hand with `visit --working` — and `replay` re-seeded from those alone.
It is removed. A declared subset is a stored human judgement about which notes mattered, which is
the shape [v0.8.0](#analytics-measuring-the-graph) spent a version removing from everywhere else;
it had no verb to undo it; and an optional declaration has the failure mode every optional
declaration has — forget it, and a whole sitting replays from one note. The notes a run touched are
the notes it held. Old `SET_WORKING` entries still fold, as the plain visits they always also were:
the log is append-only, so a retired rule stops being written but never stops being read.

`delete` is a **tombstone**, not an erasure — the `Op` log is append-only, so the walk stops being
reconstructed and each note it visited is decremented by one (never below zero).

`replay` re-provokes from the saved trail (move 1: semantically near, graph-far), marks candidates
that **appeared since** the run, and flags any visited note now **inhibited** — it proposes, never
authors (spec §8.2). It is read-only; `walk.sh fork` is the operation that acts on it.

## Analytics (measuring the graph)

`scripts/analytics.sh` answers questions **about** the corpus and **writes nothing to it** — not a
property, not an edge, not even an `Op` recording that a report ran (spec §13). Run any of it as often
as you like; the corpus cannot tell the difference.

That is the design, not a convenience. Before v0.8.0 these numbers were stored — `Note.fitness`,
`Cluster.autocatalytic` and friends — so a nightly job had to rewrite them whenever the graph moved,
and they were wrong in between. Computed on demand they cannot go stale, they cost nothing to redefine
(a fitness weight is a constant in a report, not a migration), and they can be asked of the past.

```bash
bash scripts/analytics.sh communities --members   # detected themes, their hubs and members
bash scripts/analytics.sh autocatalysis           # only the ones that cycle under catalysis
bash scripts/analytics.sh fitness --limit 15      # note standing, and what it's made of
bash scripts/analytics.sh criticality             # sparse / critical / dense
bash scripts/analytics.sh visited --ascending     # what you keep but never revisit
bash scripts/analytics.sh debt                    # what you owe writing (move 6)
bash scripts/analytics.sh communities --as-of 20260701T000000000Z   # the graph as it stood then
```

**Communities** come from stdlib label propagation over the ratified `BINDS` + `BEGETS` graph (no
third-party deps). A community's **hub** is its highest-degree member. There is nothing to ratify and
nothing to name in the database: a community is what the graph looks like *now*, so ratifying one bind
can redraw the boundaries — that is correct behaviour, not instability. If a theme deserves a name,
write a hub note about it and bind it to the members; a keyword is just a note (§10).

**Autocatalytic** is a structural predicate, not a threshold *(v0.7.0)*: it asks whether the catalysis
relation — `BEGETS` ∪ ratified `BINDS{catalyzes}` — **cycles** among the members (spec §3.3, §12.4).
Since `BEGETS` is acyclic, any such cycle must run through at least one ratified `BINDS{catalyzes}` —
lineage alone never closes. The smallest case is a note bound back to something upstream of it (a
conclusion feeding its own premise); the general case is two chains of descent catalyzing each other at
different points. The report prints the **catalytic core** (the members forming the cycle), plus
**`reproduction_rate`** (intra-community catalysis edges per day) and a **local `criticality`** (mean
internal degree). All descriptive — nothing is gated on any of them (§10).

**Structural debt** *(move 6)* is `descendants / (1 + attention)`, where attention is ratified-`BINDS`
degree plus `Note.visited` — the notes the corpus grew out of but the writer stopped going back to.
**The two halves are read off different relations, and that is the whole design**: `BEGETS` is free and
automatic (it exists because you named a parent), while a ratified bind or a walk is expensive and
human. Measure both on the link graph and they cancel — "important" would mean well-linked and
"neglected" would mean poorly-linked, and no note could be both. It is the deliberate inverse of
`fitness`, which credits descendants and so scores exactly these notes as the healthiest in the graph.
There are **no weights to tune**, only a descendant floor and a cap. A note with no descendants never
appears (move 5 already resurfaces orphans), and a parent and its descendant never both appear — neglect
is inherited, so only the higher scorer of a lineage survives. An empty report says *why* it is empty:
"nothing is owed" and "lineage is too shallow to measure" are different messages, and the second means
notes are not being committed with a `--continues`/`--branches` parent. Rendered as the last section of
the nightly digest, and on demand by `analytics.sh debt` (skill: `writing-prompts`; design:
`docs/indexia-prompt-assistant-spec.md`).

**`--as-of <timestamp>`** recomputes against the graph as it stood at that instant, using `created_at`
on `Note`, `BEGETS` and `BINDS`. One honest limit: `BINDS.status` carries no history, so a past view
uses each surviving edge's *current* status — a bind ratified today counts as ratified in a view of last
month, and one rejected since is absent from that view entirely. Note membership and edge existence are
dated exactly.

## Graph UI

A browser view of the graph, and *(v0.8.1)* a place to act on it.

**Reading writes nothing.** Browsing appends no `Op`, touches no property, and above all never
moves `Note.visited`: only a recorded walk may do that (§13.2), and browsing is not walking — a
number both a human and a daemon could increment would measure neither. Since *(v0.8.3)* the view
can record a walk, and that is the same rule rather than an exception to it: the counter moves only
inside a run you opened with the [`walk` button](#graph-ui), and until you press it, clicking notes
writes nothing at all. [`tests/test_ui_readonly.py`](tests/test_ui_readonly.py) pins the reading
half, the payload contract and the traversal guard, by exercising every `GET` route and comparing
the `Op` and `visited` totals either side.

**Writing goes through the same door the CLI does.** Every write below is a call into
`notelib`'s `Ingestor`, `LinkManager` or `WalkManager` — the three writer classes, and the server
composes no SQL of its own — so nothing reaches the graph without the `Op` that records it in the
same transaction (§12.3). There is no new rule, no new validation and no second ingestion path;
the view is simply a fourth caller
alongside `add-note.sh`, `ingest-staging.sh` and `link.sh`.

```bash
bash scripts/ui.sh start        # then open http://localhost:8420/  (also from a Windows browser)
bash scripts/ui.sh status       # process + /api/health
bash scripts/ui.sh stop
bash scripts/ui.sh run --read-only   # the pre-v0.8.1 surface: every write answers 403
bash scripts/ui.sh run --tailscale   # reachable from the tailnet, HTTPS — see below
```

It is **not** started by `up.sh`. Every daemon there is required for correctness; this one is
opt-in, and it holds the root DB password.

**`--tailscale`** binds this machine's Tailscale IP instead of loopback and serves HTTPS
with a cert `tailscale cert` issues for the node's MagicDNS name — the same mechanism `audua` and
`eliciter` use for their own UIs, in [`scripts/tailscale.py`](scripts/tailscale.py). That widens
the write guard's `Host`/`Origin` check by exactly one name — the provisioned FQDN — never a
wildcard, so a DNS-rebinding attempt still has nothing to aim at (see Security posture below and
[`tests/test_ui_write.py`](tests/test_ui_write.py), which pins the widened check the same way it
pins the loopback-only one). There is no fallback to a self-signed cert: if `tailscale` is not
installed or not up, `--tailscale` fails loudly rather than serving weaker TLS. `make ui-up
ARGS=--tailscale` starts it detached; `ui.sh status` reports which mode a running server is in by
reading its own `/proc/<pid>/cmdline`, since `status` carries none of the args `start` did.

**What it shows.** Every note as a node, sized by `fitness` (square-root, so one hub cannot swallow
the viewport) and coloured by community; both edge layers at once — `BEGETS` thin and grey with the
arrow at the child, `BINDS` heavier, `catalyzes` green with a triangle head and `inhibits` red with
a **tee** head (the standard inhibition notation, so the claim never rests on colour alone).
Suggested binds are dashed and faded: a proposal is not corpus. Only the three largest communities
carry a hue — beyond three, adjacent colours stop being reliably distinguishable, so the rest are
neutral and the legend carries their identity instead.

**The left rail** answers two questions, in this order: **view** — how the graph is drawn (layout
mode, `labels`, `re-layout` / `fit` / `reset`) — and then everything below it, **what is in it**.
The two are told apart by control shape as well as by heading: a mode or an action is a button,
and only a filter is a checkbox. The header keeps just the panel group *(v0.8.3)*, since that is
the one thing that fills the right-hand pane.

**Filters** (all client-side; the whole corpus ships in one payload). The default window is the last
6 months. Because `BEGETS` points backwards in time, a recency window would otherwise amputate
ancestry — every note whose parent is older would read as a root with no origin — so the direct
out-of-window neighbours come along as **ghosts**: dimmed, unlabelled, real and clickable, but not
the subject. Edges between two ghosts are not drawn, or the closure cascades and the window stops
meaning anything. `strict` turns the concession off. Beyond the window: edge type, bind mode, bind
status, note status, unlinked notes, and community.

**Selection.** Clicking a note selects it *and its neighbours* — one hop, over the edges actually on
screen, so the selection always agrees with what you can see — and opens the note in the right-hand
panel: full body, Folgezettel address, source, fitness broken into its four terms, its parents,
children and binds, and its nearest notes by meaning, with the ones that are near but *unlinked*
called out (that is provocation move 1, free, from data already on the page). Clicking an edge
selects it and both endpoints, and spells the claim out in words — "A **corrects** B" — because on
an `inhibits` bind the direction *is* the claim. The complement dims rather than the selection
highlighting, which is the only thing that still reads at scale. Background click or `Esc` clears;
`#note=<id>` in the URL selects and centres one on load.

**Shift-click picks a second note, and there is no third** *(v0.8.4)*. Both picked notes wear a
ring, both neighbourhoods light up, and the panel follows the one clicked last. The cap is two
because multi-select exists here so that a **pair** can be named, and a pair is what a bind takes —
a third note would be a selection with no verb behind it. So the third shift-click is refused *out
loud*: pressed twice with nothing happening and no reason given, a control reads as broken.
Shift-clicking a picked note again releases it, which is how you make room; a plain click starts
over with that note alone. Shift does nothing to an edge — shift is the pairing gesture, and only
notes can be paired.

**`search`** *(v0.8.3)* finds a note when you know something about it but not where it sits. Like
`status` it only reads, so it stays on a `--read-only` server. One box of words plus a scope —
**title or body** (the default), **title**, **body**, or **source** — and beside it **author** and
a **from** / **to** day range, all AND-ed together. Empty, it browses the most recent notes. The
filters *are* `search.sh`'s: same `lexical_search` call, same rules, so the panel cannot promise a
result the command line would not stand behind, and `--since D --until D` means all of day D here
too.

Clicking a result **highlights the note on the map and leaves the results up** — it selects the
note and its on-screen neighbours and centres on it, exactly as clicking the node would, but
without swapping the list out for the first thing you clicked in it. That is the same bargain the
`queue` and the community rows in `status` already strike. If the hit is outside the current
window or filters it still highlights, and says so rather than looking like a dead click.
**`open`** beside each row is the other half, for when the answer is the note itself. The query
and its results survive leaving the panel and coming back.

It is **lexical, and there is deliberately no `-q` here.** A semantic query has to embed the query
string and then ask `LSM_VECTOR`, and asking it live rebuilds every vector in the corpus — minutes,
not seconds. Behind a search box that is not slow, it is a hang. The semantic reading is already on
the page: open any note and read its *near in meaning* list, served off the nightly k-NN cache.
`search.sh -q` gives it on the command line, where waiting is a decision you took.

**`link`** *(v0.8.4)* is the other end of the pair gesture: two notes you went and found yourself,
and the bind proposed between them. Every other way this view draws an edge starts from a list the
machine made — *near in meaning, not linked* on a note, the digest's rows in `queue`. This one
starts from you, which is the act §3.3 calls the expensive one ("catalysis across a generational
gap — a human has to see it and say so") and which had no affordance until now.

Two slots stacked vertically, **from** over **to**, because order is the claim: on a `corrects`
bind, from→to *is* the assertion (§6). Fill them by clicking the map — a plain click resets the
form into **from**, a shift-click drops the second into **to** — or by searching. Each slot has its
own box that searches **note bodies only** and shows **three** hits; it asks the server for four so
that *"more than 3 match — narrow it"* is measured rather than guessed. **⇅ swap** exists so that
getting the direction backwards costs one click instead of two fresh searches. The button lights
only when both slots hold different notes.

**⇅ swap greys out once the two notes already stand in a relation** — *any* edge, lineage as much
as association. Swap is a convenience for a form you are still composing, and the rule is about the
pair rather than the edge type: two notes the graph already orders are not two notes this form gets
to reorder on a whim. The tooltip says which case you are in, because they are inert for different
reasons.

With a **`BINDS`** it is inert outright: the panel renders from the edge's **stored** direction, so
reordering two labels would change nothing while appearing to. Turning it around for real is
`REJECT_LINK` then `SUGGEST_LINK` the other way — two `Op`s, one of which deletes a proposal — and
that is not something a hinge between two slots should do quietly. The tooltip points at the
`reject` sitting directly below it; reject, and the swap frees up with both notes still in place.

With a **`BEGETS`** flipping *would* change what gets proposed, and that is precisely why it should
not be one click. Lineage cannot be reversed, so a bind against its grain is an assertion about a
pair whose order is already settled (§3.3) — real, sometimes exactly right, and never accidental.
Picking both notes again in the other order still does it. That is the whole of the design: the
cheap gesture goes, the deliberate one stays.

While `link` holds the pane it is also a **mode**: clicking a note fills a slot instead of opening
that note, so the form is still there to see it happen. Anything else that fills the pane ends the
mode, because a form off screen taking clicks is a write affordance nobody can see.

It **proposes** (`SUGGEST_LINK`) and nothing else — there is no one-step ratified bind anywhere in
the grammar, and §12.8 asks a view that writes to be a new *caller* of the ingestion path and not a
new path. What follows is not a second gesture bolted on: the pair now has an edge, so the panel
repaints and renders that edge's own verdict buttons — the same ones `queue` shows. Suggest and
ratify stay two acts (§8.2); they just no longer need two panels. Pick two notes that are **already
bound** and you get the verdict straight away with no button to propose, since a pair holds at most
one bind in one direction (§7) — said in the panel rather than earned as a 409.

**What it writes** *(v0.8.1, extended v0.8.4)*. Five things, all of them existing rules:

- **`+ note`** commits a note (`ADD_NOTE`). With one selected, *continue* and *branch* carry the
  `BEGETS` parent across, which is the whole point of writing from the map rather than beside it.
  It commits **without a vector** — an Ollama call inside a click is a hang, and `Note.embedding
  IS NULL` already *is* the queue that `embed-worker.sh` drains every few seconds.
- **`link`**, on any note in the *near in meaning, not linked* list, proposes a bind
  (`SUGGEST_LINK`) — move 1 made actionable, and the crossing this section used to name as the
  first one worth making. It proposes; it does not ratify. The `link` **panel** *(v0.8.4)* is the
  same rule reached from the other direction — a pair you chose rather than one the machine put in
  front of you — and it proposes just as narrowly.
- **`queue`** is the ratification queue: every suggested bind with its claim written out, and the
  four verdicts (`RATIFY_LINK` as catalyzes / corrects / untyped, or `REJECT_LINK`). The machine
  proposes and the human disposes (§8.2), and that split is exactly what the two buttons are.
- **`edit`** fixes a typo in place (`CORRECT_COSMETIC`) — the corpus's one in-place mutation, and
  it is refused if the embedding drifts past the threshold. A refusal offers §6's actual remedy in
  one click: commit the correction as its own note, bound back with a ratified `inhibits`.
- **`inbox`** previews `staging/` through the real dry run and ingests it, filing each file
  under `processed/` or `failed/` exactly as `ingest-staging.sh` does. It also takes **file
  drops** — see below.

**`walk`** *(v0.8.3)* records a reading session from the map (§11.1). It is a **mode**, not a
panel, which is why it stands outside the panel group — while it is on, selecting a note means
something it did not mean a moment ago.

- **Grey and disabled** until a note is selected: a walk begins somewhere, and there is nothing to
  seed it with.
- **Green** with a note selected. Pressing it opens a walk at that note (`START_WALK`, which
  visits the seed at `seq` 0).
- **Red**, reading `save walk · N`, while the run is open. Every note you then select — clicked on
  the graph, opened from the search results, or reached by `#note=` — is appended to the trail
  (`VISIT`). Every note you commit from `+ note` is recorded as the walk's own (`PRODUCE`).
- Pressing it again closes the run (`SAVE_WALK`) and the graph re-reads, so `visited` and the
  fitness it feeds catch up. Saved walks replay and fork:
  [`analytics.sh walk <id>`](#analytics-measuring-the-graph), `walks`, `replay`.

**This is the only thing in the whole view that may move `Note.visited`**, and the button is how
that stays true. §13.2 reserves the counter for recorded walks so that it measures human attention
rather than machine activity, and browsing is still not walking: a visit needs a walk id, a walk id
exists only because somebody opened one, and until they do, clicking notes writes nothing at all.
The mode is visible in three channels at once — the label, `aria-pressed`, and the colour — because
a recorder you cannot tell is running is worse than none.

Two rules inherited from `walk.sh` rather than reinvented, both in `WalkManager`: a note counts
**once per walk** however often you come back to it (the re-visit is still logged as the trail
event it is, so the page can send every selection without keeping the trail itself), and **producing
a note is not visiting it** — which is why composing during a walk cannot inflate its own count,
even though the new note is selected the moment it commits.

An open walk survives a reload: the page asks `GET /api/walk` on load, because a walk lives in the
`Op` log and nowhere else, and a refresh that orphaned one would leave it recording nothing and
never closing. That also picks up a walk opened from `walk.sh` — the same person's unclosed
sitting, now with a button that can close it.

Not on the button, deliberately: **intent** — a prompt in front of a one-click gesture is friction
on the thing the button exists to make cheap, and the walk is not left anonymous for it, since
every walk is now named after its seed when none is given (`walk.sh start --intent` still states a
real one) — and
**fork** and **delete**, which are decisions about a walk you are no longer taking and belong
wherever past walks are listed — which is nowhere in this view yet. Batch-ingested notes are not
counted as produced either: those were authored before the sitting and merely imported during it.

**Dropping files** *(v0.8.2)*. The `inbox` panel has two drop zones: typed notes
(`.md .txt .note`) into `staging/`, handwritten ones (`.jpg .png .pdf`) into `staging/scans/`. Either
zone accepts anything and the **extension** decides where it lands, so a photo dropped on the
typed one is still a photo; the reply says where it went. Click a zone to browse instead.

A dropped file is **not yet a note, and parking one is not an operation**: no `Op` is logged,
nothing is embedded, and nothing reaches the graph until you ingest it. That is why the drop and
the ingest are two clicks and not one.

- A file already named for a spec §4 id keeps it — which is what makes re-dropping an exported
  note, or rebuilding a corpus with its real history, work. Anything else is given a fresh id,
  and the name it arrived as becomes its `source_ref`.
- That `source_ref` is written **behind a `---` fence**, and only when the file has no header of
  its own. Without the fence `parse_props` would fold the whole body into the `source_ref` value
  and leave the note with no body at all. The fence also fixes an older hazard in passing: a
  plain-prose file containing a markdown `---` divider used to lose everything above the divider
  and ingest anyway, because the parser overwrites what it collected on reaching a fence. Ours is
  now the first one. YAML front matter is unwrapped and a leading BOM stripped, both for the same
  reason — either would otherwise swallow the header.
- An id already staged is **refused, never overwritten**, and refusal looks only at `staging/`:
  `processed/` and `failed/` hold ids that have had their turn, and consulting them would break
  the re-drop above.
- `staging/scans/` is **park only**. Turning handwriting into notes takes a person and a model reading
  the page together, with a ratification step in the middle — the `transcribe-notes` skill. The
  panel lists what is waiting and says so, rather than offering a button that cannot do it.

A write re-reads the corpus and redraws in place. Nothing moves — a graph that rearranged itself
every time you committed a note would have no places left in it, which is the whole claim behind
§11.3's spatial map. New notes arrive at their deterministic seed position.

**Why a page you visit cannot write to your corpus.** The server binds loopback and has no
authentication, which cost nothing while it only answered `GET`: a hostile page can send a
cross-origin request, but it cannot read the reply. A write needs no reply. So a write must carry
`Content-Type: application/json` **and** an `X-Indexia-Write` header, neither of which a
cross-origin request can set without a CORS preflight — and the server answers no `OPTIONS`, so
the preflight fails and the request is never sent. A plain `<form>`, the one thing that can POST
cross-origin without asking, can set neither. The `Host` header is checked too, which is what
makes "loopback only" survive a DNS name pointed at `127.0.0.1` — and under `--tailscale` the same
check accepts exactly one more `Host`/`Origin` value, the tailnet FQDN the cert was issued for,
never a wildcard.
[`tests/test_ui_write.py`](tests/test_ui_write.py) pins all of it, including the 501 on `OPTIONS`
— that one is not a detail about an unimplemented verb, it *is* the defence.

This is also why an uploaded file arrives as **base64 inside a JSON body** rather than as a
multipart form. `multipart/form-data` is precisely the one content type a cross-origin `<form>`
*can* send with no preflight, so accepting it would dissolve the guard above. The size cap moves
for that one route only — 16 MiB decoded, ~24 MiB on the wire — because a photograph of a
notecard is megabytes and everything else here is still prose, which at 1 MiB it should stay.

**`status`** *(v0.8.2)* reads and never writes, so unlike the buttons above it stays on a
`--read-only` server. It shows the four scheduled jobs with their cadence, last run and computed
next run; whether `scheduler`, `embed-worker` and `ui` are alive; the tail of the scheduler log;
and the corpus measured on demand — criticality with its advice, the fittest notes, the ones
never revisited, communities with their reproduction rate, and cache/backlog/queue health. The
numbers are the same ones [`analytics.sh`](#analytics-measuring-the-graph) prints, from the same
functions.

It opens immediately and fills in, because gathering it takes a second or two the first time.
The cost is the **number of round trips to ArcadeDB** — eight of them, each 80–400 ms over HTTP —
and not the size of the answers or the graph arithmetic on top: the community/autocatalysis pass
that looked like the expensive part measures 15 ms. A 60-second cache makes every later open
instant, and any write drops it.

There is deliberately **no way to run a job from it.** `knn-cache` absorbs the one `LSM_VECTOR`
rebuild — minutes, not seconds — and a button that can hang the page for that long is not a
convenience; the scheduler takes anything due within the tick anyway. What the panel is for is
noticing that it hasn't. Note that nightly and weekly jobs only fire after 02:00 UTC, so between
midnight and 02:00 nothing is due.

**Layout** (top of the left rail, under **view**). `force` by default; `timeline` pins the vertical
axis to creation order so lineage reads top-to-bottom. Positions are seeded from a hash of the note
id, so the same corpus lays out the same way on every reload and on any machine; drag a node and the
position is remembered (`reset` forgets them). **Filtering never re-lays out** — re-layout on
interaction is what makes a graph feel like `pyvis`, and it destroys the spatial memory that makes a
map worth having (§11.3 mod 6). `re-layout` and `reset` are the only two controls that move a node,
which is why they sit together and why the rail says so.

**`labels`** turns node labels off for the whole graph, leaving pure structure — the better reading
once a corpus outgrows legible labels, and the cheapest frame available, since the text pass is
where a labelled graph spends itself. Off stays off through a selection, and the choice persists
across reloads; ghosts stay unlabelled either way.

The renderer is a single vendored, pinned [Cytoscape](ui/vendor/README.md) file — no npm, no build
step, no CDN, works offline. `ui/graph.js` is the only file that knows it exists.

## Embedding

Notes are embedded for the semantic layer (spec §7) by a **local, private** Ollama server
(`mxbai-embed-large`, 1024-dim — matching the `LSM_VECTOR` index). Embedding is **asynchronous**: a
commit stores the note instantly as `pending-embed` and a background **worker** embeds it a few
seconds later (each embed is CPU-bound and can take a while on a modest box, so note-taking never
waits on it). Provision once, then run the daemon + worker (`up.sh` starts both):

```bash
bash scripts/setup-ollama.sh          # one-time: install Ollama (no sudo) + pull the model
bash scripts/embed-server.sh start    # the model daemon (status | stop | warm)
bash scripts/embed-worker.sh start    # the background embedder (status | stop)
```

- **The worker** polls for notes with no embedding and embeds them (each logs an `Op(EMBED)`).
  `embed-worker.sh status` shows how many are pending. `embed-backfill.sh` is the equivalent
  one-shot pass (handy after a bulk import or a stretch with the embedder down).
- **Embed inline instead.** `--embed` on `add-note`/`ingest-staging` embeds at commit for that call
  (blocking); `INDEXIA_EMBED_ON_COMMIT=sync` makes inline the global default. `--no-embed` forbids it.
- **Fail-open.** Nothing blocks on the embedder: if it's down, notes just stay `pending-embed` until
  the worker (or a backfill) catches up.
- **Config** (optional; defaults in [`docker/.env.example`](docker/.env.example)):
  `INDEXIA_EMBED_BACKEND` (`ollama`|`none`), `INDEXIA_EMBED_MODEL`, `INDEXIA_EMBED_DIM`,
  `INDEXIA_EMBED_ON_COMMIT` (`async`|`sync`), `OLLAMA_HOST`, `OLLAMA_KEEP_ALIVE`.
- Changing the model/dimension means changing `INDEXIA_EMBED_DIM` **and** the `LSM_VECTOR` index
  dimension in `ddl/schema.sql` (spec §10, the one tuning knob), then re-embedding.

## Maintenance loop

Background jobs on a cadence, run by an in-repo **scheduler daemon** (`scripts/scheduler.sh`, started
by `up.sh` / stopped by `down.sh`, like the embed worker) — no cron or root needed. The §12.6 table, as
built:

| Job | Cadence | What it does |
|-----|---------|--------------|
| `knn-cache` | nightly | materialize every embedded note's top-k neighbours, so the moves below pay no ANN rebuild |
| `provocation-digest` | nightly | run all six moves → `recent/provocations.md`; stage the strongest move-1 suggestions (§8.1) |
| `resurface` | weekly | re-encounter orphan/inhibited/anniversary notes → `recent/resurface.md` (§8.1 move 5) |
| `link-expiry` | weekly | sweep `suggested` links older than 30 days, keeping the queue human-sized (§7) |

The same table is readable in the [Graph UI](#graph-ui)'s `status` panel, with each job's last
run and a computed next one beside it — the arithmetic is `scheduler.next_run`, which is `_due`
read backwards and pinned against it over a two-week grid in
[`tests/test_scheduler_status.py`](tests/test_scheduler_status.py). Worth knowing what that grid
turned up: **`_due` is not monotonic in time.** Its nightly test is `now.date() > last.date() and
now.hour >= 2`, so a job that ran Monday is due at 02:00 Tuesday, stays due all Tuesday, then
goes *un*-due at midnight and is due again at 02:00 Wednesday. Between midnight and 02:00 UTC no
nightly or weekly job is due at all.

**Every job here writes** *(v0.8.0)*. Four that used to live in this table — `activation-decay`,
`fitness-recompute`, `community-detect` and `criticality-monitor` — existed only to keep stored
analytics from going stale, and with nothing stored there is nothing to refresh. Their measurements
moved to [Analytics](#analytics-measuring-the-graph), which computes them on demand.

The corpus is still watched at the **edge of chaos** (spec §11.3), but it is *watched*, not regulated:
`analytics.sh criticality` reports the band and the human links or writes in response. That was always
the real behaviour — the old criticality monitor only ever logged its reading; nothing acted on it —
and v0.8.0 states it plainly. The loop closes through you.

**The nightly order is load-bearing.** Adding one embedding invalidates the whole `LSM_VECTOR` index,
so the *next* vector query rebuilds all N vectors (~11 min at 101 notes, measured). Asking per seed, the
six-move digest used to pay that over and over — one run went 36 minutes without finishing. Two changes
fixed it, and both depend on the ordering:

- `knn-cache` runs **first**, absorbing the single rebuild and writing the neighbour lists that
  `provocation-digest` then reads instead of querying vectors itself;
- the vector-touching jobs first **wait for the embed queue to drain**, so the corpus has stopped moving
  before the first query. That wait is *fail-open*: after a bounded timeout the job logs the pending
  count and runs anyway, because a stuck embed worker must never be what stops the nightly batch.

Measured on the 101-note corpus: a full six-move digest now runs in **6 seconds and makes zero vector
queries** — it is served entirely from the cache, so an invalidated ANN index cannot slow it down at all.

`knn-cache --if-stale` (how the scheduler calls it) skips the rebuild entirely unless the set of embedded
notes has changed, so a quiet day costs one cheap comparison instead of an ANN rebuild.

```bash
bash scripts/knn-cache.sh --status          # coverage + whether the cache is stale, and why
bash scripts/knn-cache.sh --if-stale        # rebuild only if it no longer covers the corpus
```

A staleness reason **names the notes**, not just how many (`1 embedded note(s) not in the cache
(20260716T115559413Z)`, up to three then "and N more"). The count alone cannot tell you the two
cases apart: a note written an hour ago and not yet cached is the loop working, whereas an old note
that has lost its row is something having gone wrong — and the second is what [`tests/lib.py`](tests/lib.py)'s
`knn_cache_guard` exists to catch. `test_knn_cache.py` deletes and corrupts rows on purpose and puts
them back in `finally`, but a `finally` does nothing for a process that is killed, and the file's
destructive window is also its slowest stretch. That happened: a killed run left the live cache one
note short with every corpus count reading clean, and it stayed that way until a nightly `--if-stale`
happened to notice. `corpus_guard` could not have caught it — `KnnCache` is derived data, not corpus,
and by its definition nothing had happened. The cache guard repairs the leak and fails a check, so
the next run reports it rather than silently inheriting it.

**A short cached answer beats a complete slow one.** The cache stores 50 neighbours per note, but move 1
subtracts the seed's whole graph-near neighbourhood first — on this corpus that removes 24–76 notes, so a
seed often has only a handful of survivors left. Those short answers are served as they are rather than
re-querying the index: falling back whenever the cache yielded fewer than `k` is what turned one 8-second
digest into 654 seconds, because a single heavily-linked seed dragged the whole run through a rebuild.
A live query happens only when the cache has *no* row for the seed, or when exclusions removed
everything. Use `--no-cache` on `provoke.sh` / `analytics.sh replay` when you want completeness over
speed.

```bash
bash scripts/scheduler.sh status            # UP / DOWN  (start/stop are wired into up.sh/down.sh)
bash scripts/scheduler.sh run --once        # run every currently-due job once, in the foreground
bash scripts/scheduler.sh run --force       # run every job now regardless of cadence (for testing)
```

Every job also runs **standalone** via its own `scripts/*.sh` wrapper — handy on demand or from cron:

```bash
bash scripts/knn-cache.sh                   # rebuild the k-NN adjacency cache (Op KNN_CACHE)
bash scripts/provocation-digest.sh --no-stage   # render the six-move digest without staging links
bash scripts/resurface.sh                   # weekly re-encounter digest (Op RESURFACE)
bash scripts/link-expiry.sh --dry-run       # what the suggestion sweep would delete (Op EXPIRE_LINKS)
```

**Staging is capped, rendering is not.** `provocation-digest` lists every candidate it found but stages
only the strongest **10 per run** (`--stage-cap`), above a move-1 similarity floor of **0.65**
(`--min-score`), and only while fewer than **50** suggestions are already awaiting a decision
(`--max-queue` — see [Links & provocations](#links--provocations) for why a per-run cap alone does
not bound a queue). Moves 1 and 2 score on opposite axes — move 1's score is similarity (higher is nearer),
move 2's is the same cosine read as distance (lower means further apart, which is what makes a note you
held in mind the same hour and never joined interesting). So the floor applies to move 1 only, and the two
are ranked on their own axes then interleaved, which is what stops either move from crowding out the
other under a shared cap. The digest is a surface to read; the queue is a list to decide.

**Weekly means every seven days, not "in the nightly batch."** `_due` asks a weekly job for a full
week elapsed *and* an hour past 02:00 UTC, and that second half only excludes midnight–02:00 — so a
weekly job keeps whatever time of day it first ran at. `resurface` currently lands around 22:35 UTC
and `link-expiry` around 18:43. Worth knowing for `link-expiry` in particular, which deletes edges:
it can sweep the ratification queue while you are reading it.

**Two of the four jobs write no `Op` when they change nothing** — `knn-cache --if-stale` against a
fresh cache, and `link-expiry` with nothing stale. That is [§13's rule](#analytics-measuring-the-graph)
(anything that appends no `Op` changed no state) reaching the maintenance loop, not a gap. But it
does mean the append-only log is not the record that those two ran, and cannot be: `scheduler.log`
and `scheduler-state.json` are. Both jobs now log `· no Op (nothing changed)` so a quiet log line is
telling you something, rather than leaving you to wonder whether the job fired at all.

Each job is **fail-open** (a job that can't reach the DB logs and retries — it never kills the loop),
but a job that fails *every* time **backs off** rather than hammering: 15 minutes after the first
failure, doubling, capped at 6 hours, cleared by one success. Fail-open alone was not enough, because
the cadence is no brake — a nightly job stays due from 02:00 until midnight, so an always-failing one
had a twenty-two-hour retry window, and one that fails by *timing out* burns its whole timeout every
attempt. The status panel reads the backoff too (`blocked_until`), so it never shows `due` for a job
the daemon is deliberately not running.

**Timeouts are per job, and sized to catch a hang rather than to bound cost.** `knn-cache` gets 4 h
against a 30 min default: it is O(N) round trips plus the absorbed ANN rebuild, its recorded run
times are spread from 133 s to 1149 s with the *slowest* being the 25-note run (what dominates is
whether the vector index was cold, not the corpus size), and it may spend 600 s waiting for the embed
queue inside the same budget. It also commits the whole pass in a single transaction at the end, so
a kill at 99% throws all of it away — the cheap failure is to let a slow run finish.

The nightly hour is UTC; last-run state lives in `~/.indexia/scheduler-state.json`, and a
job removed from the schedule takes its entry there with it. Every log line is stamped UTC — without
that a long-running daemon's old failures sit at the tail of the log looking current, which is
exactly how a bug fixed days earlier gets rediagnosed. Prefer OS cron? The `*.sh` wrappers are
directly cron-usable — the daemon is just the zero-setup default.

**The three daemons** (`scheduler.sh`, `embed-worker.sh`, `ui.sh`) each track their process with a
pid file in `~/.indexia/<name>.pid`, written on `start` and verified on `stop`/`status` (alive, and
its command line still names the script — so a reused pid is never mistaken for the daemon). They
used to find themselves with `pgrep -f scripts/<name>.py`, which matches any command line
*containing* that text: a shell invoked as `bash -lc '… scripts/ui.py …'` matched, so `stop` killed
the caller. A daemon started before pid files existed is adopted on the next `status`.
[`tests/test_daemon_pid.py`](tests/test_daemon_pid.py) pins it.

## Operating & inspecting

Day-to-day operation and looking under the hood.

| Task | Command |
|------|---------|
| Start / stop the stack | `bash scripts/up.sh` · `bash scripts/down.sh` (`--backup`, `--reset`) |
| Also serve Studio/REST over Tailscale | `bash scripts/up.sh --tailscale` — adds the tailnet alongside loopback; binary protocol (2424) stays loopback-only either way |
| Health check | `bash scripts/status.sh` — containers, readiness, database list |
| Follow logs | `bash scripts/logs.sh [N]` — last `N` lines of the server log (default 100) |
| Interactive SQL | `bash scripts/console.sh` — an ArcadeDB SQL prompt against `indexia` |
| End-to-end self-test | `bash scripts/smoke-test.sh` — insert + `vector.neighbors` + traversal, self-cleaning |
| Regression tests | `bash tests/run.sh` — see [Tests](#tests) |
| Re-apply schema | `bash scripts/apply-ddl.sh` — idempotent; run after editing `ddl/schema.sql` |
| Mint note ids | `bash scripts/new-id.sh [N]` — N fresh spec §4 ids (used by transcribe-notes, review-transcripts) |
| Rotate dev secrets / cert | `bash scripts/gen-env.sh` · `bash scripts/gen-cert.sh` |

**Reading the rewrite log.** Every state change appends an `Op` (spec §11.2), so the log *is* the
audit trail. Inspect it in the console (`bash scripts/console.sh`):

```sql
SELECT rule, id FROM Op ORDER BY id DESC LIMIT 20;         -- recent operations
SELECT rule, count(*) AS n FROM Op GROUP BY rule;          -- histogram of rule types
SELECT FROM Op WHERE rule = 'RATIFY_LINK' ORDER BY id DESC; -- inspect a rule's JSON payloads
```

The rule grammar, as built: `ADD_NOTE`, `EMBED`, `CORRECT_COSMETIC`,
`SUGGEST_LINK`/`RATIFY_LINK`/`RETYPE_LINK`/`REJECT_LINK`/`EXPIRE_LINKS`,
`START_WALK`/`VISIT`/`PRODUCE`/`SAVE_WALK`/`FORK_WALK`/`DELETE_WALK`,
`PROVOKE_DIGEST`, `RESURFACE`, `KNN_CACHE`, `PROMOTE_TYPE`, `BACKFILL_LINK_DATES`,
`MIGRATE_V0_8_0`. (`seed-binds.sh` has no rule of its own — it replays a manifest as real
`SUGGEST_LINK` + `RATIFY_LINK` pairs.)

**The converse is the useful half** *(v0.8.0)*: anything that appends no `Op` changed no state. The
analytics layer appends nothing, which is how "it only reads" is *checked* rather than asserted — see
[`tests/test_analytics_readonly.py`](tests/test_analytics_readonly.py). The walk rules are also the
one case where the log is the *only* record: a walk has no vertex, so `START_WALK` and its successors
**are** the walk, and `DELETE_WALK` is a tombstone rather than an erasure.

Removed in v0.8.6: `PROPOSE_VARIANT`/`COMMIT_VARIANT`/`REJECT_VARIANT`, and with them
`Note.status = 'proposed'`. The draft never lived in the graph — the assistant proposes it in
conversation and `staging/` holds it as a file, so what reaches the corpus is already the approved
note (spec §12.7). No log holds an instance of any of the three.

Removed in v0.8.0, if you see them in an old log: `DECAY`, `FITNESS_RECOMPUTE`, `CRITICALITY`,
`COMMUNITY_DETECT`, and the `CRYSTALLIZE_CLUSTER`/`RATIFY_CLUSTER`/`REJECT_CLUSTER`/`PROMOTE_HUB`/
`DERIVE_CLUSTER`/`RECOMPUTE_CLUSTER` family. The `START_TRACE`/`SAVE_TRACE`/`FORK_TRACE`/`DELETE_TRACE`
rules were renamed to their `*_WALK` equivalents.

## Tests

Regression tests for the places where a bug is quiet — the vector-access path, the suggestion queue's
lifecycle, the date arithmetic. Zero-dependency like everything else: each file prints one line per
check and exits 0/1, and `tests/run_all.py` totals them. Full detail in [`tests/README.md`](tests/README.md).

```bash
INDEXIA_TESTS_FAST=1 bash tests/run.sh --docs   # the whole suite + docs lint, ~20s
bash tests/run.sh                 # everything, including the slow live-fallback checks
bash tests/run.sh --unit          # only the tests that need no database (~1s)
bash tests/run.sh knn expiry      # only files matching these substrings
```

Two checks verify the cache's live fallback by issuing a real vector query, which on a cold ANN index
rebuilds the whole graph first (~10 min) — so a full run is either ~20s or ~8min depending on the
index's state. `INDEXIA_TESTS_FAST=1` skips exactly those two and keeps everything else.

**They run against the live corpus** — there is no fixture database. That is deliberate (every bug
they exist to catch was found *because* the corpus was real and densely linked), which makes cleanup a
safety property: `tests/lib.py` provides `corpus_guard()`, which fails a test whose
`Note`/`BEGETS`/`BINDS` counts don't match afterwards, and `synthetic_links()`, which plants real suggested
edges and deletes exactly those on exit. Anything testing the expiry sweep plants its own edges rather
than risk one of yours. Run `bash scripts/backup.sh` first if you'd rather be certain.

Two tests deserve a note. [`test_knn_cache.py`](tests/test_knn_cache.py) asserts that a full digest
pass issues **zero** vector queries — the property that stops an ordinary note-taking session from
putting an eleven-minute ANN rebuild in front of the nightly run.
[`test_db_invariants.py`](tests/test_db_invariants.py) pins the ArcadeDB behaviours the code leans on,
and is the tripwire for the one bad result described under [Status & roadmap](#status--roadmap) that
has never reproduced.

## Backups

- **On demand:** `bash scripts/backup.sh` → hot, non-blocking zip in `./backups/indexia/`.
- **Scheduled:** ArcadeDB's built-in **AutoBackupSchedulerPlugin**, configured in
  [`docker/backup.json`](docker/backup.json) — nightly at 02:00 (`cron 0 0 2 * * ?`), keeping the
  last 14 files. Edit that file to change the schedule/retention (takes effect on restart).
- **Restore:** `bash scripts/restore.sh <zip> [target-db]` restores into a *new* database; run
  `down.sh && up.sh` afterward so the server registers it.

## Security posture

- **Loopback-only** (`127.0.0.1`) port binds by default; the DB is never on the LAN.
- **TLS on** (self-signed keystore, gitignored under `certs/`).
- Only HTTP(S) + binary ports published; Postgres/Gremlin/Mongo/Redis plugins are **off**.
- Secrets live in `docker/.env` (gitignored, `0600`). They are auto-generated dev values; the root
  password only takes effect on a **fresh** `./data` (reset with `down.sh --reset` to rotate).
- The one deliberate exception to loopback-only is `up.sh --tailscale`
  ([`scripts/tailscale.py`](scripts/tailscale.py)): it *adds* a second publish of **HTTPS/Studio
  (2480) only** on this machine's Tailscale IP, with a `tailscale`-issued cert — the loopback
  publish stays up too (`docker-compose.tailscale.yml` merges an extra `ports` entry rather than
  replacing the base file's), so local tooling is unaffected. **The binary protocol (2424) is
  never parameterized** — it doesn't appear in the overlay at all, so there is no flag that could
  widen it even by mistake. Unlike the graph UI's guarded write surface, ArcadeDB itself has no
  equivalent CSRF-style guard — root-password auth over HTTPS is the entire boundary, exactly as
  it is on loopback today, so a device on your tailnet that has (or guesses) that password gets
  the same direct read/write access to the whole graph a local `curl` would. Nothing is persisted
  in `.env`: a plain `up.sh` next time drops the tailnet publish. No fallback to a self-signed
  cert on failure — if Tailscale is unavailable, `--tailscale` errors out rather than serving
  weaker TLS.
- The **graph UI** (`ui.sh`) holds the root DB password and serves note bodies over **plain HTTP**
  by default. It binds `127.0.0.1` only and has no authentication, because it has no non-loopback
  surface — **do not widen the bind casually**, which matters more since *(v0.8.1)* it also writes.
  The browser never talks to ArcadeDB directly; that is what keeps the password server-side.
- The one deliberate exception is **`--tailscale`**: it binds the machine's Tailscale IP
  and serves HTTPS with a `tailscale`-issued cert for the node's MagicDNS name
  ([`scripts/tailscale.py`](scripts/tailscale.py)), and nothing else — a raw `--host 0.0.0.0` is
  still unsupported and still the wrong move. No fallback to a self-signed cert on failure: if
  Tailscale is unavailable, `--tailscale` errors out rather than serving weaker TLS.
- Its **writes are guarded against the browser itself**, not just the network: `Host` must name the
  server (so a DNS name resolving to `127.0.0.1` does not qualify), `Origin` must be us when sent,
  and every write must carry `Content-Type: application/json` and `X-Indexia-Write`. Those last two
  are unsettable cross-origin without a CORS preflight, and no `OPTIONS` is answered — **adding a
  `do_OPTIONS` handler would silently undo this.** Bodies are capped at 1 MiB, refused on the
  declared `Content-Length` before a byte is read. Run `ui.sh run --read-only` to drop the write
  surface entirely. Under `--tailscale`, the same `Host`/`Origin` check accepts exactly one more
  name — the provisioned FQDN — never a wildcard, so this guarantee holds on the tailnet too.

## Layout

```
docker/   docker-compose.yml · docker-compose.tailscale.yml (--tailscale overlay) · backup.json
          .env.example (.env is generated, gitignored)
ddl/      schema.sql
scripts/  all tooling — thin *.sh wrappers over the shared Python core (lib.sh + notelib.py)
          analytics/  the read-only report layer (spec §13) — imports notelib, never writes
ui/       the graph view served by scripts/ui.py — index.html · app.js · graph.js · write.js
          search.js · status.js · link.js (panels that only read; link.js renders write.js's
          markup and hands it back to Write.bind) · style.css
          vendor/     one pinned Cytoscape build, checked in (no npm, no build step)
tests/    regression suite (run.sh · lib.py harness · test_*.py · tools/) — see tests/README.md
staging/  drop zone for typed note files (README tracked; drops gitignored)
          scans/        image inbox for the transcribe-notes skill (README tracked; images gitignored)
          transcripts/  audio-transcript inbox for the review-transcripts skill (README tracked; transcripts gitignored)
recent/   generated digests: recent-notes.md, provocations.md, resurface.md (gitignored, overwritten)
certs/    keystore.p12   (gitignored)
          tailscale/  cert.pem · key.pem · keystore.p12, re-provisioned each --tailscale
                      run (UI and DB share the same FQDN's cert; gitignored)
data/     ArcadeDB databases   (gitignored, bind mount)
backups/  backup zips          (gitignored, bind mount)
docs/     spec.md
.claude/  skills/ (add-note · ingest-staging · transcribe-notes · review-transcripts · search-notes · walk · analytics · writing-prompts) · plans/ · settings.local.json
```

## Status & roadmap

**Build order complete, plus the trailing v1 rules.** Spec §12.8 steps 1–7 are all built and
live-validated (ingestion → read + links + move 1 → the signed associative layer → walks →
communities & autocatalysis → the maintenance loop → the analytics split). The last spec'd v1 pieces
are wired too:

- **`CORRECT_COSMETIC`** (§6, §12.3) — `add-note --correct <id>`, the *only* in-place edit, guarded by embedding drift (≥ threshold ⇒ refused → issue a correction instead).
- **`PROMOTE_TYPE`** (§3.3, §12.3) — `promote-type.sh` registers a new vertex/edge type (schema growth / "unprestatability").

**Analytics separated from operations** *(v0.8.0, §13)*. The graph is now purely relational: `Note`
joined by `BEGETS` and `BINDS`, and nothing else. `Note.fitness`, `Note.activation`, the `Trace` and
`Cluster` vertex types and their six edge types are gone; everything they held is computed on demand
by `analytics.sh`, which writes nothing at all. Two supports made it possible — `Note.visited`, which
counts human-directed walks (the one thing no amount of graph reading recovers), and `created_at` on
`BEGETS`, which makes the whole graph dated and every report answerable `--as-of` a past instant.
Migrate an existing corpus with `scripts/migrate-v0-8-0.sh`.

**Hardened by integration testing** against a 101-note corpus. What the corpus taught, and what
changed as a result:

- **The ANN rebuild dominated everything.** One new embedding invalidates the whole `LSM_VECTOR`
  index, so per-seed vector queries each paid a full rebuild — a six-move digest ran 36 minutes
  without finishing. Now the vector layer is materialized nightly (`knn-cache`, read via the
  `KnnCache` document type) and the vector-touching jobs wait for the embed queue to settle first.
  The digest is down to **6 seconds and zero vector queries**.
- **Nulling an indexed edge property leaves a stale index entry.** With an index on
  `BINDS.created_at`, `UPDATE … SET created_at = null` does not remove the row's old entry, so
  `WHERE created_at < date(…)` still matches a row whose value is null — which is how the first
  version of the expiry sweep deleted the undated edges it promised to spare. Root-caused by replaying
  the original sequence against a restore of the pre-backfill backup; reproducible on demand in
  [`tests/test_db_invariants.py`](tests/test_db_invariants.py). Value-to-value updates are fine, and
  without the index nulls are excluded correctly — hence no index on that property, plus the explicit
  null guard. A second symptom seen at the same time (17 rows where 12 matched, two duplicated) did
  **not** reproduce even under the faithful replay; likely the same class of fault, but uncharacterized.
- **The suggestion queue outgrew its human** (173 suggested against 18 ratified). Staging is now
  capped per run with a score floor, `BINDS` carries a `created_at`, and a weekly sweep expires
  suggestions nobody ratified in 30 days.
- **A closed date range read as half-open.** `--since D --until D` returned nothing; both bounds are
  now inclusive of the day named.

What remains is only **deliberate deferrals** and **post-v1 aspirations**.

**Deliberate v1 decisions** — not gaps; the spec chose these and revisits them with real usage (§10):

- **`BINDS` sub-typing stops at two modes** — `catalyzes` / `inhibits` / untyped is a closed vocabulary. A relation needing more than that is a note, not an edge label (§10).
- **No tags/keyword index** — a keyword is just a hub note many notes link to (Luhmann's own method).
- **Autocatalysis & the criticality band are descriptive-only** — the `autocatalytic` flag and the per-community/corpus criticality figures gate no behaviour, and criticality is *observed* rather than regulated (§11.3): the report names the band, you decide what to do about it.
- **Communities are detected, never ratified** *(v0.8.0)* — there is no `Cluster` node to accept or name. If a theme deserves a name, write a hub note and bind it to the members.
- **Working-set→product catalysis is not a fitness term** *(v0.8.0)* — `Op(PRODUCE)` still records it, so promoting it back is a report change, not a schema change.
- **`--as-of` uses each edge's current `BINDS.status`** — edge existence and note membership are dated exactly, but ratification history is not reconstructed (recoverable from `Op(RATIFY_LINK)` if it ever matters).
- **Embedding model** defaults to `mxbai-embed-large` (1024-dim); the final MTEB pick is left open (swappable via config).

**Post-v1 aspirations** — the §11 "living computer" layer, beyond the build order:

- **A provocation UI** — the observer-centred *spatial map* / provocation view (§11.3 mod 6). **Delivered** *(v0.8.1)*: [`ui.sh`](#graph-ui) serves the spatial map — filterable, selectable, with a note detail panel — and the provocation half is now actionable rather than only marked. "Near in meaning, not linked" carries a button that goes through `LinkManager.suggest`, so it proposes rather than ratifies and §8.2's machine-proposes/human-disposes split survives it; the ratification queue is where the disposing happens. Writing a note, correcting one, and draining `staging/` came along with it, each through the same `notelib` call the CLI makes. What remains aspiration here is the rest of §11.3: the view surfaces moves 1 and 2 (via the digest's suggestions), not all six.
- **Multi-scale competency daemons** (§11.3 mod 4) — per-community / per-hub autonomous agents with individual goals (a cluster guarding its coherence, a hub recruiting semantically-near notes). Today maintenance is corpus-global jobs, not per-agent daemons.
- **Full branching-parameter criticality** (§10) — the report uses a mean-degree band + avalanche proxy; a full branching-ratio analysis comes later.
- **A materialized analytics cache** (§13.1) — reports scan the graph, which is free at ~100 notes and will not stay free. The answer then is a rebuildable `KnnCache`-shaped document type, *not* a property back on `Note`: the distinction that matters is whether the corpus claims the value as part of itself.
