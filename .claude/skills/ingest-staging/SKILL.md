---
name: ingest-staging
description: Batch-ingest typed note files from the Indexia staging/ directory into the ArcadeDB graph. Use when the user has dropped (or wants to drop) id-named files in staging/, or asks to import / process / ingest staged notes. For adding one note from flags or dictation, use the add-note skill instead.
---

# Ingest staged note files

Turn files in `staging/` into `Note` vertices via `scripts/ingest-staging.sh`. Each file's name
is the note `id`; its contents are `name: value` property lines. Successful files move to
`staging/processed/`, failures to `staging/failed/`.

## Prerequisites

- Database up (`bash scripts/status.sh` / `bash scripts/up.sh`).
- **Execution** through the WSL wrapper from the repo root:
  ```bash
  wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/ingest-staging.sh'
  ```

## File format

**Filename = id** (spec §4): `YYYYMMDDTHHMMSSmmmZ`, e.g. `20260722T101500000Z.md`
(the `.md`/`.txt`/`.note` suffix is optional and stripped). The id is validated and checked for
uniqueness before insert.

**Contents** — one property per line as `name: value`. Recognized keys: `body` (**required**),
`title`, `author`, `source_ref`, `created_at`, `status`, `id`, and the relationship pair `parent` +
`mode` (`continues`|`branches`, adds a BEGETS edge, §4). That is the *only* relationship pair —
ingestion creates no other edge (§3.2). There is no `supersedes` key: a correction is a plain note
here, followed by `link.sh suggest <new> <old> --mode inhibits` and a ratification (§6).
Unrecognized lines fold into the previous value; a line containing only `---` starts the body (use
this for long or colon-heavy bodies):

```
title: Folgezettel is display-only
author: human
source_ref: docs/spec.md §4
---
The Luhmann address is a derived projection of the BEGETS ancestry,
computed on demand — never a stored field.
```

To create a staged file for the user, generate a valid id (compact UTC ms, `date -u +%Y%m%dT%H%M%S%3NZ`)
and write it to `staging/<id>.md`. See `staging/README.md` for the full reference.

## How to run

1. **Preview first** — validate and parse without inserting or moving anything:
   ```bash
   wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/ingest-staging.sh --dry-run'
   ```
2. **Ingest for real:**
   ```bash
   wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/ingest-staging.sh'
   ```

The script prints one line per file (`OK … -> Note <id> [embedded|pending-embed]` or
`FAIL <name>: <reason>`) and a summary, and exits non-zero if any file failed. Report the results;
for failures, inspect `staging/failed/` (common causes: malformed id, id already in the corpus,
missing `body`).

## Notes & gotchas

- Append-only (spec §6): a duplicate id is rejected rather than overwritten.
- `created_at` defaults to the id's instant, so an id-named file carries its own timestamp — handy
  for back-dated imports.
- Each note is inserted atomically with an `Op(ADD_NOTE)` log entry and marked `pending-embed`;
  the background embed worker (`scripts/embed-worker.sh`) embeds it shortly after (async, the
  default — see the ingest summary line). `--embed` embeds inline at commit instead; `embed-backfill.sh`
  is the manual one-shot if the worker isn't running.
