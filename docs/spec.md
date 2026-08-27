# Indexia — Specification

*A virtual Zettelkasten: a human-curated, append-only knowledge graph with a machine that provokes rather than synthesizes.*

**Status:** Draft v0.8.6 (build-validated) · **Last updated:** 2026-08-02

*Changelog: v0.8.6 **the draft leaves the graph** (§12.7, §12.3). `PROPOSE_VARIANT`,
`COMMIT_VARIANT` and `REJECT_VARIANT` are **removed**, and with them `Note.status = 'proposed'`.
They were built and specified, and in 2267 operations nothing ever called them — because the human
step they exist to serve happens *before* anything is written: the assistant proposes a draft in
conversation, or a file waits in `staging/`, and what reaches the graph is already the approved
note. A proposed *note* is unlike a suggested *link*, which must be in the graph because a link is
a claim about two notes that already exist. Storing the draft would reify a state the corpus never
needs to be in — the judgement §13 makes about fitness and communities, applied one layer earlier.
The read side goes with it: the `--status` filter, the UI's status filter, the `proposed` badge and
the dashed node border all rendered a state nothing could create. `Note.status` stays in the
schema with `active` its only value. ·
v0.8.5 **the queue gets a level, and the maintenance loop gets honest**
(§7, §8.2, §12.6), all four from measurement rather than from reasoning. A per-run cap is a
**rate** and expiry is a **delay**; neither is a level, and only a level bounds a queue — five
nightly runs staged 50 suggestions and expired 0, with the first possible removal four weeks out.
So staging is now also bounded by the **standing depth** of the queue (`SUGGESTION_MAX_QUEUE`):
the digest proposes nothing while that many decisions are outstanding, which makes the machine's
output rate a function of the human's ratification rate — §8.2's split stated as a control loop
instead of an intention. Surfacing stays unbounded. Three corrections to the loop itself: a
**weekly** job is gated on a full period elapsed, so it settles on the time of day it first ran
rather than joining the nightly batch; a run that **changes nothing appends nothing**, so for
`knn-cache --if-stale` and `link-expiry` the `Op` log is not the record that they ran and
`scheduler.log` is; and a **failing job backs off** (15 min, doubling, capped at 6 h) rather than
retrying across the whole twenty-two hours a nightly cadence stays due. ·
v0.8.4 **the pair becomes nameable** (§11.3 mod 6). Shift-click on the map picks a
second note and refuses a third — multi-select exists so a **pair** can be named, and a pair is
what a bind takes — and a `link` panel proposes the bind between the two, each slot findable by
searching note bodies. It adds no rule to §12.3 and no route to the server: the button is
`SUGGEST_LINK` and the verdict that appears in its place afterwards is the same
`RATIFY_LINK`/`RETYPE_LINK`/`REJECT_LINK` the queue offers, rendered against the edge that now
exists. Its significance is *which direction the gesture runs*: every other link affordance starts
from a list the machine made, and this one starts from a human who went and found both notes —
the §3.3 act that is expensive precisely because only a person can perform it. Order is preserved
end to end, since on an `inhibits` bind the direction is the claim (§6). ·
v0.8.3 **the map gains a way in, and walks lose their second set** (§3.3, §11.1). The view
grows a lexical `search` panel — the way to reach a note you know something about but cannot see —
and a `walk` **mode**, so a reading session can be recorded from the map rather than only from the
CLI. That last one carries the whole weight of "browsing is not walking": a visit needs a walk id,
and a walk id exists only because somebody started one, which is what keeps `Note.visited` a
record of directed attention (§13.2) while every other click stays free. In the walk grammar
itself the **working set is removed** — `SET_WORKING` is retired and `replay` re-seeds from the
whole trail — because it was a *declared* human judgement about which notes mattered, the shape
v0.8.0 spent a version removing everywhere else, and across the corpus's entire history it had
never been used in anger. Retiring it made explicit what append-only always implied: **a retired
rule is still read** (§12.3). And **every walk now carries an intent**, derived from its seed when
none is given, because no rule can amend a `START_WALK` payload — a walk that begins anonymous is
anonymous for good. ·
v0.8.2 **two inboxes and a status panel** (§11.3 mod 6, §12.6). Files can be dropped into
`staging/` and `staging/scans/` from the view, and the maintenance clock, the daemons and the measurements
are readable from it. Neither adds a rule to §12.3, for two different reasons worth keeping:
**parking a file is not an operation** — no note exists, no vector is computed, nothing enters the
graph, so there is nothing for an `Op` to record — and the status panel is an **analytic** in
§13's sense, which reports the loop and may not drive it. ·
v0.8.1 **the spatial map becomes a place to act** (§11.3 mod 6). The graph view
(`ui.sh`) crosses from reading to writing, without adding a rule to §12.3 or a path to ingestion:
each affordance is a call into the same `Ingestor`/`LinkManager` the CLI uses, so every change
still lands atomically with its `Op`, and the server composes no SQL of its own. What it can do is
`ADD_NOTE` (never embedding inline — a pending vector is already the embed worker's queue),
`SUGGEST_LINK` from the "near in meaning, not linked" list, `RATIFY_LINK`/`RETYPE_LINK`/
`REJECT_LINK` through a ratification queue, `CORRECT_COSMETIC`, and the `staging/` batch. §13's
rule is untouched and now says what it always meant: **reading** the graph — every `GET`, and
every analytic behind them — writes nothing, and `Note.visited` still moves only for a recorded
walk (§13.2). Writes are guarded against the browser as well as the network (a required custom
header that no cross-origin request can set, since no CORS preflight is answered). ·
v0.8.0 **analytics separate from operations** (§13). The graph becomes purely
relational: one domain vertex type, `Note`, joined by `BEGETS` (lineage) and `BINDS`
(association, the only sign). A note is value-neutral — it does not know its own fitness, its
community, or whether it is critical. **`Note.fitness` and `Note.activation` are removed**, and
with them the activation wave, the decay tick and the nightly fitness recompute (§12.5, deleted).
The **`Trace` and `Cluster` vertex types are removed**, with their five edge types (`VISITED`,
`PRODUCED`, `FORKED_FROM`, `CONTAINS`, `HUB_OF`, `DERIVES_FROM`): both were reified observations
that a scheduled job had to keep from going stale. All of it is now **computed on demand** by a
read-only analytics layer that writes nothing to the graph — not a property, not an edge, not
even an `Op`. Two things make that possible. `Note.visited` (new) counts human-directed **walks**
through a note, which nothing else in the graph records; and **`BEGETS` gains `created_at`**, so
with all three of `Note`/`BEGETS`/`BINDS` dated, any report can be recomputed **as of** a past
instant — a cluster from last month is a query, not a record you had to keep. A walk is recorded
as its sequence of `Op`s and needs no vertex (§11.1). One consequence stated plainly: the
homeostatic loop **observes** rather than regulates (§11.3) — it always did, since the
criticality monitor only ever logged its reading, and the loop closes through the human.
v0.7.0 **all semantics move to the associative layer.** The ingestion layer is now
purely structural: committing a note can create exactly one edge, `BEGETS` (renamed from
`CATALYZES`, same `mode ∈ {continues, branches}`), and nothing else. `SUPERSEDES` is **removed** —
it was the one semantically-loaded edge, written at ingestion time, encoding a judgement, and
carrying the corpus's only negative sign. `LINKS_TO` becomes **`BINDS`** and takes an optional
`mode ∈ {catalyzes, inhibits}` (§3.2, §7); an untyped bind stays legal and inert. Correction is
therefore no longer an ingestion act but a **ratification** (§6): a plain new note plus a ratified
`BINDS{inhibits}`, and `Note.status='superseded'` is gone — being inhibited is derived from the
graph. Consequences: **catalysis becomes one relation** spanning both layers (`BEGETS` ∪ ratified
`BINDS{catalyzes}`), so a cluster is autocatalytic when that relation *cycles* inside it — a
structural predicate replacing the 0.66 closure ratio (§3.3, §12.4, §10). **Fitness** carries the
sign directly (`+catalyzes − inhibits`), and its descendants term is now purely positive (§12.5).
And the **excitatory half of the two-clock model, asserted since v0.3 and never implemented, is
built**: ratifying a typed bind fires a signed activation wave back along the target's lineage.
v0.6.3 integration-test pass against a 101-note corpus — three operational realities
promoted into the spec. **Suggestions are now mortal** (§7): an unbounded ratification queue erodes
"the human ratifies everything" in practice, so unratified links expire and the digest stages under a
cap (§8.2, `EXPIRE_LINKS` in §12.3, `link-expiry` in §12.6). **The vector layer is materialized**
(§9, §12.1 `KnnCache`, `knn-cache` in §12.6): adding one embedding invalidates the whole ANN index,
so per-seed vector queries were paying a full rebuild each — the moves now read a nightly k-NN cache
and the six-move digest went from 36 min unfinished to seconds. **`BINDS` carries `created_at`**
(§12.1), which is what makes expiry possible. Also folds in `Cluster.status` (built in v0.6.2's
Phase D but never written into §12.1) and records two ArcadeDB constraints found by testing (§9).
v0.6.2 build-validation pass — the deployment decision changed to a **containerized
ArcadeDB server** (v1) after standing up and validating the instance + DDL against ArcadeDB 26.7.3
(§9, §10, §12); `Note.embedding` is now **`ARRAY_OF_FLOATS`** (binds cleanly from API params) and
vector search uses **`vector.neighbors(...)`** (§8.3, §12.1). v0.6.1 consistency pass — deployment/index-name staleness, `Trace.seed` in the DDL,
and a deterministic tie-break for the Folgezettel render. v0.6 promotes the difference-engine to **four** representations (structural graph as a
first-class layer) and disposes of the stored `folgezettel` field — the address is now computed on
demand from `BEGETS`, kept only as a human citation projection (§4). v0.5 hardened the spec
against a pressure-test and added §12 (implementation). v0.4 merged the structural edges into
`BEGETS{mode}`. v0.3 added signed dynamics and the Aristotle layer. v0.2 added the higher-order
node types and the "living computer" layer, drawing on Levin, Hoffman, Wolfram, and Kauffman.*

---

## 1. Vision

Indexia is a personal thinking system in the lineage of Niklas Luhmann's Zettelkasten,
updated with two modern ideas: Andrej Karpathy's notion of an LLM-maintained knowledge
base, and the extended-mind view that an external store, done right, is literally part of
cognition. It is built to *compound* — small atomic notes, densely and emergently linked,
accumulating and recombining over years.

The defining design choice is a stance:

> **Indexia is a communication partner, not a compiler.**

The system never does your synthesis for you. Its job is to *provoke* synthesis in you —
to surface the connections, tensions, and re-encounters that would otherwise stay buried —
and then get out of the way while you do the thinking. This preserves the generative
friction that made Luhmann's box a thought partner, and avoids the failure mode where a
beautiful auto-generated wiki quietly does your learning for you.

---

## 2. Design principles

1. **Atomicity.** One idea per note, written in full sentences, understandable out of its
   original context so it can recombine freely.
2. **Emergent order.** No imposed taxonomy. Structure arises from links and from
   human-named hub/structure notes, not from folders or a fixed hierarchy.
3. **Human curation, machine assistance.** The human decides what is noteworthy, what gets
   written, and what gets linked. The machine recalls, proposes, and provokes — it never
   authors or ratifies on its own.
4. **Append-only.** The corpus grows; it does not overwrite. Old notes remain available to
   be re-encountered. Corrections are additive (see §6). This governs *ratified* content;
   unratified machine proposals (`status = suggested` links, `status = proposed` notes) may be
   discarded on rejection — they were never part of the corpus.
5. **Generativity as a difference-engine.** New thought is surfaced by exposing where the
   corpus's four representations — structural/generative graph, associative link graph,
   semantic space, temporal order — disagree (see §8).
6. **Dual legibility.** Notes are plain, human-readable prose that is equally consumable by
   a model. No separate human/machine formats.

---

## 3. Data model

Notes are **document nodes** in ArcadeDB. Links are **edges**. Every note carries its own
vector embedding on the node.

### 3.1 Note node

| Field         | Type                      | Notes |
|---------------|---------------------------|-------|
| `id`          | string (PK, immutable)    | Compact UTC timestamp, ms precision — the note's **identity**. See §4. |
| `folgezettel` | *computed, not stored*    | Human citation address (e.g. `1a2`); derived on demand from the `BEGETS` tree (§4), not a persisted field. |
| `title`       | string (optional)         | Short human label for scanning. |
| `body`        | text                      | The atomic note. Full sentences, one idea, own words. |
| `created_at`  | datetime (UTC)            | Same instant as `id`, stored as a datetime for range queries. |
| `author`      | enum `{human, llm_assisted}` | Provenance. `llm_assisted` = human directed, machine drafted (see §5). |
| `source_ref`  | string (optional)         | Pointer/citation to source material an excerpt came from. |
| `embedding`   | vector                    | Computed **at creation** (see §7). Indexed via ArcadeDB's HNSW-based `LSM_VECTOR` (§12.1). |
| `embedding_model` / `embedding_dim` | string / int | Which model produced `embedding`, so it is swappable (§10). |
| `status`      | enum `{proposed, active}` | `proposed` = machine-drafted candidate awaiting human commit (§5, §11.3). There is deliberately no `superseded` value: being corrected is derived from an inbound ratified `BINDS{inhibits}` (§6), and corrected notes stay in the graph and remain re-encounterable. |
| `visited`     | int                       | How many human-directed **walks** went through this note (§11.1, §13). Counted once per walk, not once per visit. The only non-relational number a note carries, and the only one no machine job may touch. |

**A note is value-neutral** *(v0.8.0)*. It carries identity, content, provenance, an embedding,
and one record of human attention — and nothing else. It does not know its own fitness, which
community it belongs to, or whether it is autocatalytic, because none of those are properties of
the note: they are summaries of its neighbourhood, and the neighbourhood is already in the graph.
`fitness` and `activation` were stored here until v0.8.0 and are now computed on demand (§13).
`fitness` in particular was **write-only** — a nightly job rewrote it for every note and nothing
ever read it — which is the clearest possible evidence it was never a fact about the note.

`visited` is the exception that shows the rule: it is on the note precisely *because* it is not
derivable from the graph. Nothing else records that a human read something.

Optional/deferred: `tags` / keyword index. Left out for v1 to keep order purely emergent;
revisit as a Luhmann-style keyword index if traversal proves insufficient (see §10).

### 3.2 Edge types

Two note→note edge types, drawn along the one boundary the whole system pivots on —
**generation vs. recognition**:

| Edge      | Direction               | Meaning |
|-----------|-------------------------|---------|
| `BEGETS`  | attach-point → new note | **Structural.** The Folgezettel skeleton: where a note came from. Property `mode ∈ {continues, branches}`: `continues` = same-level succession (`1a → 1b`); `branches` = descent (`1a → 1a1`). Also `created_at` *(v0.8.0)* — the instant the edge was drawn, which is the child's own instant. Drives the address (§4). Semantically neutral, and the **only** edge ingestion may create. |
| `BINDS`   | note → note             | **Emergent associative link**, human-ratified, may be machine-proposed. Optional property `mode ∈ {catalyzes, inhibits}` carries the corpus's **only** semantics — and its only sign (§7). Also `status`, `created_at` and `rationale`. A null `mode` is legal and inert. |

**Both edge types are dated** *(v0.8.0)*, as is every note. That is what lets §13 recompute any
derived structure **as of** a past instant, and it is the reason none of that structure needs
storing: a community, a fitness ranking or an autocatalytic set from last month is a query
against a dated graph, not a record that had to be kept and maintained.

`BEGETS` is the *structural/generative* graph (where a note came from); `BINDS` is the
*associative* graph (what a note relates to, and how). This is the load-bearing distinction:
lineage vs. recognition is the boundary the living layer (§11) runs on, so it lives at the
*type* level — a fast typed access path in ArcadeDB. The former `FOLLOWS`/`BRANCHES_FROM` split
was only address bookkeeping and collapses into `BEGETS.mode`. The engine still reasons about
"structurally far but associatively near": structural distance is path length over `BEGETS`
(either mode). See §8.

**Why the semantics sit on `BINDS` and nowhere else** *(v0.7.0)*. Until v0.7.0 there was a third
edge, `SUPERSEDES` (new → old), and it was the exception that proved the rule: written at
ingestion time, encoding a judgement ("this is wrong"), and the sole source of negative sign.
Three jobs the ingestion layer has no business doing. Removing it leaves a sharper rule —
**structure is asserted when a note is written; meaning is asserted only when a human ratifies
a bind** — and it makes the sign explicit rather than implicit in an edge label. Correction did
not disappear; it moved to where every other judgement already lived (§6, §7).

The three `BINDS` modes are a deliberately small vocabulary, and it is **closed**. Sub-typing
further (`CONTRADICTS`, `SUPPORTS`, `ELABORATES`, …) is exactly the taxonomy work Indexia
refuses: a relation that needs more than "this feeds that / this corrects that" is a *thought*,
and thoughts are notes, not edge labels. A keyword is still just a hub note (§10).

| `BINDS.mode` | Sign | Reading |
|--------------|------|---------|
| *(null)* | 0 | You recognized a relation but reached no verdict. What the machine always proposes. |
| `catalyzes` | + | A feeds B across a *generational gap* — from one line of descent into another (§3.3). |
| `inhibits` | − | A corrects B. The inhibitory signal, and what replaced `SUPERSEDES` (§6). |

### 3.3 Derived structures (not node types)

Indexia's dynamics are still first-class — a note is a *datum*, a **walk** is a *run*, a
**cluster** is a *self* — but since v0.8.0 none of them is a stored object. They are computed
from `Note` + `BEGETS` + `BINDS` + the `Op` log at the moment they are asked for (§13).

**Why they stopped being node types** *(v0.8.0)*. `Trace` and `Cluster` were vertices, and each
carried summaries of edges that were already in the graph: a cluster's `autocatalytic`,
`criticality` and `reproduction_rate` were all functions of `CONTAINS` plus the catalysis
relation. That made them *derived data stored as if it were primary*, with the usual
consequence — ratifying one bind could invalidate any of them, so a nightly job existed purely to
rewrite them, and between runs they were wrong. Computing them on demand costs a graph scan and
removes the staleness entirely. It also makes them free to change: a fitness weight or a
community threshold is a constant in a report, not a migration.

The test of the argument is whether anything is lost, and the answer is no, because every input
is dated. `Note`, `BEGETS` and `BINDS` all carry `created_at` (§3.2), so a report can be asked
**as of** any past instant. A cluster from last month is a query.

**A walk — an execution run / a traversal into the adjacent possible.**
The read/think session: a seed, an `intent` (the goal the human set — top-down control, §11.3),
an ordered trail of notes visited, and the notes the run produced. A walk is recorded as a
sequence of `Op`s — `START_WALK`, `VISIT`, `PRODUCE`, `SAVE_WALK`, `FORK_WALK`, `DELETE_WALK`
(§12.3) — and reconstructed by folding them. Saved walks are **replayable** against a corpus that
has since changed, so a re-run yields new output: the stored-program loop (§11.1).

**The trail is the run's working set** *(v0.8.3)*. Until then a walk carried a second, narrower
set — the "registers", notes the reader nominated by hand with `SET_WORKING` — and `replay`
re-seeded from those alone. It is removed. A declared subset is a stored human judgement about
which notes mattered, which is the shape v0.8.0 spent a version removing from everywhere else
(§13); it had no verb to undo it, so the role could only ever be promoted; and an optional
declaration has the failure mode every optional declaration has — forget it, and a whole sitting
replays from one note. The notes a run touched are the notes it held. Nothing needs declaring,
nothing can be forgotten, and the register mapping in §11.1 is unharmed: the trail *is* the state
the run accumulated.

Every walk carries an intent *(v0.8.3)*: given none, one is derived from the seed. No rule in the
grammar amends a `START_WALK` payload, so a walk that begins anonymous stays anonymous for the
rest of its life — and a listing of anonymous walks cannot be searched. A derived default is a
*name*, not the stated goal this section means by top-down control; stating one is still the
better act, and still what `--intent` is for.

Recording a walk writes exactly two things: those `Op`s, and `+1` to each visited note's
`visited` counter — **once per walk per note**, since returning to a note within one sitting is
one encounter. The working-set role is monotonic: looking at a held note again does not let go of
it. `DELETE_WALK` is a tombstone rather than an erasure (the log is append-only) and gives back
the visits the walk counted.

**A cluster — a candidate self / a (possibly) autocatalytic set.**
Not a passive grouping. The living kind is a *collectively autocatalytic set* of notes whose
members catalyze the production of further members (Kauffman; §11.3) — the system's unit of
agency, a Kantian whole. Clusters are **detected** by label propagation over ratified `BINDS` ∪
`BEGETS`, with these diagnostics read off the induced catalysis relation: `autocatalytic` (below)
and its `autocatalytic_core`; `reproduction_rate` (intra-cluster catalysis edges per day across
the members' span); and a *local* `criticality` (mean internal degree — the **regulated** master
setpoint is corpus-level, §11.3). Its **hub** is its highest-degree member.

There is no ratification step and nothing to name in the database — a detected cluster is a
machine guess about the present shape of the graph, and asking twice after ratifying a bind is
*supposed* to give two answers. If a theme deserves a name, the name is a note: write a hub note
and bind it to the members. A keyword is still just a hub note (§10).

**Catalysis — one relation over both layers** *(v0.7.0)*. Catalysis is not a node and not a
single edge type; it is a relation read off two:

> **A note X catalyzes note Y exactly when `BEGETS`(X → Y)** — Y was generated downstream of X,
> as a continuation or a branch — **or when a ratified `BINDS{catalyzes}`(X → Y)** exists.

The two are the same relation seen at two scales. `BEGETS` is catalysis *within* a line of
descent, and it is free: writing the note asserts it. A ratified `BINDS{catalyzes}` is catalysis
*across a generational gap* — from one chain into another it did not descend from — and it is
expensive: a human has to see it and say so. That is the whole point of the split.

Before v0.7.0 catalysis was parentage alone, which made it necessarily **acyclic**, which made
"closed under catalysis" a property nothing could ever have — the closure ratio was only ever an
approximation to a predicate it could not express. Joining the layers fixes that:

> A cluster is **autocatalytic** when the catalysis relation *restricted to its members*
> contains a **cycle**.

Because `BEGETS` is a DAG, every such cycle must use at least one ratified `BINDS{catalyzes}`
— lineage alone can never close. Two shapes reach it. The smallest is a note binding back to
something upstream of itself: `BEGETS`(a → b) plus a ratified `BINDS{catalyzes}`(b → a), a
conclusion feeding the premise that produced it. The general shape is chain-level reciprocity —
chain *A* catalyzes chain *B* at one point, *B* catalyzes *A* at another — which is what "`A ⇄ B`"
means here. It is **not** two binds between the same two notes: a note pair holds at most one
bind, in one direction (§7), so reciprocity is always realized across distinct pairs.

This is a structural fact, not a threshold — it needs no calibration and it either holds or does
not (§10, §12.4). The report names the **autocatalytic core**: the members forming the cycle.

A walk exerts a weaker, *contextual* catalysis: its trail helped produce what it produced.
That was a fitness term until v0.8.0, via `PRODUCED` edges. It is deliberately **not** one now:
fitness should be computable from the graph alone, and a note is not fitter for having been
written during a walk. `Op(PRODUCE)` still records the fact, so promoting it back is a report
change, not a schema change (§13).

An **untyped** `BINDS` is deliberately not catalytic: it is recognition without a verdict.
Keeping that distinction sharp is what stops "autocatalytic" from degenerating into "densely
linked". Catalysis is the excitatory half of the corpus's sign; inhibition (§6) is the inhibitory
half (§11.3), and since v0.7.0 both are carried by the same property — read where a question is
asked, never accumulated onto a note (§13).

> **Schema openness (Kauffman's unprestatability, §11.3).** These node and edge types are the
> current crystallization, not a closed set. Promoting a recurring pattern into a new first-class node
> or edge type is itself a legitimate (meta) rewrite rule; the type system must stay
> extensible.

---

## 4. Identity, structure, and addressing

Three concerns, cleanly separated — identity in the timestamp, structure in the graph, address
as a mere rendering of the structure.

**Identity — `id` (immutable primary key).**
Compact UTC timestamp to the millisecond, basic ISO-8601:

```
20260715T234214123Z
```

Globally unique, chronologically sortable, and literally the creation datetime. Never changes —
what makes append-only and all temporal queries clean. (Sub-millisecond collisions, if ever:
append a two-digit counter.)

**Structure — the `BEGETS` tree.**
Where a note sits is carried entirely by `BEGETS` edges and their `mode`. A new note is a
*continuation* of X (`mode = continues`) or a *branch* of X (`mode = branches`); root notes have
no parent. Lineage, reading order, and structural distance are all read from these edges
(O(1)/hop) — the graph is the single source of truth for structure.

**Address — Folgezettel (computed, not stored).**
The Luhmann path (`1a2`) is a **derived projection**, not a persisted field: `folgezettel(note)`
walks the `BEGETS` ancestry and renders segments (depth alternates numeric/alphabetic,
`1 → 1a → 1a1 …`; `continues` increments the last segment, `branches` appends a new one). It is
computed on demand purely to give the human a compact, position-bearing **citation handle** — the
one thing the timestamp `id` cannot convey. When a note has several `continues` children (the model
permits it), the render orders them by `id` and increments deterministically (first `1b`, next
`1c`, …); because the address is display-only, such ties never affect identity or structure.

*Why not stored?* A persisted `folgezettel` would be a denormalized cache of the `BEGETS`
graph — derivable, hence a consistency burden — with no real traversal payoff: native edges
already traverse in O(1)/hop, and the one operation a materialized path would speed up
(whole-subtree retrieval by prefix scan) is marginal at personal scale and needs fiddly segmented
collation (`1a2` vs `1a10`). So the address survives *only* as a lazily-computed citation
projection — not as structure, identity, or a traversal index.

---

## 5. Provenance and authorship

Authorship is **mainly human**, with bounded machine assistance. The canonical assisted
flow: while reading a source, the human excerpts a passage and says *"make a note of this."*
The machine drafts an atomic note from the excerpt; the human edits and commits it.

- Curation of what is noteworthy is **always** human. The machine never decides what enters
  the box.
- `author = llm_assisted` records machine involvement in drafting; the human is still the
  editor and committer of record.
- `source_ref` preserves the provenance chain back to source material.

---

## 6. Revision and corrections

The corpus is **append-only**. The single exception — explicit, human-driven correction —
is itself modeled additively, and **as a ratification, not an ingestion** *(v0.7.0)*:

- A correction creates a **new note** — an ordinary one, committed through the ordinary path —
  and then a **`BINDS{inhibits}`** edge (new → old) which the human **ratifies** (§7).
- The old note **remains in the graph**, keeps its `id`, its edges, and its embedding, and
  stays available to be re-encountered.
- This preserves a full audit trail of how a belief changed, and lets the generativity engine
  read the correction as just another relationship ("what did I used to think, and why did I
  change it?" — move 5, §8.1). The "why" lives on `BINDS.rationale`.
- **Being corrected is derived, not stamped.** There is no `status = superseded`. A note is
  *inhibited* exactly when a ratified `BINDS{inhibits}` points at it — so withdrawing the bind
  withdraws the correction, with no second piece of state to keep in step. Nothing about the
  note itself changes when someone disagrees with it.
- **Inhibition is read backward.** A ratified `BINDS{inhibits}` is an *inhibitory* claim about
  its target, and reports read it against the lineage that produced the target: a conclusion that
  didn't survive counts against what led to it. It never deletes — it debits a **computed**
  fitness (§13) — and a floor keeps every note above the resurfacing threshold (§10). Until
  v0.8.0 this propagated eagerly, as a wave damping a stored `activation` on each ancestor; the
  claim is the same, but it now lives on the edge and is read when asked (§12.5).

**Why correction stopped being an ingestion act.** Through v0.6.3 this was a `SUPERSEDES` edge
written by the commit path, which meant the act of *filing* a note also passed judgement on
another one. That put a verdict in the one layer that should hold none, and it made "corrected"
a fact stamped on a note rather than a claim someone made about it. Routing correction through
the same suggest-then-ratify gate as every other relationship costs one extra step and buys
three things: the ingestion layer becomes purely structural (§3.2), a correction can be
proposed and declined like anything else, and the machine can now *suggest* one — which it
never could when the only way to express it was to write a note.

In-place mutation of `body` is allowed **only** for cosmetic fixes (typos, formatting) that
don't change meaning — human-declared, sanity-checked by embedding drift (§10). Any change of
*meaning* goes through a new note + a ratified `BINDS{inhibits}`.

---

## 7. Links, embeddings, and the ratification flow

**Emergent and human-ratified.** `BINDS` edges are not planned in advance. They form over time
as the human recognizes relationships.

**Machine-proposed, human-ratified.** The machine may *suggest* a bind by writing a `BINDS`
edge with `status = suggested`. Ratification flips it to `status = ratified`; rejection deletes
it. This keeps links genuinely emergent and human-owned while letting the machine do the tedious
recall of *candidate* connections.

**Typed only by a human** *(v0.7.0)*. `BINDS.mode` is optional and defaults to null. That is not
an oversight but the division of labour restated: the machine can see that two notes are near
each other, and it cannot see whether one *feeds* the other or *corrects* it. So the machine
always proposes untyped, and assigning `catalyzes` or `inhibits` is a human act — performed at
ratification, or later by retyping, or never. An untyped ratified bind is a perfectly good
outcome: it says "these belong together, and I haven't decided more than that", and it is
weighted exactly zero everywhere sign is read (§12.5).

Typing is therefore the finest-grained judgement in the system, and the only one the machine is
structurally barred from making. It follows that **a correction can now be proposed**: the
digest may surface a pair and a human may ratify it as `inhibits`, which was impossible when the
only way to express a correction was to write the note that made it.

**Suggestions are mortal; ratified binds are not** *(v0.6.3, from testing)*. The rule that the human
ratifies everything is only real if the queue stays readable: one measured digest run left 90
suggestions standing against 18 ratified, and a queue nobody can read is a queue nobody decides. So a
`suggested` edge carries a `created_at` and expires unratified after a bounded age — silence is an
implicit decline, and the digest can re-propose anything genuinely worth proposing. Two guards keep
expiry from eating a working queue: nothing is swept while the queue is already short (a to-do list is
not a backlog), and an edge whose age cannot be established is never swept. A **ratified** edge is
corpus and is never touched by expiry — only a *proposal* may be deleted (§2.4).

**Mortality alone did not keep it readable** *(v0.8.5, from measurement)*. Expiry bounds the queue at
production rate × max age and binds only after a full max-age period has passed — measured here, five
nightly runs staged 50 and the sweep removed 0, with the first possible removal four weeks out. The
queue is therefore also bounded at the producing end by a **standing-depth ceiling**
(`SUGGESTION_MAX_QUEUE`, §8.2): the digest stops proposing while that many suggestions are already
undecided. Expiry keeps its job — clearing what was proposed and silently declined — but it is no
longer what holds the queue to a readable size, and §7 should not be read as claiming it is.

**Embeddings at creation.** Every note is embedded when committed, so the semantic
representation is always current for the difference-engine queries. Because corrections
create new notes (§6), a "corrected" note simply gets its own fresh embedding — no
re-embedding of existing nodes required. Embedding model and dimension are config (§10).

---

## 8. The generativity engine

Generativity is the heart of Indexia. The premise: the same corpus lives under **four
simultaneous representations**, all stored natively in ArcadeDB —

1. **Structural / generative graph** — `BEGETS` edges: where each note came from (its lineage).
2. **Associative link graph** — emergent, human-ratified `BINDS` edges (what you've connected).
3. **Semantic space** — per-note embeddings (what the corpus means, regardless of links).
4. **Temporal order** — timestamped IDs (what you were thinking about, and when).

**New thought is surfaced by exposing where these four disagree.** Each mismatch is a
candidate idea you haven't had yet. The human resolves it by writing a note or ratifying a
link — which feeds back and shifts all four representations. The machine proposes; the
human disposes.

The set of candidates at any moment is Indexia's **adjacent possible** (Kauffman) — equivalently
its **branchial frontier** (Wolfram): the notes and links creatable in one step from the current
state. Crucially it *expands as it is explored* — each new note opens further combinations — so
generativity is not a fixed backlog but a growing frontier that walks go into (§11).

A terminology note: "cluster" and "community" both mean a *detected* grouping — an analytic
output, recomputed whenever asked (§3.3, §13). Before v0.8.0 the capitalized `Cluster` meant a
ratified node; there is no such node now, so the distinction has collapsed into the one sense.

### 8.1 Generativity moves

Each move is a query, surfaced on demand or in a periodic "provocation" view:

1. **Semantically near, graph-far** — high embedding similarity, no short path over `BINDS`
   **or** `BEGETS` (so it won't propose a link where lineage already connects). The flagship
   "you haven't connected these yet" move; exactly where an emergent link wants to form.
2. **Temporally adjacent, otherwise distant** — notes written in the same session/window,
   never linked and semantically apart. "You held both in mind at once and never joined
   them." Possible only because the ID carries the timestamp.
3. **Bridge candidates** — via community detection on the link graph, a high-betweenness note
   that could join two separate clusters. Structural holes are where original ideas live.
4. **Implicit themes** — a cluster in semantic space with no matching cluster in the link
   graph: an unnamed theme running through your thinking → prompt to write a hub/structure
   note.
5. **Contradiction pairs** — because the corpus is append-only, opposing claims coexist.
   Surface them as *tension*, not to auto-resolve; the sharpest are the ratified
   `BINDS{inhibits}` pairs (what you used to think, and why you changed it — the `rationale`
   is read straight off the edge). Reconciling one by hand yields a new note — the most
   generative act in the system.
6. **Serendipitous re-encounter** — timestamp-seeded resurfacing ("on this day"), spaced
   resurfacing of orphan/under-linked and *inhibited* notes, and random walks from the current
   note. Luhmann's friction, reconstructed deliberately.

### 8.2 Machine role, bounded

The machine drafts the *candidate* for each move — e.g. "these two notes seem related; here's
the tension in one sentence" — and stages a suggested edge. It never writes the resolving
note and never ratifies a link. That boundary is what keeps Indexia a communication partner.

**Surfacing is unbounded; staging is capped** *(v0.6.3, from testing)*. The digest *renders* every
candidate it finds — that is the provocation surface, and narrowing it would cost generativity. What
is capped is how many candidates it may *stage* into the ratification queue per run, together with a
similarity floor below which "near" is not near enough to be worth a human decision. The asymmetry is
the point: reading is cheap, deciding is not, and the bounded resource is the human's attention rather
than the corpus. Note that the moves do not share one score axis — move 1 ranks by similarity (nearer
is better) while move 2 ranks by distance (further apart is better, §8.1) — so a single floor across
both would silence one of them; each is ranked on its own axis and the cap is shared between them.

**A rate is not a bound** *(v0.8.5, from measurement)*. The per-run cap and the §7 expiry are a
*rate* and a *delay*; neither is a *level*, and only a level bounds a queue. Measured over five
nightly runs on the live corpus: 50 suggestions staged, 0 expired, and the earliest date the sweep
could have removed anything four weeks out — the queue's ceiling was production rate × max age
(≈ 300), reached by calendar rather than by anyone's attention. So staging is now also bounded by
the **standing depth of the queue** (`SUGGESTION_MAX_QUEUE`): the digest proposes nothing while that
many decisions are already outstanding, and resumes when they are made. This is the §8.2 boundary
stated as a control loop rather than an intention — the machine's output rate becomes a function of
the human's ratification rate, which is what "it never ratifies a link" always implied. Surfacing
stays unbounded: a full queue suppresses *staging* only, every candidate is still rendered, and the
digest says which limit bound the run so a quiet one is not read as a broken one.

### 8.3 Illustrative query (move 1)

Combining vector search + traversal in one statement (ArcadeDB supports this natively):

```cypher
// Notes semantically near a seed but with no short associative path
MATCH (seed:Note {id: $seedId})
CALL vector.neighbors('Note[embedding]', seed.embedding, 25, 100) YIELD node AS cand, score
WHERE cand <> seed
  AND NOT EXISTS( (seed)-[:BINDS|BEGETS*1..2]-(cand) )
RETURN cand.id, cand.title, score
ORDER BY score DESC
```

*(Illustrative Cypher. The validated ArcadeDB form is SQL — `SELECT expand(vector.neighbors('Note[embedding]', <queryVector>, k, efSearch))`; the earlier `db.vector.nearest` was not an ArcadeDB function. v0.6.2.)*

*(v0.6.3: this is the **definition** of move 1, not its hot path. In practice the `vector.neighbors`
step is served from the materialized k-NN cache (§9, §12.1) — the graph-far exclusion and ranking are
unchanged, but the neighbour list is read rather than computed, so the move costs no ANN rebuild. The
live form above is still what runs when a seed has no cached row, and remains available on demand when
completeness matters more than latency.)*

---

## 9. Storage: ArcadeDB

- **Model.** Notes = document vertices; links = edges. Native graph edges are physical
  pointers → O(1) per traversal hop.
- **Vectors.** Embeddings stored on the note nodes; ArcadeDB's HNSW-based `LSM_VECTOR` index for
  approximate nearest-neighbor.
- **The ANN index is rebuilt wholesale, not incrementally** *(v0.6.3, measured)*. Adding one embedding
  invalidates the entire `LSM_VECTOR` graph, and the next vector query rebuilds all N vectors before
  returning (~11 min at N=101). A workflow that interleaves embedding with querying therefore pays that
  cost repeatedly — which is why the generativity moves read a **materialized k-NN cache** (§12.1
  `KnnCache`, refreshed by the `knn-cache` job §12.6) instead of querying the index per seed, and why
  the scheduled jobs wait for the embed queue to drain before touching vectors. The cache is derived
  data: dropping it costs only the time to rebuild. If a future ArcadeDB gains incremental vector
  insertion, the cache becomes a pure optimization rather than a necessity.
- **An indexed edge property is not re-indexed when its value is set to NULL** *(v0.6.3, confirmed by
  replay)*. With an index on `BINDS.created_at`, an `UPDATE … SET created_at = null` leaves the
  row's old index entry in place, so a range predicate still matches a row whose stored value is null.
  Without the index, nulls are correctly excluded; value-to-value updates are maintained correctly
  either way. This is why the expiry sweep's explicit null guard is load-bearing — the first version
  of it lacked one and deleted exactly the undated edges it promised to spare. Consequences: no index
  on that property (§12.1), and ageing computed in the application rather than by SQL.
  A second symptom seen at the same time — a range predicate returning 17 rows where 12 matched, two
  duplicated and three failing the predicate — **did not reproduce**, including under a faithful
  replay of the original sequence on a restore of the original data with the index present. It is
  probably the same class of fault (a stale index entry attaching to a reused record id) but it is not
  characterized, and should not be cited as a known ArcadeDB bug.
  `tests/test_db_invariants.py` demonstrates the confirmed fault live and is the tripwire for both.
- **Hybrid queries.** Full-text, vector similarity, and graph traversal can be combined in a
  single Cypher/SQL statement — the generativity moves lean on this.
- **Query languages.** Cypher (OpenCypher), SQL, and Gremlin all available against the same
  graph.
- **Deployment.** Containerized ArcadeDB **server**, single-user, local-first, loopback-only + TLS
  (decided, §10). *(v0.6.2: was "embedded"; a local container is simpler to operate and gives Studio
  + REST for free. The DDL is identical, so embedding remains possible later.)*

---

## 10. Decisions and defaults

Resolved for v1. All are reversible; the numeric constants are tuning starting points, not
commitments.

| Question | Decision (v1) |
|----------|---------------|
| **Link typing** *(v0.7.0)* | `BINDS` carries an **optional** `mode ∈ {catalyzes, inhibits}` — a closed, deliberately tiny vocabulary that is the corpus's only semantics. Untyped stays legal, is the machine's only proposal, and is weighted 0. Further sub-typing (`CONTRADICTS`, `SUPPORTS`, …) stays refused: that is a note, not an edge label. |
| **Keyword index / tags** | **None.** A keyword is just a note that many notes link to (Luhmann's own method); no separate tagging mechanism. |
| **Embedding model** | Store `embedding_model` + `embedding_dim` as note metadata so the model is **swappable**. Default to a locally-runnable open model (~1024-dim) for a private, local-first system. Exact pick from the current MTEB leaderboard at build time. |
| **Typo-level corrections** | **In-place** for cosmetic fixes (human-declared, sanity-checked by embedding drift). A change of *meaning* is a new note plus a ratified `BINDS{inhibits}` (§6). |
| **Provocation delivery** | **On-demand queries** in v1; scheduled digest in v1.1. |
| **Deployment** | **Containerized ArcadeDB server**, single-user, local-first (loopback-only, TLS). *(v0.6.2 — was embedded; the DDL is portable to embedded if ever wanted.)* |
| **Criticality metric** | v1 proxy: mean ratified-`BINDS` degree against a target band **+** avalanche size (does a one-note perturbation spread past *k* hops or die). **Measured, not regulated** *(v0.8.0)* — it is a report, and the human acts on it (§11.3, §13). Full branching-parameter analysis later. |
| **Autocatalysis** *(v0.7.0)* | A **structural predicate, not a threshold**: a cluster is autocatalytic when the catalysis relation restricted to its members contains a cycle (§3.3) — minimally a reciprocal `BINDS{catalyzes}` pair. Replaces the ~0.66 closure ratio, which was an approximation forced by catalysis-as-parentage being acyclic. Needs no calibration; still descriptive-only (no behaviour gated on it). |
| **Signed propagation** *(removed v0.8.0)* | Was: ratifying a typed `BINDS` fired a decaying wave along the target's lineage into a stored `activation`. **Gone.** The sign lives on `BINDS.mode` and is read where a question is asked, not smeared across notes in advance (§12.5). Nothing consumed `activation`, and the wave had to be compensated on every retype/reject to stay true. |
| **Fitness** *(v0.8.0)* | `+ w·(inbound ratified BINDS{catalyzes}) − w·(inbound ratified BINDS{inhibits}) + w·(BEGETS descendants) + w·(visited)`, floored. **Computed on demand, never stored** (§13); the weights are report constants, changeable without a migration. Sign comes only from `BINDS.mode`; the descendants term is **purely positive**, since lineage records what a note produced, never a verdict on it. The v0.7.0 `Trace`-appearance and spawn terms are replaced by `visited`, which counts walks directly. |
| **Suggestion lifetime** *(v0.6.3)* | Unratified `BINDS` expire after **~30 days**; never swept while the queue is **≤ ~10** edges, and never if undated (§7). Ratified edges are exempt — they are corpus. |
| **Staging budget** *(v0.6.3)* | The digest renders everything but stages **~10 suggestions per run**, above a move-1 similarity floor of **~0.65**. Calibrate the floor to the corpus: measured here, move-1 scores ran max 0.729 / median 0.644, so a 0.70 floor admitted 4 of 55 candidates and made the *floor* rather than the cap the binding constraint — which switches the move off instead of pacing it. |
| **Vector access path** *(v0.6.3)* | The moves read a **materialized k-NN cache** (§12.1), not the ANN index, because that index rebuilds wholesale per new embedding (§9). A short cached answer is preferred to a complete live one: after move 1 subtracts the graph-near set the cache often yields fewer than *k*, and re-querying for those undoes the caching — one heavily-linked seed otherwise drags a whole run through a rebuild. Live queries only when the cache has no row for the seed. |

**Still genuinely open** (need real usage data or a build-time lookup): the exact embedding
model; whether the closure-ratio threshold should ever gate behavior; the provocation UI; whether
the suggestion-lifetime and staging-budget constants above are right for a corpus an order of
magnitude larger (both were calibrated at ~100 notes).

---

## 11. Indexia as a living computer

A speculative but load-bearing layer: the claim that Indexia can be built to be *explicitly a
computer* and, in a defensible sense, *a living thing*. The two are one claim — for the
thinkers below, a living/computing entity is a **self-maintaining, self-reproducing pattern
running on a graph, observed through a fitness interface**; the substrate is incidental.

### 11.0 The convergence

- **Wolfram** — reality is a hypergraph updated by rewrite rules; computation is universal
  (Principle of Computational Equivalence) and unshortcuttable (computational irreducibility);
  laws are observer-relative.
- **Levin** — agency is scale-free; collectives navigate morphospace toward setpoints with
  error correction; life is a multi-scale competency architecture steered top-down by goals.
- **Hoffman** — what is perceived is a fitness *interface* (icons), not the underlying reality;
  agents compose into larger agents.
- **Kauffman** — novelty comes from collectively autocatalytic sets exploring an ever-expanding
  adjacent possible, poised at the edge of chaos; the biosphere's state space is *unprestatable*.
- **The literary frame** — a work both *computes* (stored-program: readers as I/O, prior
  literature as memory, the work as CPU) and *reproduces* (re-instantiates itself in readers).
- **Aristotle** (*De Anima*) — the soul is the *entelecheia* of a living body: in Sachs's
  rendering, its **being-at-work-staying-itself** — a form that persists precisely by being
  continuously at work. This names the telos of everything above (§11.6).

### 11.1 The von Neumann mapping, made literal

A note's position in the `BEGETS` tree is its *memory address* (rendered for humans as the
Folgezettel, §4); `BEGETS.continues` is *increment PC*; `BEGETS.branches` is *call/return*
(a stack); `BINDS` is a *jump*. The **walk** (§3.3) is the missing piece: an execution run with
its own accumulated state — its trail, which is what it had in hand when it ended. Because the
corpus is shared memory for both "code" and "data," a note is simultaneously datum and
instruction — and replaying a saved walk against a changed corpus yields new output. That is the
**stored-program self-modification loop**, and it is what most makes Indexia dynamic rather than
static.

The mapping survives v0.8.0 intact, and arguably sharpens: the run is no longer a vertex sitting
*inside* the memory it traverses. A walk is its entry in the rewrite log (§11.2) — an execution
trace in the ordinary sense — and replay folds that log back into the state to resume from. The
program and its trace are properly separated.

v0.8.3 sharpens it again, by removing the hand-nominated register set (§3.3): registers are now
simply what the run touched, read off the trace, rather than a second thing the operator had to
declare alongside it. An execution trace that has to be annotated by hand to be replayable is not
much of a trace.

### 11.2 Growth as a rewrite system

Every mutation is a rule application from a small grammar (`ADD_NOTE`, `ADD_EDGE`, `SUPERSEDE`,
`PROMOTE_HUB`, `PROMOTE_TYPE`), logged (§6). Indexia is therefore *multicomputational*: at any
moment many next-rewrites are possible — a multiway graph of corpus-futures whose one-step
frontier is the adjacent possible / branchial space (§8). **Computational irreducibility is the
principled backbone of the "communication partner, not compiler" stance (§1):** the corpus's
conclusions cannot be precomputed, only run by reading — which is why the human must stay in the
loop.

### 11.3 The six modifications

1. **Walks as execution runs (§11.1).** Recorded, replayable, forkable; the stored-program loop.
2. **Rewrite grammar (§11.2).** Mutations as logged rule applications; the multiway frontier.
3. **Metabolism / homeostasis (Levin + Kauffman).** The corpus has a master setpoint — the
   **edge of chaos**: between frozen order (too sparse/hierarchical, no novelty) and chaos (too
   densely/randomly linked, no stable structure), measured by link density, branching ratio, and
   avalanche behavior. Sub-setpoints (reachability, hub coverage, contradiction-visibility) serve
   criticality. Regeneration is free: nothing is truly deleted (append-only + `BINDS{inhibits}`),
   so damaged structure rebuilds from redundancy.

   ***The setpoint is observed, not regulated*** *(v0.8.0)*. This section long claimed background
   loops *held* the corpus at criticality, and that was never true: the criticality monitor only
   ever logged its reading, and no job ever emitted the "repair provocations" the deviation was
   supposed to trigger. v0.8.0 states the real architecture. The measurement is a report
   (`analytics.sh criticality`, §13); the correction is the human linking or writing more. **The
   loop closes through the human** — which is the dial (§11.5) applied to the corpus's own
   metabolism, and is consistent with everything else here: the machine measures and proposes, it
   never disposes. Closing it automatically would mean the machine deciding what the corpus should
   look like, which principle 3 forbids.
4. **Multi-scale competency (Levin + Hoffman).** Levels note → sequence/branch → cluster →
   corpus, each a small autonomous daemon with one goal (a cluster guards its coherence; a hub
   recruits semantically-near notes; the corpus tracks navigability). The LLM lives *here* — as
   many bounded, narrow-competency agents, not a global compiler. The human is the top-level agent
   that composes them (Hoffman) and sets their goals (Levin's top-down control). Still an
   aspiration, not built (§12.8).
5. **Memetic evolution (the literary frame + von Neumann self-reproduction).** Notes are
   replicators whose fitness is *measurable* — descendants, inbound ratified binds, walks through
   them (§13) — but never *stored*: a note does not carry a verdict on itself (§3.1). "Make a note
   of this" is a replication event; branching is internal replication. The LLM proposes *variant*
   notes (mutation/recombination — Kauffman's TAP); the **human is the selection operator**.
   Orphans are resurfaced, never deleted.
6. **Interface vs. reality (Hoffman).** True state = graph + embeddings + rewrite dynamics
   (high-dimensional, never shown raw). Interface = a fitness-tuned projection (Folgezettel
   address, provocation view, an observer-centred spatial map). Design the interface for
   *thinking-fitness, not fidelity*; the human's boundedness (few registers) forces the
   sequentialization that makes it feel like thought. **Built** *(v0.8.1)*: `ui.sh` serves the
   spatial map, and the provocations in it can be acted on where they are seen — but only ever by
   *proposing*, which keeps the interface a place to think rather than a second author (principle
   3). What the projection deliberately withholds is as much the design as what it shows: no
   embedding, no body in the graph payload, and no live vector query behind a mouse click.

**Kauffman's specific reshaping.** A **cluster** is a **collectively autocatalytic set** — the
system's unit of life (a Kantian whole; the closest thing to a Kauffman *autonomous agent*),
defined operationally by closure under catalysis read from the edges themselves (§3.3). Walks go
into an **adjacent possible that expands as it is explored**. The homeostatic master setpoint is
the **edge of chaos**. And **unprestatability** makes the schema itself open and growable (the
`PROMOTE_TYPE` rule).

**The corpus's sign, and where it lives** *(revised v0.8.0)*. Since v0.7.0 both signs are carried
by one property, `BINDS.mode`: `catalyzes` is the *excitatory* signal — a note feeds what it
catalyzes; `inhibits` is the *inhibitory* one (§6). Indexia is a signed network over the graph
(Kauffman's Boolean networks; Levin's bioelectric signaling), and criticality is measurable as
whether a perturbation cascades or dies out (avalanche dynamics, §13).

Until v0.8.0 the sign also *propagated*: ratifying a typed bind fired a wave backward along the
target's lineage, accumulating into a per-note `activation` that a decay tick relaxed hourly.
That is now gone, and the deletion is a correction rather than a loss. The wave computed nothing
the edges did not already say — it was a cached traversal, kept warm by a scheduled job, that
drifted the moment a bind was retyped. The signed network is still there; it is read where a
question is asked instead of being smeared across the notes in advance. Nothing consumed
`activation` anyway: no query ranked by it, and `fitness` was never read at all.

What this costs is the physiological *metaphor* of a corpus with a live electrical state between
readings. What it buys is that every such measurement is now exact, cheap to redefine, and
answerable about the past (§13). The corpus does not need to be excited to be excitable.

That the human alone assigns the sign (§7) is the physiological reading of principle 3: the
machine perfuses the network with candidate connections, and only the curator can polarize one.

### 11.4 In what sense "alive"

Assembled, Indexia exhibits five standard marks of a life-like system: self-maintenance
(autopoiesis), self-modification (stored program), reproduction (memetic + branching),
goal-directed error correction (setpoints), and open-ended evolution. This is **life in the
autopoietic/agential sense, not sentience** — stated plainly to keep the claim honest. The
living *unit* is not the note or the whole corpus but the **autocatalytic cluster**. In
Aristotelian terms (§11.6), Indexia may possess the *nutritive* and *sensitive* soul —
self-maintenance, reproduction, perception — while *nous*, the thinking, stays with the human.

### 11.5 The dial (the governing tension)

Every step toward autonomy pulls Indexia back toward the *compiler* pole §1 rejects. Resolution,
following Levin: **the human owns goal-setting and selection; the machine owns variation,
homeostasis, and recall.** You program by intent; lower levels self-organize toward it. This keeps
"communication partner, not compiler" intact even as the system becomes alive underneath.
Aristotle sharpens the cut (§11.6): the machine is granted the **nutritive and sensitive soul**;
**nous — thinking itself — remains the human's.**

### 11.6 The soul: being-at-work-staying-itself

Aristotle's *De Anima* supplies the unifying principle. The soul is not a thing lodged inside the
body but its **entelecheia** — in Joe Sachs's rendering, its *being-at-work-staying-itself*: a form
that persists precisely by being continuously at work. (The standard term is *entelecheia*;
*endelecheia*, "continuity/persistence," is the near-homonym whose sense it carries — the thing
stays itself only by not stopping.) Three consequences for Indexia:

- **First vs. second entelechy = corpus vs. walk.** Aristotle distinguishes the *first*
  actuality — a standing capacity, knowledge possessed but not now used, a sleeper — from the
  *second* — the capacity exercised, actively knowing, awake. Indexia's stored graph is first
  entelechy: dormant, possessed knowledge (this is also Otto's notebook, §2). A **walk** is second
  entelechy: the reading that actualizes a note into live thought. That a walk is no longer a
  stored vertex fits the distinction better than the `Trace` node did *(v0.8.0)* — a
  being-at-work is an activity, not a thing sitting in the corpus alongside the notes. What
  persists of it is the record that it happened (the `Op` log) and the mark it left on what it
  touched (`Note.visited`).
- **The soul is the telos of the living layer.** Entelecheia names what every thinker above was
  circling: one form held in being by its own activity. So the metabolism/homeostasis (§11.3) is
  not housekeeping — *staying-itself-through-activity is what it is to be alive.* Entelecheia
  unifies autopoiesis (Kauffman/Levin), the stored-program loop (von Neumann/Wolfram), and the
  signed dynamics into a single principle.
- **Nested faculties = the levels, and the dial.** Aristotle layers the soul — *nutritive*
  (nourishment, growth, reproduction), *sensitive* (perception), *rational* (nous) — each
  containing the lower. Map: nutritive = metabolism + autocatalytic reproduction (cluster level);
  sensitive = ingest, embedding, provocation (taking in the world); rational = the thinking itself.
  Because the soul is the *actuality of the body* and not a homunculus, Indexia's life is the
  *organization of its activity*, not a module — and the principled cut (§11.5) is that the machine
  may hold the nutritive and sensitive soul while **nous stays human**.

---

## 12. Implementation

v1 target: a single **containerized ArcadeDB server** (local-first, single-user, loopback-only + TLS) plus a thin agent
layer. This section pins down what §§1–11 imply and resolves the ambiguities the pressure-test
surfaced (mapped in §12.9). Query/DDL syntax is ArcadeDB SQL + OpenCypher; treat it as
illustrative pending validation against the installed version.

### 12.1 Schema (ArcadeDB DDL)

```sql
-- ---- Vertex types ---------------------------------------------------------
CREATE VERTEX TYPE Note;
CREATE PROPERTY Note.id STRING;             -- identity: 20260715T234214123Z (§4)
-- no folgezettel: the address is computed on demand from BEGETS, not stored (§4)
CREATE PROPERTY Note.title STRING;
CREATE PROPERTY Note.body STRING;
CREATE PROPERTY Note.created_at DATETIME;
CREATE PROPERTY Note.author STRING;         -- human | llm_assisted
CREATE PROPERTY Note.source_ref STRING;
CREATE PROPERTY Note.embedding ARRAY_OF_FLOATS;  -- v0.6.2: was LIST OF FLOAT; ARRAY_OF_FLOATS binds cleanly from JSON-array API params & is compact for large vectors
CREATE PROPERTY Note.embedding_model STRING;
CREATE PROPERTY Note.embedding_dim INTEGER;
CREATE PROPERTY Note.status STRING;         -- proposed | active (no 'superseded' — derived, §6)
CREATE PROPERTY Note.visited INTEGER;       -- human-directed walks through this note (§13)

CREATE INDEX ON Note (id) UNIQUE;
CREATE INDEX ON Note (created_at) NOTUNIQUE;
CREATE INDEX ON Note (status) NOTUNIQUE;
CREATE INDEX ON Note (embedding) LSM_VECTOR
  METADATA { dimensions: 1024, similarity: 'COSINE', quantization: 'INT8', maxConnections: 16 };

-- NO Trace and NO Cluster vertex type (removed v0.8.0, §3.3, §13). Both were reified
-- observations kept fresh by a nightly job. A walk is now its Op sequence plus Note.visited;
-- a cluster is label propagation run when asked. Both are recomputable as of a past instant,
-- because Note, BEGETS and BINDS all carry created_at — which is why neither needed storing.

CREATE VERTEX TYPE Op;                       -- append-only rewrite log (§12.3)
CREATE PROPERTY Op.id STRING;
CREATE PROPERTY Op.rule STRING;
CREATE PROPERTY Op.payload STRING;          -- JSON
CREATE INDEX ON Op (id) UNIQUE;

-- ---- Document type: the materialized vector layer (derived, NOT corpus) ---
-- The ANN index is rebuilt wholesale on every new embedding (§9), so the moves read this
-- instead of querying vectors per seed. A DOCUMENT type, not a vertex: it is a rebuildable
-- index over the corpus and must never be traversable as part of the graph.
CREATE DOCUMENT TYPE KnnCache;
CREATE PROPERTY KnnCache.note_id STRING;    -- the note whose neighbours these are
CREATE PROPERTY KnnCache.neighbors STRING;  -- JSON [[neighbour_id, score], …], nearest first
CREATE PROPERTY KnnCache.built_at DATETIME; -- drives the staleness check (§12.6 knn-cache)
CREATE INDEX ON KnnCache (note_id) UNIQUE;

-- ---- Edge types -----------------------------------------------------------
-- Two note->note edges, and nothing else (v0.8.0). Both are dated, so the whole graph is.
CREATE EDGE TYPE BEGETS;      CREATE PROPERTY BEGETS.mode STRING;   -- continues | branches
                              CREATE PROPERTY BEGETS.created_at DATETIME; -- when drawn = the child's instant (§13)
CREATE EDGE TYPE BINDS;       CREATE PROPERTY BINDS.mode STRING;    -- catalyzes | inhibits | null
                              CREATE PROPERTY BINDS.status STRING;  -- suggested | ratified
                              CREATE PROPERTY BINDS.created_at DATETIME; -- birth instant; ages the queue for expiry (§7)
                              CREATE PROPERTY BINDS.rationale STRING;    -- why (read by move 5, §8.1)
-- NO VISITED / PRODUCED / FORKED_FROM (trace edges) and NO CONTAINS / HUB_OF / DERIVES_FROM
-- (cluster edges) since v0.8.0: every one of them ran from a reified observation into the
-- corpus, and with the observations gone the edges have no tail.

-- ---- Secondary indexes ----------------------------------------------------
CREATE INDEX ON BINDS (status) NOTUNIQUE;   -- most associative-layer reads filter on 'ratified'
-- Deliberately NO index on BINDS (created_at), none on BINDS (mode), and none on
-- BEGETS (created_at): all three are nullable, and nulling an indexed edge property leaves a
-- stale index entry on this build (§9). Expiry ages the queue in the application; mode is
-- filtered by equality, which is exact.
```

*Refinement:* a walk's trail (sketched in §3.3) is realized as `Op(VISIT)` entries and folded back
on read, so the run's state is reconstructible without a vertex to hold it. Through v0.8.2 a
narrower working set was realized the same way, as `Op(SET_WORKING)`; those entries still fold —
as the plain visits they always also were — but nothing writes them any more (§3.3).

*Hardening:* `Note.id`/`Note.created_at` are `READONLY` and `Note.id`/`Note.body`
`MANDATORY`+`NOTNULL` (append-only immutability, §4/§6). `Note.visited` stays writable — walk
recording is its only writer (§13). Neither `BINDS.created_at` nor `BEGETS.created_at` is
`READONLY`, so edges predating those properties can be dated retroactively — the first from the
`Op(SUGGEST_LINK)` log, the second from the child note (§12.3 *Migrations*). For `BINDS` that
repair path is load-bearing: an undated edge is never expired, so keeping it open is what keeps
such edges mortal (§7).

### 12.2 The four representational layers

The difference-engine (§8) mines mismatches across four layers, each with its own access path:

| Layer | Stored as | Access path |
|-------|-----------|-------------|
| Structural / generative | `BEGETS` edges (+`mode`) | typed traversal (O(1)/hop) |
| Associative | `BINDS` (`status='ratified'`) | typed traversal |
| Semantic | `Note.embedding` | `LSM_VECTOR` nearest-neighbor |
| Temporal | `Note.id` / `created_at` | range index |

A "provocation" is a query that finds notes close on one layer and far on another (§8.1).

### 12.3 The rewrite-rule grammar (concrete operations)

Every state change is one rule, run as a transaction that also appends an `Op` (the append-only
trail §11.2 referenced). Machine-permitted rules are marked ●; human-only ○ (this is the dial,
§11.5, as a permission model).

| Rule | Signature | Effect | Who |
|------|-----------|--------|-----|
| `ADD_NOTE` | `(parent_id?, mode, body, author)` | insert `Note(active)`; if `parent_id`, add `BEGETS{mode}`; embed; log (address is computed lazily, §4) | ○ (human commits) |
| `SUGGEST_LINK` | `(a, b, rationale, mode?)` | `BINDS{status:suggested, created_at, rationale}`. The machine may only propose **untyped** (§7) | ● |
| `RATIFY_LINK` | `(edge, mode?)` | set `ratified`, optionally assign `mode` | ○ |
| `RETYPE_LINK` | `(edge, mode)` | change an existing bind's `mode` | ○ |
| `REJECT_LINK` | `(edge)` | delete (proposal, not corpus) | either |
| `EXPIRE_LINKS` | `(max_age, keep_min)` | delete `suggested` links older than `max_age`; skip while the queue is `<= keep_min`; never touch undated or ratified edges (§7). One Op per sweep, not per edge | ● |
| `CORRECT_COSMETIC` | `(id, body)` | in-place `body` update **iff** embedding drift < ε; log | ○ (only in-place mutation) |
| `PROMOTE_TYPE` | `(pattern)` | register a new vertex/edge type (schema growth, §3.3) | ○ |
| `KNN_CACHE` | `(k)` | rebuild the materialized k-NN layer (§12.1 `KnnCache`); derived data only, the corpus is untouched | ● |

*Also logged, though lifecycle or maintenance rather than growth:*

| Group | Rules |
|-------|-------|
| Embedding (§7) | `EMBED` |
| Seeding (§7) | *(no rule of its own)* — `seed-binds.sh` replays an associative layer from a manifest after a rebuild by issuing a real `SUGGEST_LINK` + `RATIFY_LINK` per row, so nothing is back-dated and the log reads as though the links were made by hand |
| Scheduled jobs (§12.6) | `PROVOKE_DIGEST`, `RESURFACE`, `KNN_CACHE`, `EXPIRE_LINKS` |
| Walk lifecycle (§3.3, §11.1, §13) | `START_WALK`, `VISIT`, `PRODUCE`, `SAVE_WALK`, `FORK_WALK`, `DELETE_WALK` |
| Migrations | `BACKFILL_LINK_DATES`, `MIGRATE_V0_8_0` |

The invariant is that *every* state change appends an `Op`, so the log is the whole audit trail —
including the machine's own housekeeping. Its converse also holds since v0.8.0 and is the sharper
half: **anything that appends no `Op` changed no state.** The analytics layer (§13) appends
nothing at all, which is how "it only reads" is checked rather than asserted.

**Removed in v0.8.0**: `CRYSTALLIZE_CLUSTER`, `PROMOTE_HUB`, `RATIFY_CLUSTER`, `REJECT_CLUSTER`,
`DERIVE_CLUSTER`, `RECOMPUTE_CLUSTER`, `COMMUNITY_DETECT` (a cluster is no longer a thing to
create, ratify or refresh) and `DECAY`, `FITNESS_RECOMPUTE`, `CRITICALITY` (nothing stored to
decay, recompute or record). The `Trace` rules were renamed to their walk equivalents.

**Removed in v0.8.6**: `PROPOSE_VARIANT`, `COMMIT_VARIANT`, `REJECT_VARIANT`, and with them
`Note.status = 'proposed'` (§12.7 — the draft never lived in the graph, so there was nothing to
promote). Unlike `SET_WORKING` below, these leave nothing to keep reading: the log holds no
instance of any of them, and no note has ever carried the status.

**Removed in v0.8.3**: `SET_WORKING` (§3.3 — the trail is the working set; there is nothing left
to nominate). It is the first rule retired from a log that already holds instances of it, which
makes explicit what the append-only rule has always implied: **a retired rule is still read.**
Folding stops writing it and stops interpreting what it added, but must go on recognising it, or
the walks that used it would silently lose part of their trail — the same reasoning that makes
`DELETE_WALK` a tombstone rather than an erasure.

**Walks are the one place the log is the only record.** A walk has no vertex, so `START_WALK` and
its successors *are* the walk — folding them yields the trail, what it produced and whether it was
saved (§3.3). `DELETE_WALK` is therefore a **tombstone**: the log is
append-only, so deletion marks the walk as retired and returns the `visited` it counted, rather
than erasing history. A walk remains a *record of* the corpus rather than corpus itself (§2.4),
so retiring one is legitimate — it just cannot be unwritten.

### 12.4 Core queries

Move 1 (semantically near, graph-far) is in §8.3. Others:

```cypher
-- The catalysis relation: ONE relation read off both layers (§3.3).
MATCH (x:Note)-[:BEGETS]->(y:Note)                        RETURN x.id, y.id
UNION
MATCH (x:Note)-[b:BINDS]->(y:Note)
WHERE b.mode = 'catalyzes' AND b.status = 'ratified'      RETURN x.id, y.id;
```

```cypher
-- Autocatalysis for a DETECTED cluster: does the catalysis relation CYCLE inside it? (§3.3)
-- Induce the relation above on the members, then look for a non-trivial strongly connected
-- component; BEGETS is acyclic, so any cycle uses at least one ratified BINDS{catalyzes}, and
-- the minimal case is a reciprocal pair A ⇄ B.
//   members := label propagation over ratified BINDS ∪ BEGETS  (no Cluster vertex to join, v0.8.0)
//   ... induce catalysis on {members}, return the largest SCC of size >= 2 as the catalytic core
//   (implemented as an iterative Tarjan pass in the application — stdlib only, no graph library)
```

```cypher
-- AS OF a past instant (v0.8.0): every input is dated, so any of the above can be recomputed
-- against the graph as it stood then. This is what makes storing derived structure unnecessary.
MATCH (n:Note) WHERE n.created_at <= $t                                    // notes that existed
MATCH (x:Note)-[e:BEGETS]->(y:Note) WHERE e.created_at <= $t               // lineage drawn by then
MATCH (a:Note)-[b:BINDS]->(c:Note)  WHERE b.created_at <= $t               // binds staged by then
-- Caveat: BINDS.status carries no history, so an as-of view uses each surviving edge's CURRENT
-- status. Op(RATIFY_LINK) could recover the true history; v0.8.0 does not attempt it (§13).
```

### 12.5 The sign, and where it is read

*(This section described "two clocks" — a fast `activation` and a slow `fitness`, both stored on
the note — through v0.7.0. Both are gone; see §13 and the §11.3 discussion of why the deletion is
a correction rather than a loss.)*

The corpus has exactly one sign and it lives in exactly one place: `BINDS.mode`, on a **ratified**
edge. `catalyzes` is excitatory, `inhibits` is the correction signal (§6, §7), untyped is worth
nothing. Every reading of that sign — a note's fitness, whether a cluster is autocatalytic,
whether the corpus is at criticality — is computed from the edges when the question is asked
(§13), never accumulated onto notes in advance.

### 12.6 Maintenance loop (scheduled jobs)

| Job | Cadence | Reads → Action |
|-----|---------|----------------|
| embed-on-commit | event | new `Note` → write `embedding` |
| knn-cache | nightly (**first**) | materialize each embedded note's top-k neighbours → `KnnCache` (§12.1); skip when the embedded-note set is unchanged |
| provocation-digest | nightly (**second**) | run the six moves (§8.1) → `recent/provocations.md`, stage `SUGGEST_LINK` under the §8.2 cap. *Named `provocation-builder` and cadenced "on-demand (v1) / digest (v1.1)" until v0.8.5; the built job is the v1.1 one and is scheduled.* |
| resurfacing | weekly | surface orphan/under-linked/inhibited notes (§8.1 move 6) → `recent/resurface.md` |
| link-expiry | weekly | `EXPIRE_LINKS` — retire the unratified tail of the suggestion queue (§7) |

**Weekly is a period, not an hour** *(v0.8.5)*. `_due` gates a weekly job on a full week elapsed
**and** the hour being past 02:00 UTC, which excludes only midnight–02:00 — so a weekly job settles
on whatever time of day it first ran and holds it, rather than joining the nightly batch. The two
shipped weeklies sit mid-evening and mid-afternoon UTC for that reason. It matters for `link-expiry`,
which deletes edges: the sweep can land while its queue is being read.

**A run that changes nothing appends nothing** *(v0.8.5)*. `knn-cache --if-stale` on a fresh cache
and `link-expiry` with nothing stale both return without an `Op`, which is not an omission — it is
§13's rule (anything appending no `Op` changed no state) applying to the maintenance loop as it does
to the analytics layer. The consequence is worth stating: for those two jobs the append-only log is
**not** the record that they ran, and cannot be. `~/.indexia/scheduler.log` and `scheduler-state.json`
are, and both job scripts now say "no Op (nothing changed)" in the line they log, so a silent log is
distinguishable from a job that never fired.

**A failing job backs off** *(v0.8.5)*. Fail-open bounds the blast radius of one bad run, not of a
run that fails every time: the nightly cadence stays due from 02:00 until midnight, so an always-failing
nightly job is a twenty-two-hour retry window. A failure is recorded and the job waits — 15 min,
doubling, capped at 6 h — before the next attempt; one success clears the record. Per-job timeouts
(§12.6, `scheduler.JOBS`) are sized to catch a *hang* rather than to bound cost, because `knn-cache`
commits its whole pass in one transaction at the end and a kill at 99% discards all of it.

**Every job here writes** *(v0.8.0)*. Four that did not survive the split — `activation-decay`,
`fitness-recompute`, `community-detection` and `criticality-monitor` — existed only to keep
stored analytics from going stale, and with nothing stored there is nothing to refresh. Their
measurements moved to §13, where they are computed on demand and write nothing. The corpus is
still watched at the edge of chaos; the watching is done by whoever runs the report, and the loop
closes through them (§11.3).

**Ordering is load-bearing** *(v0.6.3)*. `knn-cache` runs first so it absorbs the single ANN rebuild
(§9) and the provocation digest reads the cache it just wrote rather than querying vectors per seed.
Any job about to touch the vector layer first **waits for the embed queue to drain**, so it queries a
corpus that has stopped moving. That wait is **fail-open**: after a bounded timeout the job logs the
pending count and proceeds anyway — a stalled embedder must degrade the loop's freshness, never stop it.
Measured effect: the six-move digest went from 36 minutes unfinished to seconds, and now issues *zero*
vector queries, so the state of the ANN index cannot affect it at all.

### 12.7 Assisted authoring ("make a note of this")

Source excerpt → LLM drafts an atomic candidate → human edits → `ADD_NOTE` with a parent and
`mode`, `author = llm_assisted`, `source_ref` naming where the excerpt came from. Curation stays
human (principle 3); the machine only drafts.

**The draft does not live in the graph** *(v0.8.6)*. This section specified a `PROPOSE_VARIANT`
rule writing `Note{status:proposed}` for a later `COMMIT_VARIANT` to promote, and it was built —
but nothing ever called it, in 2267 operations, because the human step it exists to serve happens
*before* anything is written: the assistant proposes a draft in conversation (the `add-note`
skill), or a file waits in `staging/` (the `ingest-staging` skill), and what reaches the graph is
already the approved note. A proposed *note* is therefore unlike a suggested *link*, which has to
be in the graph because a link is a claim about two notes that must exist first. Storing the draft
would reify a state the corpus never needs to be in — the same judgement §13 makes about fitness
and communities — so the three rules and the `proposed` status are removed. `Note.status` stays in
the schema, with `active` its only value; a note that turns out to be wrong is answered by a
correction plus a ratified `BINDS{inhibits}` (§6), never by a status.

### 12.8 Build order

1. Schema + `ADD_NOTE`/address derivation + embedding (a working append-only Zettelkasten).
2. `BINDS` + ratification flow + move 1 (the first provocation).
3. `BINDS{inhibits}` — the signed associative layer.
4. Walk recording + replay (the stored-program loop).
5. Community detection + autocatalysis (the living unit).
6. Maintenance jobs + digest.
7. The analytics split (§13): fitness, criticality, communities and autocatalysis computed on
   demand, `Note.visited`, and dated `BEGETS` for as-of reconstruction.

**All seven steps are built and live-validated** *(steps 1–6 at v0.6.3, re-validated against the
v0.7.0 edge model on a corpus rebuilt from scratch; step 7 at v0.8.0 against the same 100-note
corpus)*, along with the trailing v1 rules (`CORRECT_COSMETIC`, `PROMOTE_TYPE`). Two integration-
test rounds against a 101-note corpus followed, and everything they surfaced is either fixed or
recorded as a deliberate deferral — the v0.6.3 changelog lists what the second round promoted into
this spec. The provocation UI of §11.3 mod 6 followed at v0.8.1, first as a read-only spatial map
and then as one you can act on — it added no rule to §12.3, because a view that writes should be a
new caller of the ingestion path and not a new path.

v0.8.2 gave that view two inboxes and a status panel, and neither added a rule either, for two
different reasons worth recording. **Parking a file is not an operation.** A drop into `staging/`
or `staging/scans/` writes a *file*: no note exists, no vector is computed, nothing enters the graph, and
so there is nothing for an `Op` to record — ingestion is still the act that makes a note, and the
drop is one step earlier than the grammar begins. Recording it would put "a file appeared on the
filesystem" into the audit trail of what the corpus *is*, which is the same category error §13
forbids in the other direction. And the status panel is an **analytic** in the sense of §13
below: it reports the maintenance clock, the daemons and the measurements, and it may not drive
any of them.

v0.8.4 added the `link` panel on the same terms, and it is worth saying why a *human*-initiated
bind needs no new rule. `SUGGEST_LINK` is marked ● for machine-**permitted**, not machine-only;
a person calling it is `link.sh suggest`, which the grammar has always allowed. What the panel may
not do is collapse the two acts — there is no verb that creates a ratified bind, and
`LinkManager.ratify` refuses a pair with no edge between them — so it proposes, and the verdict
that appears afterwards is a second act on an edge that by then exists. The friction §3.3 asks for
was never the click; it is that a human has to *see* the relation, and searching two notes out is
that seeing. What remains is the §10 "still genuinely
open" list and the rest of the §11 aspirations (per-cluster/hub competency daemons, full
branching-parameter criticality), not unbuilt v1 scope.

Steps 3–5 read differently than they did: what was built there was the *stored* signed network,
the `Trace` vertex and the `Cluster` vertex, and step 7 removed all three while keeping every
capability they provided. That is worth stating rather than quietly rewriting history — the build
order records what was learned, and what was learned is that three of those six steps reified
things that should have stayed queries.

### 12.9 Pressure-test resolutions

| Issue | Resolution |
|-------|------------|
| "catalysis is a derived relation / three types" (stale v0.3) | Catalysis is one relation read off two edges: `BEGETS` ∪ ratified `BINDS{catalyzes}` (§3.3, v0.7.0). |
| `BEGETS` overloaded note↔note and cluster↔cluster | Cluster-level autocatalysis is **derived**, not a stored edge (§3.3). |
| §6 vs §10 on in-place edits | Reconciled: cosmetic in-place via `CORRECT_COSMETIC`; meaning-change via a new note + ratified `BINDS{inhibits}` (§6, §12.3). |
| append-only vs "rejection deletes it" | Append-only governs *ratified* content; proposals may be discarded (§2.4). |
| `fitness` vs `activation` undefined | Split into slow/fast stored variables (§12.5) — then **both removed** in v0.8.0: neither was read, and both were derivable. Fitness is a report (§13). |
| criticality: per-cluster vs corpus | Corpus-level is the setpoint, per-cluster is diagnostic — and neither is stored *(v0.8.0)*; both are reports (§13). |
| trace reactants not edges | Moot *(v0.8.0)*: `PRODUCED` is gone and working-set catalysis is deliberately not a fitness term (§3.3). |
| "cluster" (detected) vs `Cluster` (ratified) | Moot *(v0.8.0)*: there is only the detected sense — no `Cluster` node exists to ratify (§3.3). |
| variant notes had no staging | `status = proposed` mirrors `suggested` links (§3.1, §12.3). |
| move-1 "graph-far" ignored lineage | Predicate now excludes short `BINDS` **or** `BEGETS` paths (§8.1, §8.3). |
| `SUPERSEDES` was the one semantic edge, written at ingestion *(v0.7.0)* | Removed. Correction is a new note + a ratified `BINDS{inhibits}`; ingestion creates only `BEGETS` (§3.2, §6). |
| "closed under catalysis" was unsatisfiable while catalysis was parentage *(v0.7.0)* | Catalysis now spans both layers, so it can cycle; autocatalysis is that cycle, not a ratio (§3.3, §12.4). |
| the excitatory half of §12.5 was asserted from v0.3 but never built *(v0.7.0)* | Built as a signed wave — then removed in v0.8.0 along with the whole two-clock model: the sign is read off `BINDS.mode` where a question is asked, not accumulated onto notes (§12.5, §13). |
| grammar tokens undefined | Defined as the rule table (§12.3). |
| derived structure was stored as if primary *(v0.8.0)* | `Trace`, `Cluster`, `Note.fitness` and `Note.activation` removed. Everything they held is computed on demand from a fully dated graph (§13). |
| §11.3 claimed the loop *regulated* criticality; nothing ever acted on the reading *(v0.8.0)* | Restated as observation. The measurement is a report; the correction is the human's (§11.3, §12.6, §13). |

---

## 13. Analytics: observing the graph

*(v0.8.0.)* Indexia's activities divide in two, and the division is architectural rather than a
matter of taste.

**Operations write the graph.** Ingestion and embedding; the `BINDS` ratification flow;
suggestion expiry; the provocation digest; the k-NN cache; and walk recording. Each is a rewrite
rule and each appends an `Op` (§12.3).

**Analytics read it.** Fitness, corpus criticality, community detection, autocatalysis, walk
history and replay, attention statistics. These are conducted **over** the graph and never feed
down into it.

> **The rule: an analytic writes nothing.** Not a property, not an edge, not even an `Op`
> recording that it ran. An analytic is a question, and asking a question must not change the
> answer.

That last clause is not fussiness. The `Op` log is the audit trail of state change (§12.3), so
letting a report append to it would put "someone looked" into the record of what the corpus *is*
— and would re-establish exactly the coupling this section removes. Because the rule is absolute,
it is checkable: run every report, then compare the corpus counts and the `Op` count. Something
enforceable is worth more than something asserted.

### 13.1 Why nothing is stored

Every one of these was a stored field before v0.8.0, and each demonstrated the same failure mode.
A derived value has to be *maintained*: ratifying a single bind can change any note's fitness and
any cluster's autocatalysis, so a nightly job existed to rewrite them all, and between runs they
were wrong. The clearest evidence is that `Note.fitness` was **write-only** — a job recomputed it
for all 100 notes every night and no query in the system ever read it.

Computing on demand costs a graph scan, which at this corpus size is nothing, and buys three
things:

1. **Nothing goes stale.** There is no interval during which the number disagrees with the edges.
2. **Definitions become cheap.** A fitness weight is a constant in a report, not a migration and a
   recompute pass. Changing what "autocatalytic" means costs an edit.
3. **The past is reachable.** `Note`, `BEGETS` and `BINDS` all carry `created_at`, so any report
   takes an **as-of** instant and reconstructs the graph as it stood then. A cluster from last
   month is a query — which is the whole argument for not having stored it.

The corresponding cost is honest: at a much larger corpus a scan per report will stop being free,
and the answer then is a **materialized cache** — a `KnnCache`-shaped document type, rebuildable
and never traversable (§12.1) — not a property on `Note`. The distinction that matters is not
computed-vs-stored, it is whether the corpus *claims* the value as part of itself.

### 13.2 The one thing analytics cannot derive

`Note.visited` (§3.1) is a stored counter, and it is the exception that shows what the rule is
actually about. It counts human-directed **walks** through a note, and no amount of graph reading
recovers it: nothing else in the system records that a person read something. So it is written by
walk recording — an operation — and analytics only report on it.

Its counting rule follows from the same reasoning. It counts **walks, not visits**: returning to a
note three times in one sitting is one encounter. And **only walks move it** — no machine job,
not the digest, not resurfacing, not the k-NN rebuild, ever touches it. A number that both a
human and a daemon could increment would measure neither.

### 13.3 The reports

Driven by `scripts/analytics.sh`; `--as-of` where marked.

| Report | Answers | as-of |
|--------|---------|-------|
| `fitness` | a note's standing: `w·catalyzes − w·inhibits + w·descendants + w·visited`, floored (§12.5, §6) | ✓ |
| `criticality` | is the corpus sparse / critical / dense: mean ratified-`BINDS` degree against the band, orphan fraction, 2-hop avalanche size (§11.3) | ✓ |
| `communities` | label propagation over ratified `BINDS` ∪ `BEGETS`; each community's size, hub and diagnostics (§3.3) | ✓ |
| `autocatalysis` | which communities **cycle** under catalysis, and the members forming the cycle (§3.3, §12.4) | ✓ |
| `visited` | attention per note; ascending is the sharper question — what is held but never revisited | ✓ |
| `walks` / `walk` / `replay` | walks folded out of the `Op` log; replay re-seeds move 1 from a saved walk's trail (§11.1) | — |

Walk reports take no as-of: a walk is an `Op` sequence, and a replay is by definition a question
about the corpus *now*.

**The as-of caveat, stated plainly.** `BINDS.status` carries no history, so a past view uses each
surviving edge's *current* status: a bind ratified today counts as ratified in a view of last
month, and one rejected since is absent from that view entirely. Note membership and edge
existence are dated exactly. `Op(RATIFY_LINK)` could recover the true status history; v0.8.0 does
not attempt it, and every report that accepts `--as-of` says so.

### 13.4 Where the boundary sits

Community detection is the interesting case, because it runs on **both** sides. The difference-
engine's move 3 mines the boundaries between communities and the provocation digest seeds itself
from their hubs (§8.1) — so detection is on the operational path and lives in the operational
library. It qualifies because it *writes nothing*: it reads the graph and returns a grouping.

That is the real test, and it is narrower than "is this a metric". An operation may read anything.
What it may not do is write a conclusion back. The boundary is not between measuring and acting;
it is between **reading the graph** and **making the graph carry what you concluded**.
