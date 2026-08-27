"""Structural debt — what the corpus grew out of and the writer stopped attending to.

Move 7 of the generativity engine (spec §8.1; design in docs/indexia-prompt-assistant-spec.md).
The other six moves ask what is semantically near, temporally adjacent, or structurally between.
This one asks what is *load-bearing and neglected*, which nothing else in Indexia reads.

    debt(n) = descendants(n) / (1 + attention(n))

      descendants(n) = transitive BEGETS descendants of n
      attention(n)   = undirected ratified-BINDS degree of n  +  n.visited

**The two halves are measured on different relations, and that is the whole design.** Indexia's
two note->note edges cost completely different things:

  * `BEGETS` is free and automatic — written at note creation because you named a parent. No
    decision, no later act. So it records what the corpus OWES a note.
  * A ratified `BINDS` is expensive and human: a person looked at two notes and judged them
    related. `Note.visited` is the same fact from the other side — a person walked through it
    (§13.2). So together they record what the writer has GIVEN BACK.

Measure both halves on the link graph and they cancel: "important" would mean well-linked and
"neglected" would mean poorly-linked, and no note could be both. Splitting them across the two
relations is what makes the ratio mean something. It is also why betweenness was rejected for the
numerator — betweenness is a *function of links*, so it can never be high while a note is
under-linked, quite apart from needing Brandes and being meaningless on a small graph.

**It inverts `fitness` on purpose.** Fitness credits descendants and inbound catalyzes edges, so
the note most in need of writing scores as one of the healthiest things in the graph. Fitness
measures standing; this measures debt. They share their inputs and disagree about what they mean,
and reading them side by side is the point.

**No tunable weights.** The only constants are a floor and a cap, both below. There is nothing to
calibrate, which matters because there is not yet a corpus to calibrate against.

Like everything in this package it reads and writes nothing — no property, no edge, not even an
`Op` (§13). The provocation digest renders it nightly; `analytics.sh debt` asks on demand.
"""
MIN_DESCENDANTS = 3   # below this the ratio is noise rather than debt (a note with one child and
                      # no binds scores 1.0, which says nothing about neglect)
K = 5                 # candidates returned, matching the digest's other per-move caps


def score(descendants=0, binds=0, visited=0):
    """The ratio itself, as pure arithmetic — separated from the queries so the convention stays
    testable without a database (the pattern `fitness.score` uses).

    `+1` in the denominator so a wholly unattended note does not divide by zero, and so its debt
    is then simply its descendant count.

    Suggested (unratified) binds must never reach this. They are machine proposals with a 30-day
    life, and letting the digest's own output into the denominator would cancel the very debt it
    exists to reveal. `report` below reads `corpus.ratified()` for exactly that reason.
    """
    return descendants / (1.0 + binds + visited)


def _ancestors(parent, note_id):
    """Every id on the BEGETS path from `note_id` up to its root, excluding `note_id`.

    `parent` is {child: parent}, which is total because a note takes at most one parent — lineage
    is a forest, not a general DAG. The `seen` guard is defensive only: the shape rules that out,
    but a malformed restore should not spin forever (the same guard `_fz_depth_anchor` carries).
    """
    out, node, seen = [], note_id, {note_id}
    while node in parent:
        nxt = parent[node]
        if nxt in seen:
            break
        out.append(nxt)
        seen.add(nxt)
        node = nxt
    return out


def rank(rows, parent, min_descendants=MIN_DESCENDANTS, limit=K):
    """Score, floor, de-duplicate along lineage, and cap. Pure — no database.

    `rows` are dicts carrying at least {id, descendants, binds, visited}; whatever else they carry
    (label, title) is passed through. Returns them scored, richest debt first, ties broken by id
    so two runs over an unchanged graph agree.

    **The lineage de-duplication is the one constraint this metric genuinely needs.** Neglect is
    inherited: if a root with twelve descendants has never been bound, its child with eleven
    almost certainly has not either, and the whole list becomes one chain rendered five ways. So
    where two candidates lie on the same ancestor path, only the higher-scoring one survives.
    Selection runs best-first, and a candidate is dropped if an already-kept note is among its
    ancestors OR it is among an already-kept note's ancestors — both directions, because the
    better score can sit at either end of the chain.
    """
    scored = []
    for row in rows:
        descendants = row.get("descendants") or 0
        if descendants < min_descendants:
            continue
        binds, visited = row.get("binds") or 0, row.get("visited") or 0
        scored.append(dict(row, attention=binds + visited,
                           debt=round(score(descendants, binds, visited), 6)))
    scored.sort(key=lambda r: (-r["debt"], r["id"]))

    kept, kept_ancestors = [], []
    for cand in scored:
        ancestors = set(_ancestors(parent, cand["id"]))
        if any(k["id"] in ancestors or cand["id"] in anc
               for k, anc in zip(kept, kept_ancestors)):
            continue
        kept.append(cand)
        kept_ancestors.append(ancestors)
        if len(kept) >= limit:
            break
    return kept


