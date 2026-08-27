"""Walks — reading sessions reconstructed from the Op log, and replayed (spec §11.1, §13).

Recording a walk is an operation (notelib.WalkManager, `scripts/walk.sh`). Reading one back is
an analytic, and that is the whole split: the walk exists as its sequence of Ops plus the +1 it
left on each note's `visited`; everything you can *say* about it — the ordered trail, what it
produced, whether anything it touched has since been corrected — is derived.

Replay is the stored-program loop (§11.1): re-seed the difference-engine from a saved walk's
trail and see what the corpus offers now that it did not offer then. The stored graph is
first entelechy — dormant knowledge; a replay is second entelechy — the being-at-work that
actualizes it (§11.6). Before v0.8.0 replay could fork, i.e. write; it cannot now. Replay
proposes, the human disposes: `walk.sh fork <id>` is the operation that acts on it.
"""
from . import common

notelib = common.notelib


def show(db, walk_id):
    """One walk, or {}. Straight through to the reconstruction in notelib (the WalkManager
    writes against the same fold, so there is exactly one definition of what a walk is)."""
    return notelib.read_walk(db, walk_id)


def listing(db, status=None, limit=50):
    """Walks newest-first, optionally filtered by status ('active' | 'saved')."""
    return notelib.list_walks(db, status=status, limit=limit)


def visits(corpus, limit=None, ascending=False):
    """Attention per note: [{id, label, visited}], most-visited first by default.

    `--ascending` inverts it, which is the more useful question: the notes with the lowest
    visit counts are the ones the corpus holds but the reader never returns to.
    """
    out = [{"id": nid, "label": corpus.label(nid), "visited": note.get("visited") or 0}
           for nid, note in corpus.notes.items()]
    out.sort(key=lambda r: (r["visited"], r["id"]) if ascending
             else (-r["visited"], r["id"]))
    return out[:limit] if limit else out


def replay(db, walk_id, k=5, ef=100, depth=2, use_cache=True):
    """Re-run a SAVED walk against the corpus as it is now: re-seed move 1 from the walk's
    trail, flag candidates that appeared after the walk ended, and flag any note it visited that
    has since been inhibited (§6). Read-only.

    **The trail is the seed set** (v0.8.3). It used to be the working set — a subset the reader
    nominated by hand with `SET_WORKING` — with the walk's seed as a fallback when none was
    named. That was a declared judgement about which notes mattered, and it had the failure mode
    every optional declaration has: forget it, and a whole sitting replays from one note. The
    notes a run actually touched are the notes it held, they cost no extra gesture, and they
    cannot be forgotten. Nothing else about replay changed.

    The cost scales with the trail: one move-1 lookup per note, each of which is a k-NN cache
    read — cheap — but which falls through to a live vector query for any note the cache cannot
    answer for (see notelib.neighbors_of). A long trail is therefore more chances to pay for an
    ANN rebuild, which is why this is a CLI analytic and not something behind a button.

    Move 1 reads the nightly k-NN cache by default. Since a replay is precisely a question about
    what changed, note the blind spot: a candidate embedded *after* the last cache rebuild will
    not appear against a trail note cached before it. `use_cache=False` queries the vector index
    live for a definitive answer, at the cost of an ANN rebuild.
    """
    walk = notelib.read_walk(db, walk_id)
    if not walk:
        raise ValueError(f"no walk {walk_id} to replay")
    if walk.get("status") != "saved":
        raise ValueError(f"walk {walk_id} is not saved yet — only saved walks replay "
                         f"(walk.sh save {walk_id})")
    ended = walk.get("ended_at")
    trail = [v["note_id"] for v in walk["visited"] if v.get("note_id")]
    if not trail and walk.get("seed"):
        trail = [walk["seed"]]        # a walk always visits its seed, so this is belt-and-braces
    seen, provocations = set(), []
    for nid in trail:
        try:
            cands = notelib.move1_candidates(db, nid, k=k, ef=ef, depth=depth,
                                             use_cache=use_cache)
        except (ValueError, notelib.EmbedderError):
            continue                           # unembedded note / embedder off — skip it
        for c in cands:
            if c["id"] in seen:
                continue
            seen.add(c["id"])
            c["from"] = nid
            c["appeared_since"] = bool(
                ended and notelib._dt_digits(c["id"]) > notelib._dt_digits(ended))
            provocations.append(c)
    inhibited_ids = notelib.inhibited_note_ids(db)
    inhibited = [v["note_id"] for v in walk["visited"] if v["note_id"] in inhibited_ids]
    return {"walk_id": walk_id, "seeds": trail, "provocations": provocations,
            "inhibited_visited": inhibited}


# ---- rendering --------------------------------------------------------------
def render_list(walks):
    lines = [f"walks — {len(walks)}"]
    for w in walks:
        lines.append(f"  {w['id']}  [{w.get('status')}]  {len(w.get('visited', []))} visited"
                     f"  ·  {w.get('intent') or '(no intent)'}")
    if not walks:
        lines.append("  (none — start one with walk.sh start --seed <note-id>)")
    return "\n".join(lines)


def render_show(walk, corpus=None):
    if not walk:
        return "no such walk"
    lines = [f"walk {walk['id']}  [{walk.get('status')}]",
             f"  intent   {walk.get('intent') or '(none)'}",
             f"  seed     {walk.get('seed')}",
             f"  started  {walk.get('started_at')}"]
    if walk.get("ended_at"):
        lines.append(f"  ended    {walk['ended_at']}")
    if walk.get("forked_from"):
        lines.append(f"  forked from {walk['forked_from']}")
    lines.append(f"\n  trail ({len(walk.get('visited', []))} note(s)):")
    for v in walk.get("visited", []):
        label = f"  {corpus.label(v['note_id'])}" if corpus else ""
        lines.append(f"    {v['seq']:>3}  {v['note_id']}{label}")
    if walk.get("produced"):
        lines.append(f"\n  produced: {', '.join(walk['produced'])}")
    return "\n".join(lines)


def render_replay(result, corpus=None):
    lines = [f"replay of walk {result['walk_id']}",
             f"  re-seeded from {len(result['seeds'])} note(s) on the trail"]
    fresh = [p for p in result["provocations"] if p.get("appeared_since")]
    lines.append(f"\n  {len(result['provocations'])} provocation(s), "
                 f"{len(fresh)} new since the walk:")
    for p in result["provocations"]:
        mark = "NEW" if p.get("appeared_since") else "   "
        lines.append(f"    {mark}  {p['score']:.3f}  {p['id']}  {p.get('snippet') or ''}")
    if not result["provocations"]:
        lines.append("    (none — the trail may be unembedded, or nothing is near it)")
    if result["inhibited_visited"]:
        lines.append(f"\n  since corrected (ratified BINDS{{inhibits}} points at these):")
        for nid in result["inhibited_visited"]:
            label = f"  {corpus.label(nid)}" if corpus else ""
            lines.append(f"    {nid}{label}")
    return "\n".join(lines)


def render_visits(rows, as_of=None):
    lines = [f"visits — {len(rows)} note(s)" + (f"  ·  as of {as_of}" if as_of else "")]
    total = sum(r["visited"] for r in rows)
    unread = sum(1 for r in rows if not r["visited"])
    lines.append(f"  {total} walk-visit(s) recorded  ·  {unread} note(s) never walked")
    for r in rows:
        lines.append(f"  {r['visited']:>4}  {r['id']}  {r['label']}")
    return "\n".join(lines)
