---
name: search-notes
description: Find notes in the Indexia ArcadeDB knowledge graph — by meaning (semantic / vector search) or by metadata (id, title, author, status, date). Use when the user wants to find / search / look up / recall a note, asks "which note said…" or "what did I write about X", or needs a note's id to link or correct it. For adding a note use the add-note skill; for batch import use ingest-staging.
---

# Search the Indexia graph

Find notes with `scripts/search.sh` — the read side of the difference-engine (spec §8). Two ways
to search, kept separate: **semantic** (nearest by meaning, over the vector layer) and **lexical /
metadata** (exact-ish filters over the metadata + temporal layers). This is read-only — it never
writes, ratifies, or commits.

## Prerequisites

- The database must be up. Check with `bash scripts/status.sh`; start it with `bash scripts/up.sh`.
- Semantic search (`-q` / `--like`) also needs the embedder up (`bash scripts/embed-server.sh status`);
  lexical search does not.
- **Execution:** Docker + python3 live in WSL Ubuntu, so run everything through the WSL wrapper from
  the repo root, e.g.:
  ```bash
  wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/search.sh -q "atomic notes"'
  ```

## Semantic — by meaning

| flag | what it does |
|------|--------------|
| `-q, --query "TEXT"` | embed the text and return the notes nearest in meaning |
| `--like ID` | return notes nearest an existing note (that note must already be embedded) |
| `--ef N` | ANN search breadth (default 100; raise for recall on a large corpus) |

Each hit shows a `score` (cosine similarity, `1.000` = identical). Notes still awaiting embedding
(`pending-embed`) can't match yet and simply won't appear.

```bash
wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/search.sh -q "structure emerges from links, not folders" -k 5'
```

## Lexical / metadata — exact-ish filters (AND-combined)

| flag | matches |
|------|---------|
| `--id-prefix P` | ids starting with `P` — a day like `20260722` selects that whole day (spec §4) |
| `--text S` | case-insensitive substring in **body or title** |
| `--title S` | case-insensitive substring in the **title** |
| `--author A` | `human` \| `llm_assisted` |
| `--status S` | `active` \| `proposed` |
| `--inhibited` | only notes corrected by a ratified `BINDS{inhibits}` — derived, not a status (§6). Corrected notes stay searchable. |
| `--since YYYY-MM-DD` / `--until YYYY-MM-DD` | UTC day range (since inclusive, until exclusive) |

With **no arguments**, `search.sh` lists the most recent notes (a plain browse).

```bash
wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/search.sh --title Folgezettel --author human --since 2026-07-01'
```

## Common flags

- `-k, --limit N` — max results (default 10 semantic / 25 lexical).
- `--json` — raw result rows (`id`, `title`, `status`, `snippet`, `score`/`created_at`, …) for scripting.

Semantic and lexical are **mutually exclusive** in v1 — pick one; combining them is an error.

## Presenting results

Report each hit as its **id** (clickable/usable), title, and snippet — plus the score for semantic
searches. The id is what the write tools take: once the user picks a target, offer the natural
follow-on with the **add-note** skill:

- link the lineage — `add-note.sh --parent <id> --mode continues|branches …`
- issue a correction — add the corrected note, then `link.sh suggest <new> <id> --mode inhibits
  --rationale "…"` and ratify (§6)

`--like <id>` pairs especially well with this: find notes near an existing one, then bind them.

## Notes & gotchas

- **Read-only.** This skill only finds notes. Writing, linking, and superseding go through add-note.
- **Semantic needs embeddings.** If a just-added note doesn't show up in `-q`/`--like` results, it is
  probably still `pending-embed`; check `bash scripts/embed-worker.sh status` (or run
  `bash scripts/embed-backfill.sh`), then retry.
- **Empty results** are reported plainly, not as an error. An empty corpus returns no matches.
