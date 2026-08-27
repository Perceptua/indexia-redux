#!/usr/bin/env python3
"""Walk recording: the Op log round-trips, and `visited` counts walks — not visits. (spec §13)

The v0.8.0 claim under test is that a walk needs no vertex. It is recorded as a sequence of Ops
plus one increment per note, and everything you can say about it — the ordered trail, what it
produced, whether it was saved — comes back out of that log unchanged.

The counting rule is the subtle half and the one most likely to regress: **once per walk per
note**. Visiting the same note three times in a sitting is one encounter, so the counter must
move once; and `DELETE_WALK` must give back exactly what the walk took.

Two things changed in v0.8.3 and are pinned here. The **working set is gone**: there is one role,
the trail no longer states one, and `replay` re-seeds from the trail itself. And **every walk gets
an intent** — derived from the seed when none is given, because no rule in the grammar can amend a
`START_WALK` payload, so a walk that starts anonymous stays anonymous for good.

Runs against the live corpus (there is no fixture DB), so it walks real notes and cleans up after
itself: every walk it starts is deleted, which restores `visited` to what it was. `corpus_guard`
catches vertex/edge drift; the visited totals are asserted directly, since no count would change
if the decrement were wrong.
"""
import lib

notelib = lib.notelib
check = lib.check
db = notelib.Arcade()


def visited_of(note_id):
    return notelib.first_row(db.query("SELECT visited FROM Note WHERE id = :n",
                                      {"n": note_id})).get("visited") or 0


ids = [r["id"] for r in notelib.rows(db.query(
    "SELECT id FROM Note WHERE status = 'active' ORDER BY id LIMIT 3")) if r.get("id")]
if len(ids) < 3:
    lib.skip("walk recording", f"needs 3 active notes, found {len(ids)}")
    lib.report_and_exit()

seed, second, third = ids
before = {nid: visited_of(nid) for nid in ids}
wm = notelib.WalkManager(db)
walk_id = None
started_walks = []           # every walk id this file opens, for the finally below
seed_label = (notelib.first_row(db.query("SELECT title, body FROM Note WHERE id = :n",
                                         {"n": seed})).get("title") or "").strip()

with lib.corpus_guard(db):
    try:
        started = wm.start("indexia-tests: synthetic walk", seed)
        walk_id = started.walk_id
        started_walks.append(walk_id)
        check("start returns a walk id that is its own Op id",
              walk_id == started.op_id and notelib.validate_id(walk_id) == walk_id)
        check("the seed is counted on start", visited_of(seed) == before[seed] + 1)

        wm.visit(walk_id, second)
        check("a first visit counts", visited_of(second) == before[second] + 1)

        wm.visit(walk_id, second)
        wm.visit(walk_id, second)
        check("re-visiting the same note in one walk does NOT count again",
              visited_of(second) == before[second] + 1, f"visited={visited_of(second)}")

        wm.visit(walk_id, third)
        check("a third note joins the trail and counts",
              visited_of(third) == before[third] + 1)

        # There is one role since v0.8.3, so the trail no longer states one. What replaced the
        # working set is the trail itself — see notelib.WALK_RULES and analytics.walks.replay.
        check("a trail entry is a note and its place in the order, and nothing else",
              all(set(v) == {"note_id", "seq"}
                  for v in notelib.read_walk(db, walk_id)["visited"]),
              str(notelib.read_walk(db, walk_id)["visited"][0]))
        check("`set_working` is gone from the writer entirely",
              not hasattr(wm, "set_working"))

        # -- reconstruction --
        walk = notelib.read_walk(db, walk_id)
        check("the walk reconstructs from the Op log", bool(walk))
        check("the intent survives the round trip",
              walk.get("intent") == "indexia-tests: synthetic walk")
        check("the seed survives the round trip", walk.get("seed") == seed)
        check("it is active until saved", walk.get("status") == "active")

        trail = walk["visited"]
        check("each note appears exactly once in the trail, however often visited",
              [v["note_id"] for v in trail] == [seed, second, third],
              str([v["note_id"] for v in trail]))
        check("seq records first-appearance order", [v["seq"] for v in trail] == [0, 1, 2])

        wm.produce(walk_id, third)
        check("produce is recorded", notelib.read_walk(db, walk_id)["produced"] == [third])
        check("producing a note is not visiting it",
              visited_of(third) == before[third] + 1)

        wm.save(walk_id)
        saved = notelib.read_walk(db, walk_id)
        check("save closes the walk",
              saved.get("status") == "saved" and saved.get("ended_at"))

        check("the walk appears in the listing",
              walk_id in [w["id"] for w in notelib.list_walks(db, status="saved")])

        # -- every walk is named (v0.8.3) --
        # There is no rule that amends a START_WALK payload, so a walk with no intent is
        # anonymous for the rest of its life and `analytics.sh walks` can only ever list it as
        # a timestamp. A derived default is not the stated goal §11.3 means; it is a name.
        anon_id = wm.start(None, seed).walk_id
        started_walks.append(anon_id)
        named = notelib.read_walk(db, anon_id).get("intent") or ""
        check("a walk started with no intent is given one from its seed",
              bool(named) and seed in [v["note_id"]
                                       for v in notelib.read_walk(db, anon_id)["visited"]],
              repr(named))
        check("...and the default names the seed rather than repeating its id",
              (seed_label and seed_label in named) or seed in named, repr(named))
        check("an explicit intent is never overwritten by the default",
              notelib.read_walk(db, walk_id).get("intent") == "indexia-tests: synthetic walk")
        blank_id = wm.start("   ", seed).walk_id
        started_walks.append(blank_id)
        check("a whitespace-only intent counts as none given",
              (notelib.read_walk(db, blank_id).get("intent") or "") == named)

    finally:
        # Every walk this file opened, by id. Never a sweep over `list_walks` — the tests run
        # against the live corpus, and a filter loose enough to catch a walk of ours is loose
        # enough to delete one of yours.
        for wid in started_walks:
            if wid:
                wm.delete(wid)

check("a deleted walk stops reconstructing (tombstone honoured)",
      notelib.read_walk(db, walk_id) == {})
check("a deleted walk leaves the listing",
      walk_id not in [w["id"] for w in notelib.list_walks(db)])
after = {nid: visited_of(nid) for nid in ids}
check("delete gives back exactly the visits the walk counted", after == before,
      f"{before} -> {after}")

lib.report_and_exit()
