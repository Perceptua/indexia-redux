# Spec: Move 6 — structural debt

**Status:** draft v0.3 — one move, one metric
**Was:** "Structural Prompt Assistant" (v0.1–v0.2). The file keeps its old name; the thing does not.
**Scope:** find notes the corpus is built on but the writer has stopped attending to, and render one writing prompt each.
**Placement:** `notelib.move6_candidates()`, rendered by `scripts/provocation_digest.py` — the sixth generativity move (spec §8.1).

---

## 1. Naming

This is **move 6**. indexia's difference-engine has five other generativity moves (spec §8.1); its output is a **provocation**; provocations that ask for writing rather than for a link are already called **prompts** in the digest ("moves 3/4 are rendered as write-a-note prompts"). So there is no new vocabulary here, and v0.2's parallel one — "signatures", a "prompt assistant", an `analytics.sh prompts` surface — is dropped.

That reconciliation settles the placement question v0.2 left open. Moves live in `notelib` and are consumed by the digest. Moves 2–5 read the graph and render; only move 1 stages suggestions into the ratification queue. **Move 6 names a single note, so it renders and stages nothing** — not as a special rule, but as the ordinary behaviour of most of the catalog.

It also inherits the read-only guarantee without needing a separate layer to hold it. Spec §13.4 draws the boundary exactly here: an operation may read anything, and `detect_communities` already sits in `notelib` on the operational path because it *writes nothing*. Move 6 qualifies the same way.

---

## 2. The goal, and the one metric

**Find notes that are structurally important while being less visited and less linked.**

Those two halves have to be measured on two *different* relations, or they cancel. This is the whole design, so it is worth being exact about why.

indexia has two note→note relations, and they cost completely different things:

- **`BEGETS` is free and automatic.** It is written at note creation — you named a parent, and the edge exists. No decision, no later act. Lineage is a **forest**: in-degree ≤ 1, out-degree unbounded.
- **`BINDS` is expensive and human.** A ratified bind exists because a person looked at two notes and judged them related. `Note.visited` is the same kind of fact from the other direction: a person walked through the note. Spec §13.2 calls `visited` the one thing no amount of graph reading recovers.

So: **lineage records what the corpus owes a note. Binds and walks record what the writer has given back.** The gap between them is the debt.

```
debt(n) = descendants(n) / (1 + attention(n))

  descendants(n) = transitive BEGETS descendants of n
  attention(n)   = undirected ratified-BINDS degree of n  +  n.visited
```

One ratio. **It has no tunable weights** — the only constants in this spec are gates and list sizes, and there is nothing to calibrate against a corpus that does not exist yet (§11.1). That is the main thing choosing one metric buys.

**Why this shape.**

- **Structurally important = many descendants.** A note with twelve notes below it seeded a line of thought; the corpus's structure literally hangs off it. This is available for free, in one pass, on a graph that exists from the first note — unlike betweenness, which needs Brandes, is meaningless below ~150 notes, and is a *function of links*, so it can never be high while a note is "less linked". Rejected for that last reason above all.
- **Less linked and less visited = low attention.** Both terms are acts a human performed on the note after writing it, so summing them is coherent rather than an apples-and-oranges fudge. It also mirrors the `fitness` formula, which sums catalyzes, inhibits, descendants and visited into one number.
- **`BEGETS` is deliberately absent from the denominator.** Being someone's parent is not attention; it cost no decision. Keeping it out is what makes numerator and denominator independent, and therefore what makes the ratio mean something.
- **Suggested (unratified) binds do not count as attention.** They are machine proposals with a 30-day life. Letting them into the denominator would let the digest's own output cancel the debt it exists to reveal.
- **`+1`** so a wholly unattended note does not divide by zero, and so its debt is simply its descendant count.

**Properties worth knowing before building it.**

- **Leaves score zero and never surface.** A note with no descendants has `debt = 0` however neglected it is. That is correct and deliberate: an unlinked leaf is just a recent note, and **move 5 already resurfaces orphans**. Move 6 does not duplicate it.
- **Well-attended roots self-cancel.** The root of a large line of thought usually *is* well linked and well walked, so its denominator grows with its numerator. What survives to the top is the note that grew a subtree and was then left alone — which is the target exactly.
- **It degrades gracefully.** `Note.visited` is currently 0 for every note (§11.2), so today the metric is `descendants / (1 + binds_degree)`. That is still the right question, and no code changes when walks start being recorded.
- **No percentile normalization.** v0.2 needed percentiles to make seven signatures comparable. With one metric you sort the raw number.

