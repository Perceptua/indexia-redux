---
name: walk
description: Record and replay reading/thinking sessions ("walks") in the Indexia ArcadeDB graph — start a walk from a seed note, note what you visit, save the run, and replay a saved run against the changed corpus to surface fresh provocations. Use when the user wants to start/record a reading session or walk, note down what they visited, or "replay my walk". For finding notes use the search-notes skill; for adding notes use add-note.
---

# Walks — recorded, replayable reading sessions

A **walk** is a read/think session through the graph (spec §11.1, §13): the notes you `visit` and
the notes the run `produce`s. The trail is the whole of what the run held — there is no separate
working set to nominate (removed in v0.8.3), and `replay` re-seeds from the trail. Saving a
walk makes it **replayable** — re-running it against a corpus that has since changed yields new
provocations (the stored-program loop; the walk is the corpus's *being-at-work*, second entelechy
§11.6).

**Recording is an operation; reading a walk back is an analytic.** That split is the whole shape
of this skill:

- `scripts/walk.sh` — the verbs that change something. Each writes one `Op` and, for a visit,
  `+1` to that note's `visited` counter.
- `scripts/analytics.sh` — the verbs that only look. A walk has no vertex of its own; it *is* its
  sequence of Ops, reconstructed on demand.

## Prerequisites

- The database must be up (`bash scripts/status.sh`; start with `bash scripts/up.sh`).
- `replay` surfaces move-1 candidates, so it needs the embedder up
  (`bash scripts/embed-server.sh status`) and the trail's notes embedded; nothing else does.
- **Execution:** Docker + python3 live in WSL Ubuntu — run through the WSL wrapper from the repo
  root:
  ```bash
  wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/analytics.sh walks'
  ```

## Recording — `walk.sh`

| command | what it does | rule |
|---------|--------------|------|
| `start --seed ID [--intent "…"]` | open a walk and visit the seed; without `--intent` the walk is named after its seed | `START_WALK` |
| `visit WALK NOTE` | append a note to the trail | `VISIT` |
| `produce WALK NOTE` | record a note the walk authored | `PRODUCE` |
| `save WALK` | close the run; saved walks replay and fork | `SAVE_WALK` |
| `fork WALK` | open a new walk from this one's seed and intent | `FORK_WALK` |
| `delete WALK` | retire a walk and give back the visits it counted | `DELETE_WALK` |

## Reading back — `analytics.sh`

| command | what it shows |
|---------|---------------|
| `walks [--status active\|saved]` | every walk, newest first |
| `walk WALK` | the ordered trail, plus what it produced |
| `replay WALK [-k N] [--depth N] [--no-cache]` | fresh move-1 candidates from the saved walk's trail |
| `visited [--ascending] [--limit N]` | attention per note across all walks |

```bash
wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/walk.sh start --seed 20260722T101500000Z --intent "how does emergence recur?"'
wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/walk.sh visit <walk> <note>'
wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/walk.sh save <walk> && bash scripts/analytics.sh replay <walk>'
```

`--json` on any verb prints raw output.

## Presenting results

- After `start`, report the new **walk id** — every later verb takes it as the first argument.
- `replay` returns fresh **move-1 candidates** (semantically near the trail, graph-far), each
  marked `NEW` if the note appeared after the walk ended, plus any visited note now **inhibited**.
  Present the candidates as clickable ids and offer to `link.sh suggest`/`ratify` them — replay
  proposes, the human disposes.

## Notes & gotchas

- **Only saved walks replay.** `save` first, or replay errors.
- **Every walk is named.** `--intent` states the goal for the run (top-down control, §11.3);
  omit it and the walk is named after its seed instead. Nothing in the grammar amends a
  `START_WALK` payload, so a walk cannot be renamed later — state the intent if you have one.
- **`visited` counts walks, not visits.** Coming back to a note three times in one sitting is one
  encounter, so the counter moves once. Revisits are still logged; they just do not double-count.
- **Only walks move it.** No machine job — not the digest, not resurfacing, not the k-NN rebuild —
  ever touches `visited`. That is what makes it a measure of the reader rather than of the system.
- **`delete` is a tombstone, not an erasure.** The Op log is append-only, so the walk stops being
  reconstructed and each note it visited is decremented by one (never below zero).
- **The machine never authors.** `replay` surfaces provocations; writing the resolving note stays
  human (add-note).
- Walks feed the `fitness` report via `visited` (`analytics.sh fitness`), which is computed when
  asked — there is no recompute job to wait for.
