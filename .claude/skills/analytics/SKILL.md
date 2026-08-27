---
name: analytics
description: Measure the Indexia ArcadeDB graph without changing it — fitness of notes, structural debt, corpus criticality (edge of chaos), detected communities/themes, autocatalytic sets, and attention statistics, optionally as of a past date. Use when the user wants to find communities or themes across their notes, check whether a group is autocatalytic, ask which notes matter most or are never revisited, see whether the corpus is over- or under-linked, or compare the graph to how it looked earlier. For writing prompts from neglected notes use the writing-prompts skill; for finding individual notes use search-notes; for links between two notes use the ratification flow (link.sh); for recording a reading session use the walk skill.
---

# Analytics — measuring the graph without touching it

Everything here **reads and never writes** (spec §13): no property, no edge, not even an `Op`
recording that a report ran. Run any of it as often as you like; the corpus cannot tell.

That is the design, not a convenience. Before v0.8.0 these numbers were stored — `Note.fitness`,
`Cluster.autocatalytic`, and friends — which meant a nightly job had to rewrite them whenever the
graph moved, and they were wrong in between. Computed on demand they cannot go stale, they cost
nothing to change, and they can be asked of the graph **as it stood in the past**.

Driven by `scripts/analytics.sh`.

## Prerequisites

- The database must be up (`bash scripts/status.sh`; start with `bash scripts/up.sh`).
- Everything reads the graph only — no embedder needed, except `replay`.
- **Execution:** run through the WSL wrapper from the repo root:
  ```bash
  wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/analytics.sh communities'
  ```

## Reports

| command | what it answers |
|---------|-----------------|
| `communities [--members] [--min-size N]` | what themes are in the corpus right now, with each one's hub |
| `autocatalysis [--members]` | which of those themes **cycle** under catalysis — the living units |
| `fitness [--limit N]` | which notes have the most standing, and from what |
| `debt [--limit N]` | which load-bearing notes you have stopped attending to (see the **writing-prompts** skill) |
| `criticality` | is the corpus over-linked, under-linked, or at the edge of chaos |
| `visited [--ascending] [--limit N]` | which notes the reader returns to, and which never get walked |
| `walks` / `walk ID` / `replay ID` | recorded reading sessions (see the **walk** skill) |

`--as-of TIMESTAMP` (a note-id-shaped instant, e.g. `20260701T000000000Z`) recomputes any of the
first five against the graph as it stood then. `--json` prints raw output.

```bash
wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/analytics.sh autocatalysis --members'
wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/analytics.sh fitness --limit 15'
wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/analytics.sh communities --as-of 20260701T000000000Z'
```

## Presenting results

- **Autocatalysis is a yes/no structural fact, not a score.** It asks whether the catalysis
  relation — `BEGETS` ∪ ratified `BINDS{catalyzes}` — **cycles** among a community's members.
  `BEGETS` is acyclic, so a cycle needs at least one ratified `BINDS{catalyzes}`. The smallest
  case is a note bound back to something upstream of it; the general case is two chains
  catalyzing each other at different points. The report prints the **catalytic core** (the
  members in the cycle). A community reading `not autocatalytic` is normal and not a defect: it
  means the group hangs together associatively but nobody has yet said that any part of it
  *feeds* another.
- **Communities are detected, never ratified.** There is no proposal to accept and nothing to
  name in the database. If a theme deserves a name, the answer is to write a hub note about it
  (add-note) and bind it to the members — a keyword is just a note (§10).
- **Nothing gates on any of these numbers.** They are descriptive. Report them as observations,
  not as instructions.
- `criticality` prints a band — `sparse` / `critical` / `dense` — with a one-line reading. The
  loop closes through the human: the system measures, it does not correct itself.

## Notes & gotchas

- **Ask twice, get two answers.** A community is what the graph looks like now, so ratifying one
  bind can redraw the boundaries. That is correct behaviour, not instability.
- **`--as-of` has one honest limit.** `BINDS.status` carries no history, so a past view uses each
  surviving edge's *current* status — a bind ratified today counts as ratified in a view of last
  month, and one rejected since is absent from that view entirely. Note membership and edge
  existence are dated exactly (`created_at` on `Note`, `BEGETS` and `BINDS`).
- **Fitness weights are constants, not settings.** They live at the top of
  `scripts/analytics/fitness.py`; changing one changes the next report, with no migration.
- Communities need at least 3 linked notes; a 2-note group is just a link.
