"""Fitness — a note's standing in the corpus, computed on demand (spec §13).

Until v0.8.0 this was `Note.fitness`, a stored float rewritten nightly for every note. It was
also write-only: nothing in the system ever read it. That is the clearest possible sign it was
never a property of the note — it was a *summary of the note's neighbourhood*, and the
neighbourhood was already in the graph.

So it is a report now. The weights below are ordinary module constants: changing one changes the
next report, with no migration and no recompute pass, because nothing was stored to become
wrong.

    fitness = w_cat·(inbound ratified BINDS{catalyzes})
            − w_inh·(inbound ratified BINDS{inhibits})
            + w_desc·(BEGETS descendants)
            + w_vis·(visited)                                floored at FLOOR

Three of the four terms are purely relational; the fourth is the human-attention counter. An
UNTYPED ratified bind contributes exactly 0 — recognition without a verdict is not a verdict
(§7). The descendants term is purely positive: lineage records what a note produced, never a
judgement on it (§3.2). The floor is what keeps every note above the resurfacing threshold —
nothing truly dies (§6).

Dropped in v0.8.0: the saved-Trace-appearance and PRODUCED-spawn terms, which needed the Trace
vertex. `visited` covers the first directly and better (it counts walks, not edges). The second
is recoverable from Op(PRODUCE) but is deliberately left out — fitness should be computable from
the graph alone, and a note is not fitter for having been written during a walk.
"""
W_CATALYZES = 1.0   # each inbound ratified BINDS{catalyzes}  (+)
W_INHIBITS = 1.0    # each inbound ratified BINDS{inhibits}   (−)
W_DESCENDANTS = 1.0 # each transitive BEGETS descendant — reproductive success
W_VISITED = 1.0     # each human-directed walk through the note (§13)
FLOOR = 0.0         # nothing sinks below this (§6, §10)


def score(catalyzes=0, inhibits=0, descendants=0, visited=0,
          w_catalyzes=W_CATALYZES, w_inhibits=W_INHIBITS,
          w_descendants=W_DESCENDANTS, w_visited=W_VISITED, floor=FLOOR):
    """The formula itself, as pure arithmetic — separated from the queries so the sign
    convention stays testable without a database."""
    return max(floor,
               w_catalyzes * catalyzes
               - w_inhibits * inhibits
               + w_descendants * descendants
               + w_visited * visited)


def report(corpus, limit=None, **weights):
    """Fitness for every note, highest first: [{id, label, fitness, catalyzes, inhibits,
    descendants, visited}]. Reads nothing but the Corpus already in memory."""
    catalyzed = corpus.in_degree("catalyzes")
    inhibited = corpus.in_degree("inhibits")
    descendants = corpus.all_descendant_counts()
    out = []
    for nid, note in corpus.notes.items():
        terms = {"catalyzes": catalyzed.get(nid, 0),
                 "inhibits": inhibited.get(nid, 0),
                 "descendants": descendants.get(nid, 0),
                 "visited": note.get("visited") or 0}
        out.append(dict(terms, id=nid, label=corpus.label(nid),
                        fitness=round(score(**terms, **weights), 6)))
    out.sort(key=lambda r: (-r["fitness"], r["id"]))
    return out[:limit] if limit else out


def render(rows, as_of=None):
    """Human-readable table."""
    lines = [f"fitness — {len(rows)} note(s)" + (f"  ·  as of {as_of}" if as_of else "")]
    lines.append(f"  {'fitness':>8}  {'+cat':>4} {'-inh':>4} {'desc':>4} {'vis':>4}  note")
    for r in rows:
        lines.append(f"  {r['fitness']:>8.2f}  {r['catalyzes']:>4} {r['inhibits']:>4} "
                     f"{r['descendants']:>4} {r['visited']:>4}  {r['id']}  {r['label']}")
    return "\n".join(lines)