**What was dropped, and why.** v0.2 catalogued seven detectors. `unbegotten_hub` is the *opposite* shape — heavily linked — so it contradicts the goal. `bridge` needs betweenness (see above). `sink_community` and `structural_hole` need community detection and, in the second case, tags that do not exist. `rising_rim` needs a second as-of `Corpus` and carries a silent-failure trap around `BINDS.status` having no history. `fan_out` and `unvisited_hub` are both *strictly contained* in `debt` — a fan-out with nothing binding back and a hub nobody has walked through are the two ways this ratio gets large. One metric absorbed the two that were right and discarded five that needed machinery.

---

## 3. Philosophy, in short

**3.1 Topology, not semantics.** Move 6 never asks what a note says. It does not need to, and the four embedding-and-timestamp moves already cover meaning. Structure is the signal nothing else in indexia reads.

**3.2 Name the site, not the conclusion.** The prompt says where to write, never what to argue. "Twelve notes descend from *Legibility*; nothing links back" is a prompt. "Argue that legibility is the core failure mode of institutional design" is a theft. If the line contains a claim the writer did not make, it is malformed. This is spec §8.2 — the machine proposes, the human disposes — applied to a rendered line.

**3.3 It inverts `fitness`, on purpose.** `fitness` rewards inbound catalyzes edges and descendants, so the note most in need of writing scores as one of the healthiest things in the graph. Fitness measures standing; this measures debt. Reading them side by side is the point, and it costs nothing because they share their inputs.

**3.4 Nothing is stored, so nothing perishes.** §7.

---

## 4. Ranking and gates

Sort by `debt` descending. Then, in order:

**4.1 Descendant floor.** Skip notes below `MIN_DESCENDANTS = 3`. A note with one child and no binds has `debt = 1`, and at that magnitude the ratio is noise rather than debt.

**4.2 Lineage dedup — the one constraint this metric actually needs.** Neglect is inherited: if a root with twelve descendants has never been bound, its child with eleven probably has not either, and the top of the list becomes one chain rendered eight ways. **Where two candidates lie on the same ancestor path, keep only the higher-scoring one.** The parent map from `_load_lineage` makes this a walk up to the root per candidate, in memory.

This replaces v0.2's per-signature quota (meaningless with one signature) and its 2-hop neighbourhood penalty (aimed at the wrong adjacency — the collision here is lineage, not links).

**4.3 Suppression.** Only `status = 'proposed'` notes, which are not yet corpus. **No degree-percentile suppression** — v0.2 inherited a rule excluding the top 0.5% by degree as "structural furniture", which is actively wrong here: in indexia a note that many notes link to *is* how a keyword exists (spec §10, "a keyword is just a note that many notes link to"). Those notes are also, by construction, the ones this metric already scores low.

**4.4 Cap** at `MOVE6_K = 5`, matching the digest's other per-move caps.

---

## 5. Prompt construction

Rendered from templates, in the digest's existing move sections. No generative step.

**Rules.**

1. One sentence, twenty words or fewer.
2. Titles appear **verbatim**. Untitled notes use `notelib.snippet(body, 60)`.
3. State the structural fact and stop. The count is the point, so print the count.
4. Never assert what a note means, implies, or should argue.
5. **No score in the line.** The ratio goes in the `--json` payload and the Op-free trace, not the rendered text — a displayed number is a number to move (§11.3).
6. Print the **Folgezettel address** beside the id. `notelib.folgezettel()` renders it on demand and the digest already prints it per seed.

**Templates.**

| Case | Template |
|---|---|
| `attention == 0` | `{n} notes descend from "{title}". Nothing links back to it.` |
| `attention > 0` | `{n} notes descend from "{title}". Only {a} link back.` |

Two templates, because "nothing" and "only two" are different facts and collapsing them into "few" loses the sharper one.

**Digest section heading**, matching the existing six:

