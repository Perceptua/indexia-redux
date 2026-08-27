---
name: add-note
description: Capture a new note into the Indexia ArcadeDB knowledge graph. Use when the user wants to add / write / jot / commit a note, note down an idea, or "make a note of this" from a source excerpt. For batch-importing files named as note ids, use the ingest-staging skill instead.
---

# Add a note to the Indexia graph

Ingest a single `Note` vertex via `scripts/add-note.sh` — the `ADD_NOTE` rule (spec §12.3).
A note is one atomic idea in full sentences (spec §3.1). Curation is always human: draft from
what the user gives you, but the user is the committer.

## Prerequisites

- The database must be up. Check with `bash scripts/status.sh`; start it with `bash scripts/up.sh`.
- **Execution:** Docker + python3 live in WSL Ubuntu, so run everything through the WSL wrapper
  from the repo root, e.g.:
  ```bash
  wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/add-note.sh --title "..." --body "..."'
  ```

## Fields (all optional flags map to ddl/schema.sql §20-25)

| flag | field | default when omitted |
|------|-------|----------------------|
| `--body` | body (**required**) | prompted / read from stdin |
| `--body-file PATH` | body, from a file | — |
| `--title` | title | none |
| `--author` | `human` \| `llm_assisted` (§5) | `human` |
| `--source-ref` | provenance pointer | none |
| `--id` | id | generated: compact UTC ms, e.g. `20260722T101500000Z` (§4) |
| `--created-at` | created_at | the id's instant |
| `--parent ID` + `--mode` | BEGETS lineage: new note `continues`/`branches` from `ID` (§4) | no link (root note) |
| `--json` | print the commit result as JSON | — |

`id` and `created_at` are immutable and normally auto-generated — do **not** pass them unless you
are back-dating an import. `status` is always `active` on a fresh note. The note is inserted
atomically with an `Op(ADD_NOTE)` log entry and **queued for embedding** — a background worker embeds
it shortly after, so the commit is instant (async, the default). `--embed` embeds inline now (blocks);
`--no-embed` forbids it.

## How to run

Typical (you draft the body, the user has approved the wording):
```bash
wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/add-note.sh \
  --title "Atomicity" --author human --source-ref "Luhmann 1992" \
  --body "A note should carry exactly one idea, stated in full sentences so it stands alone."'
```

Long/multi-line body — write it to a file first and use `--body-file`:
```bash
wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/add-note.sh --title "..." --body-file /tmp/draft.md'
```

The script prints `created Note <id> · Op <id> · queued for embedding` (or `embedded (…)` with
`--embed`). Report the note id back to the user. The note is safely committed either way; the embed
worker (`scripts/embed-worker.sh`) fills in the embedding in the background.

## Notes & gotchas

- The corpus is **append-only** (spec §6). To revise meaning, create a new note and bind it back
  as a correction (below) — do not overwrite. Passing an `--id` that already exists is rejected.
- One idea per note. If the user's text holds several ideas, propose splitting it into multiple
  notes and add them individually.
- **Embedding is fail-open**: a down embedder never blocks a commit. Embeddings come from a local
  Ollama daemon (`scripts/embed-server.sh`); if notes report "queued for backfill", run
  `scripts/embed-backfill.sh`.
- **Lineage** needs the target id: `--parent ID --mode continues|branches` links the new note into
  the `BEGETS` tree, validated atomically with the commit. Find the target id with the
  **search-notes** skill / `scripts/search.sh` (e.g. `--like <id>` or `--title …`). Without
  `--parent`, the note is a root. `BEGETS` is the **only** edge this skill can create (§3.2).
- **Corrections are not an ingestion act** *(v0.7.0, §6)*. There is no `--supersedes` flag and no
  `superseded` status. Commit the corrected note here, then bind it back and ratify:

  ```bash
  bash scripts/link.sh suggest <new-id> <old-id> --mode inhibits --rationale "why it was wrong"
  bash scripts/link.sh ratify  <new-id> <old-id>
  ```

  The old note is untouched; being corrected is derived from that ratified bind, so it can be
  withdrawn by rejecting the bind. Nothing else is written — since v0.8.0 the sign lives on the
  edge and is read where it matters, rather than accumulating into a number on the note (§13).
  Always confirm the correction with the user before ratifying — ratification is the human's act,
  not yours.