def report(corpus, min_descendants=MIN_DESCENDANTS, limit=K):
    """The notes in most structural debt: [{id, label, descendants, binds, visited, attention,
    debt}], richest first. Reads nothing but the Corpus already in memory.

    `status='proposed'` notes are excluded — they are not yet corpus (spec §3.1). Nothing else is
    suppressed. In particular there is deliberately no high-degree cutoff: in Indexia a note that
    many notes link to *is* how a keyword exists (§10, "a keyword is just a note that many notes
    link to"), and those notes are already scored low by the denominator rather than needing to
    be excluded by hand.
    """
    descendants = corpus.all_descendant_counts()
    degree = {}
    for a, b in corpus.ratified():
        if a == b:
            continue
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    parent = {child: par for par, child in corpus.begets}

    rows = []
    for nid, note in corpus.notes.items():
        if note.get("status") == "proposed":
            continue
        rows.append({"id": nid, "label": corpus.label(nid),
                     "descendants": descendants.get(nid, 0),
                     "binds": degree.get(nid, 0),
                     "visited": note.get("visited") or 0})
    return rank(rows, parent, min_descendants=min_descendants, limit=limit)


def prompt(row):
    """The one rendered line for a candidate (spec §5 of the move-7 design).

    States the structural fact and stops. It never says what the note means, implies, or should
    argue — that is spec §8.2's boundary applied to a sentence: the machine proposes the site, the
    human writes. The debt score is deliberately NOT in the line; a displayed number is a number
    to move, and this metric is one the writer can move directly by ratifying binds.

    Two templates rather than one, because "nothing" and "twice" are different facts and a single
    "few" would lose the sharper of them. "Been back" covers both terms honestly — a ratified bind
    and a walk are both the writer returning to the note.
    """
    label, n = row["label"], row["descendants"]
    if not row["attention"]:
        return f'{n} notes descend from "{label}". You have not been back.'
    times = "once" if row["attention"] == 1 else f'{row["attention"]} times'
    return f'{n} notes descend from "{label}". You have been back {times}.'


def diagnosis(corpus, min_descendants=MIN_DESCENDANTS):
    """Why the list is empty — because "you owe nothing" and "this cannot be measured here" are
    different messages and only the second is a problem.

    v0.8.5 learned this on the digest's staging budget: a run that goes quiet has to name the
    limit that bound it, or it reads as a broken job. The same applies with more force here,
    because move 7 depends on a writing habit — notes committed with a `--continues`/`--branches`
    parent. Without one, `BEGETS` is a scatter of roots, every descendant count is zero, and the
    move is silently dead rather than merely quiet.
    """
    total = len(corpus.notes)
    if not total:
        return "the corpus is empty"
    with_parent = len({child for _par, child in corpus.begets})
    if not with_parent:
        return (f"no note has a BEGETS parent — lineage is {total} disconnected roots, so nothing "
                f"has descendants. Commit notes with --continues/--branches to give this "
                f"something to measure")
    share = with_parent / total
    if share < 0.25:
        return (f"only {with_parent} of {total} notes ({share:.0%}) have a parent — lineage is "
                f"too shallow to measure debt against yet")
    return (f"no note has {min_descendants}+ descendants you have not been back to — "
            f"nothing is owed")


def render(rows, corpus=None, as_of=None):
    """Human-readable table: the prompt per note, with the terms that produced it underneath."""
    lines = [f"structural debt — {len(rows)} note(s)" + (f"  ·  as of {as_of}" if as_of else "")]
    if not rows:
        lines.append("  (nothing" + (f" — {diagnosis(corpus)}" if corpus is not None else "") + ")")
        return "\n".join(lines)
    for row in rows:
        lines.append("")
        lines.append(f"  {prompt(row)}")
        lines.append(f"    {row['id']}  ·  debt {row['debt']:.2f}  ·  "
                     f"{row['descendants']} descendant(s), {row['binds']} bind(s), "
                     f"{row['visited']} walk(s)")
    return "\n".join(lines)