```
## Move 6 — structural debt (the corpus grew out of these; you have not been back)
```

---

## 6. Placement and code

Move 6 is a pure function in `notelib`, alongside moves 1–5:

```python
def move6_candidates(db, k=MOVE6_K, min_descendants=MIN_DESCENDANTS): ...
```

`provocation_digest.build()` calls it beside `move3_candidates`/`move5_candidates`, and `render()` gains one section. No new script, no new CLI, no new schema type, no new scheduled job — the digest already runs nightly.

**Three queries, all of which the codebase already makes:**

| Need | Query | Already used by |
|---|---|---|
| descendants | `SELECT outV().id AS p, inV().id AS c FROM BEGETS` | `_load_lineage` (folgezettel) |
| binds degree | `SELECT outV().id AS a, inV().id AS b FROM BINDS WHERE status='ratified'` | first half of `_undirected_adjacency` |
| visited, title, body, status | `SELECT id, title, body, status, visited FROM Note` | `Corpus._read_notes` |

Descendant counts are then pure Python over the parent map — invert it to a children map and reach out from each note, which is exactly what `Corpus._reach` does. No new algorithm, no dependency, no vector call, and therefore no k-NN cache dependency: **move 6 is the only move that touches neither the embeddings nor the clock**, so it costs the digest nothing measurable.

**Tests.** The `debt` ratio, the descendant floor and the lineage dedup are conventions rather than data, so they pin as pure arithmetic with no database — the pattern `tests/test_analytics_metrics.py` already uses for `fitness.score` and `autocatalysis.autocatalytic_core`. That file imports `notelib` as well as `analytics`, so it can host these, though its name would then be slightly off: move 6 is a `notelib` move, not an analytics report. Splitting the arithmetic out cleanly is the alternative. The read-only property needs no test of its own — move 6 writes nothing, and the digest's `Op` count already says so if that changes.

**Optional, later:** a thin `analytics.sh debt` wrapper over the same function, for asking on demand rather than reading the nightly file. Analytics imports `notelib` (never the reverse), so this is a legal five-line report. Deferred — it is a second surface for one number, and the digest is where prompts already live.

---

## 7. What is stored

**Nothing.** No prompt table, no snapshots, no state file. The list is recomputed from the graph each run, which makes it self-maintaining in the way spec §13.1 argues for: a note that gets bound or walked drops out of the list on the next run, with no resolution logic, no decay, no cooldown, and nothing to go stale.

v0.1 proposed a SQLite store with `snapshots`, `node_metrics`, `prompts` and `prompt_events`, plus half-lives, reinforcement counters and retirement floors. All of it is deleted. Two indexia facts do the work instead: every report can be recomputed, and `--as-of` reaches the past without keeping snapshots.

**Dismissal and pinning are not built.** They are human judgements, so they are not derivable, so they would have to be `Op`s — and every stored human judgement is a thing v0.8.0 and v0.8.3 spent two versions removing from this codebase (stored fitness, stored communities, the walk working set). A five-line list is cheap to skim past. If repeats become the reason the section is ignored, the model is the walk: new `Op` rules folded at read time, never a table, and remembering that **a retired rule is still read**.

---

## 8. Constants

Module constants next to the other moves' — no config file exists anywhere in indexia, and `fitness.py` states the principle: changing a constant changes the next report, with no migration and no recompute, because nothing was stored to become wrong.

```python
MIN_DESCENDANTS = 3    # below this the ratio is noise, not debt (4.1)
MOVE6_K         = 5    # candidates rendered, matching the digest's other moves
```

That is the entire tuning surface. There are no weights.

---

## 9. Evaluation

The question is whether the writer writes from these notes.

**Resolution is visible in the graph and needs no input.** A move-7 note resolves when it gains a ratified bind, a walk, or a new child — all of which lower its debt and drop it off the list. So the measurement is: run the digest as of last month, run it now, diff the ids. `--as-of` makes a report able to reconstruct its own past output, which makes it able to score itself.

One caution: optimizing for resolution rate selects for *easy* prompts and will starve the hard ones. Diagnostic, not objective.

The sharper failure to watch for is the opposite of resolution — a note that stays at the top of the list for two months. That means either the prompt is unwritable or the writer disagrees with the premise, and both are worth knowing.

