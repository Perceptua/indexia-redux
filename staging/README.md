# staging/ — the drop zone

Put a file here and `scripts/ingest-staging.sh` turns it into a note (workflow 2; workflow 1 is
`add-note.sh` for a single note from flags). This directory is tracked; the files you drop in it
are gitignored.

```bash
bash scripts/ingest-staging.sh --dry-run   # parse + validate everything, write nothing
bash scripts/ingest-staging.sh             # ingest, then move each file away
```

## The filename is the note's id

Name the file with a spec §4 id — a compact UTC timestamp with milliseconds — optionally suffixed
`.md`, `.txt` or `.note`:

```
20260722T101500000Z.md
```

Mint fresh ones with `bash scripts/new-id.sh [N]` (it checks `staging/` for collisions). The id is
the note's identity and its `created_at`, so a back-dated id gives a back-dated note — that is how
a corpus rebuilds with its real history intact.

## Or drop the file on the page

The graph UI's `inbox` panel takes drag-and-drop (`bash scripts/ui.sh start`, then
<http://localhost:8420/>). It writes into this directory through `scripts/inbox.py`, and the rules
it applies are worth knowing because they are not quite the rules above:

- **A file already named for a valid id keeps it.** Anything else — `thought.md` off your desktop
  — is given a fresh one, and the name it arrived as is written in as `source_ref:` so the
  provenance is not lost. A file that *is* already named for its id gets no `source_ref`: its own
  filename is not provenance.
- **That `source_ref:` is always followed by a `---` fence, and is only added when the file has no
  header of its own.** Both halves matter. Without the fence, the parser below folds the entire
  body into the `source_ref` value and the note ends up with no `body` at all. And the fence
  happens to fix an older hazard: a plain-prose file containing a markdown `---` divider loses
  *everything above the divider* and ingests anyway, because the parser overwrites what it has
  collected when it reaches a fence. A dropped file's fence is the first one, so nothing is lost.
- **YAML front matter is unwrapped** (`---` / keys / `---` becomes keys / `---`) and a leading
  **BOM is stripped** — either would otherwise turn the header into part of the body, silently.
- **An id already waiting here is refused, never overwritten**, including under a different
  extension: `<id>.md` and `<id>.txt` are one note. Only this directory is consulted, not
  `processed/` or `failed/` — those hold ids that have had their turn, and refusing on them would
  break re-dropping an exported note.
- Nothing is committed by the drop. A parked file is not a note and logs no `Op`; ingest is still
  the act that makes one.

## Inside: `key: value` lines, then the body

Recognized keys: **`title`**, **`body`**, **`created_at`**, **`author`**, **`source_ref`**,
**`status`**, **`id`**, plus **`parent`** and **`mode`**.

A line containing only `---` ends the header: everything after it is the body. That is the form to
use for anything long, or anything containing a colon.

```
title: Folgezettel is display-only
author: human
source_ref: docs/spec.md §4
---
The Luhmann address is a derived projection of the BEGETS ancestry,
computed on demand — never a stored field.
```

Without a fence, any line that is not a recognized `key:` continues the previous value, so a short
body can span a couple of lines. `source-ref:` is accepted for `source_ref:`. If you give an `id:`
key it must match the filename, or the file is rejected rather than silently renamed.

**`body` is required** (`Note.body` is `MANDATORY`/`NOTNULL`). `author` defaults to `human`,
`status` to `active`.

## `parent` + `mode` — the one edge ingestion may create

Together they add a `BEGETS` edge from the parent to this note (spec §3.2). They go together: give
both or neither.

```
title: Atomic notes recombine freely
parent: 20260722T101500000Z
mode: continues
---
Because each note stands alone, they can be combined without rewriting.
```

`mode` is `continues` (same-level succession, `1a → 1b`) or `branches` (descent, `1a → 1a1`).
Order does not matter across files — ingestion processes them in filename order, and since ids are
timestamps a parent always precedes its child.

**Corrections are not an ingestion act.** There is no `supersedes` key. Commit the corrected note
here, then bind it back and ratify (spec §6, §7):

```bash
bash scripts/link.sh suggest <new-id> <old-id> --mode inhibits --rationale "why it was wrong"
bash scripts/link.sh ratify  <new-id> <old-id>
```

## What happens to your files

| Outcome | Where the file goes |
|---------|---------------------|
| ingested | `staging/processed/` |
| any error (bad id, missing body, duplicate, unreachable parent) | `staging/failed/` |

Nothing is deleted, and one bad file never stops the run. `README.md` and dotfiles are skipped, so
this file is safe here.

Notes are queued for **async embedding** — `scripts/embed-worker.sh` picks them up (or run
`scripts/embed-backfill.sh`). A note with no embedding yet is fully usable; it just cannot be found
by semantic search until it is embedded.

## `scans/` and `transcripts/` — inboxes this directory ignores

Two other drop zones live under `staging/` but hold nothing `ingest-staging` will ever read as a
note: [`staging/scans/`](scans/README.md) parks images of handwritten notecards for the
`transcribe-notes` skill, [`staging/transcripts/`](transcripts/README.md) parks machine transcripts
of audio notes for the `review-transcripts` skill. Both are safe here for the same reason
`manifests/` below is — `staged_files` only reads plain files, so a subdirectory is invisible to it
regardless of what's in it or what it's named. Either skill's output is an ordinary ratified file
written straight into *this* directory; from there it is workflow 2, same as anything else dropped
here.

## `manifests/` — replaying the associative layer

Ingestion creates `BEGETS` and nothing else, so a corpus rebuilt from `processed/` has lineage but
no binds. `manifests/binds.tsv` restores them:

```bash
bash scripts/seed-binds.sh staging/manifests/binds.tsv
```

Tab-separated `a ⇥ b ⇥ mode ⇥ rationale`, `#` comments ignored; `mode` may be empty (untyped),
`catalyzes` or `inhibits`. Every row is suggested and then ratified through the real `LinkManager`,
so `created_at` and the `Op` log are genuine rather than back-dated. Rows whose bind already exists
are skipped, so re-running is safe.

Manifests live in this **subdirectory** on purpose: `ingest-staging` scans `staging/` itself and
would try to read a stray manifest as a note.

> Keep the manifest current. It holds the **ratified** binds — every human judgement in the corpus
> — and a rebuild restores exactly what is in it. Regenerate it from the live graph before any
> from-scratch rebuild rather than trusting it to be up to date; suggested edges are deliberately
> omitted, since they are machine proposals that expire (spec §7).
