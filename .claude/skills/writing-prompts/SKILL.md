---
name: writing-prompts
description: Surface the Indexia notes in most structural debt — the ones the corpus grew out of but the writer has stopped attending to — and turn each into a writing prompt. Use when the user asks what to write next, what they have been neglecting, which notes need developing, where they owe writing, or asks for a writing prompt from their notes. Also covers "structural debt" and "move 7" by name. For measuring the graph generally (fitness, communities, criticality) use the analytics skill; for finding a specific note use search-notes; for capturing a note use add-note.
---

# Writing prompts — the notes you owe writing

Indexia's difference engine has seven generativity moves (spec §8.1). Six ask what is
semantically near, temporally adjacent, or structurally between. **Move 7 asks what is
load-bearing and neglected**, which nothing else in the system reads.

    debt(note) = descendants / (1 + attention)

      descendants = transitive BEGETS descendants — how much of the corpus grew out of it
      attention   = ratified BINDS degree + Note.visited — how often you have been back

The two halves are read off **different relations**, and that is the whole design. `BEGETS` is
free and automatic — it exists because you named a parent when you wrote the note. A ratified
`BINDS` or a walk is expensive and human. So the numerator is what the corpus owes the note and
the denominator is what you have given back. Measure both on the link graph and they cancel:
"important" would mean well-linked and "neglected" would mean poorly-linked, and no note could be
both.

It is the deliberate inverse of `fitness`, which credits descendants and so scores exactly these
notes as the *healthiest* things in the graph. Fitness measures standing; this measures debt.

**It reads and writes nothing** — no property, no edge, not even an `Op` (spec §13).

## Prerequisites

- The database must be up (`bash scripts/status.sh`; start with `bash scripts/up.sh`).
- No embedder needed — move 7 touches neither the embeddings nor the clock, so it is the cheapest
  thing in the digest.
- **Execution:** run through the WSL wrapper from the repo root:
  ```bash
  wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/analytics.sh debt'
  ```

## Commands

| command | what it does |
|---------|--------------|
| `analytics.sh debt` | the top 5 notes in structural debt, each with its prompt |
| `analytics.sh debt --limit 10` | show more |
| `analytics.sh debt --min-descendants 2` | lower the floor on a young corpus |
| `analytics.sh debt --as-of TS` | what you owed as of a past instant (note-id-shaped timestamp) |
| `analytics.sh debt --json` | raw rows: `id, label, descendants, binds, visited, attention, debt` |

```bash
wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/analytics.sh debt --limit 10'
wsl -d ubuntu -- bash -lc 'cd /home/aphorikles/indexia && bash scripts/analytics.sh debt --as-of 20260701T000000000Z'
```

The nightly provocation digest renders the same thing as its last section, so
`recent/provocations.md` already has it if the scheduler is running.

## Presenting results — the part that matters

**Name the site, not the conclusion.** This is spec §8.2 applied to a sentence: the machine
proposes where to write, the human writes. The rendered prompt is already correct — pass it
through. Do not improve it into a thesis.

- ✅ `12 notes descend from "Legibility". You have not been back.`
- ❌ "You should argue that legibility is the core failure mode of institutional design."

The second one invents a claim the writer never made, and once it is on the screen they cannot
tell which half of the idea was theirs. **If the user asks you to expand a prompt into an
argument, say what the prompt is for and offer to capture what *they* say instead** (the add-note
skill). The machine never authors — that boundary is what keeps Indexia a communication partner
rather than a ghostwriter.

Other things to get right:

- **Do not show the debt score as a target.** The report prints it in the trace line, which is
  fine for auditing, but never frame it as a number to reduce. The writer can move it directly by
  ratifying binds, so a displayed target corrupts the graph as an instrument.
- **An empty list is an answer, and it says which one.** "Nothing is owed" and "lineage is too
  shallow to measure" are different messages. Read the reason back rather than reporting silence.
- **Notes with no descendants never appear**, however neglected. That is deliberate — an unlinked
  leaf is just a recent note, and move 6 (re-encounter) already resurfaces orphans.
- **One note per lineage.** Neglect is inherited, so a parent and its descendant never both
  appear; only the higher scorer survives. If the user wants to see the whole chain, follow the
  Folgezettel address or use search-notes.

## What to do with a prompt

The natural next step is to write the note the prompt points at, then bind it back:

1. `add-note` with `--continues` or `--branches` from the note in debt — this is writing *from*
   it, which is what the prompt asked for.
2. Or, if the thought already exists elsewhere, `link.sh suggest` + `ratify` to bind them. Either
   act lowers the debt, so the note drops off the next run with nothing to mark resolved.

Nothing needs to be dismissed or cleared. The list is recomputed from the graph every time.

## Notes & gotchas

- **Move 7 depends on a writing habit.** It measures lineage, so it only works if notes are
  committed with a `--continues`/`--branches` parent. Without one, `BEGETS` is a scatter of
  isolated roots, every descendant count is zero, and the report says so explicitly rather than
  going quiet.
- **`Note.visited` is currently 0 everywhere** — walks have not been used in anger yet, so
  "less visited" is carrying no weight in practice and the metric is effectively
  `descendants / (1 + binds)`. It starts counting on its own once walks are recorded; no change
  needed.
- **`--as-of` has one honest limit.** `BINDS.status` carries no history, so a past view uses each
  surviving edge's *current* status. Note and edge existence are dated exactly.
- **The constants are constants, not settings.** `MIN_DESCENDANTS` and `K` live at the top of
  `scripts/analytics/debt.py`. There are no weights to tune — that is the point of the ratio.
- Design rationale: `docs/indexia-prompt-assistant-spec.md`.