---

## 10. Failure modes

**10.1 The corpus is empty.** The *De Anima* corpus was wiped 2026-08-02. As of 2026-08-03 `analytics.sh criticality` reports **0 notes** (band `sparse`), and `visited` and `walks` report 0. Before the wipe it was ~101 notes. **Nothing here can be validated against live data yet.** Build against synthetic lineages — `move6_candidates` is a pure function of three edge lists, which is what makes that possible — and check the gates once the corpus passes ~50 notes with real depth. The two constants in §8 are the only guesses, which is the point of having only two: `STAGE_MIN_SCORE` was set to 0.70 on intuition, admitted 4 of 55 candidates, and had to be recalibrated against measured scores.

**10.2 `visited` has never carried a signal.** Before the reset, all 44 walks ever recorded were `test_walk_ops.py` artifacts with an identical `Op` fingerprint, and `visited` was 0 for every note across the corpus's whole history — part of what motivated removing the walk working set in v0.8.3. The metric degrades to the binds-only form and needs no code change when that changes (§2), but until walks are used in anger, "less visited" is carrying no weight in practice.

**10.3 Shallow lineage.** The metric is worthless if notes are not written with parents. If `add-note` is mostly called without `--continues`/`--branches`, `BEGETS` is a scatter of isolated roots, every descendant count is 0, and move 6 renders nothing forever. **Check this before building**: the fraction of notes with a parent is one query, and if it is low the honest conclusion is that this move cannot work on this corpus, not that the threshold needs lowering.

**10.4 Root dominance.** A deep corpus concentrates descendants near its roots. §4.2's lineage dedup is what keeps this from producing one chain rendered five ways, and it is the constraint most likely to need revision on real data — in particular whether "same ancestor path" should be the whole path or a bounded number of hops.

**10.5 Graph gaming.** Once the writer knows debt surfaces, they may bind a note to silence the prompt rather than to record a relation, corrupting the graph as an instrument. This is sharper here than in the general case: ratification is the writer's own act, so they can move every number this metric reads. The defence is rule 5 of §5 — no score in the line, no totals, no streak — plus the fact that a bind takes two notes and a reason, and the `rationale` field is read by move 5 later.

**10.6 It reads as a chore.** Every other move offers something (a link to make, a tension to reconcile, a note you forgot). This one says you owe work. Placing it last in the digest, capping it at five, and keeping the sentence flat rather than admonishing are all deliberate.

---

## 11. Milestones

**v0** — `move6_candidates` + the digest section + pure-arithmetic tests. No `analytics.sh` surface, no dismissal, no as-of scoring. This is a day's work, and it should be, because the open question is whether the prompts are worth reading and only running it answers that.

**v0.1** — Calibrate `MIN_DESCENDANTS` and the §4.2 dedup rule against a corpus past ~50 notes. Check 10.3 first.

**v0.2** — Self-diff scoring (§9). Optional `analytics.sh debt`.

Anything beyond that — dismissal, decay, more detectors — should have to argue against the single metric, not be assumed.

---

## 12. Open questions

1. **What fraction of notes have a `BEGETS` parent?** Decides whether move 6 is viable at all (10.3). One query, and it should be run before anything is built.
2. **Transitive descendants or direct children?** Transitive is specified, is already computed in one pass, and is the honest reading of "the corpus grew out of this". But it is what creates root dominance (10.4). Direct children would be flatter and blunter.
3. **Should `visited` be weighted equal to a bind?** They are summed 1:1 as the simplest defensible choice, and while `visited` is 0 everywhere the question is moot. Once walks are recorded it will not be: a walk-through is much cheaper than a ratified bind, so 1:1 may over-credit attention and suppress real debt.
4. **Is a `continues` child worth the same as a `branches` child?** The metric ignores `BEGETS.mode`. A `continues` chain is one thought carried forward; a `branches` child is a new direction. Twelve continues-descendants may be one long note in disguise.
5. **Does the digest section need its own quiet-run line?** v0.8.5's lesson was that a run rendering nothing must say *why* or it reads as broken. "No notes above the descendant floor" and "lineage is too shallow to measure" are different messages and only the second is a problem.
