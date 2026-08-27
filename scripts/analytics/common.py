"""Shared read primitives for the analytics layer (spec §13).

Everything in this package obeys one rule: **it reads the graph and never writes to it.** No
INSERT, no UPDATE, no DELETE, not even an Op recording that a report ran. An analytic is a
question, and asking a question must not change the answer. tests/test_analytics_readonly.py
enforces this by snapshotting the corpus and the Op count around every report.

That rule is why these live outside notelib: notelib is the operational library, and anything
importing it may write. The dependency runs one way — analytics imports notelib, never the
reverse.

The other thing this module provides is `as_of`: because Note, BEGETS and BINDS all carry
created_at (BEGETS since v0.8.0), any report can be computed against the graph as it stood at a
past instant. That is what makes storing derived structure unnecessary — a cluster from last
month is a query, not a record you had to keep.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notelib  # noqa: E402  (import needs the path above)


class Corpus:
    """The graph as the analytics layer sees it, optionally as of a past instant.

    Holds the note set and the two edge lists, read once and reused across the metrics in a
    single report — a report that asks five questions should not scan BINDS five times.

    `as_of` is a note-id-shaped timestamp (spec §4). Notes created after it, and edges drawn
    after it, are excluded. One honest limit: BINDS.status has no history, so an as-of view uses
    each surviving edge's CURRENT status. A bind ratified today but suggested last month counts
    as ratified in a view of last month, and one rejected since is absent from it entirely.
    Op(RATIFY_LINK) could recover the true history; v0.8.0 does not attempt it, and every report
    that accepts --as-of says so.
    """

    def __init__(self, db, as_of=None):
        self.db = db
        self.as_of = notelib.validate_id(as_of) if as_of else None
        self._cutoff = notelib._dt_digits(as_of) if as_of else None
        self.notes = self._read_notes()
        self.ids = set(self.notes)
        self.begets = self._read_edges("SELECT outV().id AS a, inV().id AS b, created_at "
                                       "FROM BEGETS")
        self.binds = self._read_binds()

    # -- reads --
    def _read_notes(self):
        out = {}
        for r in notelib.rows(self.db.query(
                "SELECT id, title, status, body, created_at, visited FROM Note")):
            nid = r.get("id")
            if not nid or (self._cutoff and notelib._dt_digits(nid) > self._cutoff):
                continue
            out[nid] = r
        return out

    def _read_edges(self, sql):
        """(a, b) pairs whose endpoints both survive the cutoff, and which were drawn before it.

        An edge with a NULL created_at predates the property. Those are kept in the present-day
        view (the graph plainly has them) but dropped from an as-of view: an undatable edge
        cannot honestly be placed in the past. The v0.8.0 migration dates every BEGETS edge and
        backfill-link-dates dates the BINDS, so this only bites on an un-migrated restore.
        """
        out = []
        for r in notelib.rows(self.db.query(sql)):
            a, b = r.get("a"), r.get("b")
            if not (a and b) or a not in self.ids or b not in self.ids:
                continue
            if self._cutoff:
                drawn = r.get("created_at")
                if not drawn or notelib._dt_digits(drawn) > self._cutoff:
                    continue
            out.append((a, b))
        return out

    def _read_binds(self):
        """[(a, b, mode, status)] surviving the cutoff — the associative layer with its sign."""
        out = []
        for r in notelib.rows(self.db.query(
                "SELECT outV().id AS a, inV().id AS b, mode, status, created_at FROM BINDS")):
            a, b = r.get("a"), r.get("b")
            if not (a and b) or a not in self.ids or b not in self.ids:
                continue
            if self._cutoff:
                drawn = r.get("created_at")
                if not drawn or notelib._dt_digits(drawn) > self._cutoff:
                    continue
            out.append((a, b, r.get("mode"), r.get("status")))
        return out

    # -- derived views --
    def ratified(self, mode=None):
        """(a, b) for ratified binds, optionally of one mode."""
        return [(a, b) for a, b, m, s in self.binds
                if s == "ratified" and (mode is None or m == mode)]

    def adjacency(self):
        """Undirected {id: set(neighbours)} over ratified BINDS + BEGETS — the graph community
        detection and the criticality measure both run on (§12.6)."""
        adj = {}
        for a, b in list(self.ratified()) + list(self.begets):
            if a != b:
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
        return adj

    def catalysis_edges(self):
        """Directed (x, y) where x catalyzes y: BEGETS ∪ ratified BINDS{catalyzes} (spec §3.3).

        ONE relation read off two layers. BEGETS is catalysis within a line of descent and is
        free — writing the note asserts it. A ratified BINDS{catalyzes} is catalysis across a
        generational gap and is expensive — a human had to see it. Before v0.7.0 catalysis was
        parentage alone, hence acyclic, hence "closed under catalysis" was unsatisfiable; joining
        the layers is what gives autocatalysis something to compute.
        """
        return list(self.begets) + list(self.ratified("catalyzes"))

    def inhibited_ids(self):
        """Notes a ratified BINDS{inhibits} points AT (§6) — being corrected, derived not
        stamped."""
        return {b for _a, b in self.ratified("inhibits")}

    def descendants(self, note_id):
        """Transitive BEGETS descendants of a note (excludes self). Walked over the in-memory
        edge list rather than TRAVERSE, so it honours --as-of and costs one scan for the whole
        report instead of a query per note."""
        children = {}
        for a, b in self.begets:
            children.setdefault(a, []).append(b)
        return self._reach(children, note_id)

    def all_descendant_counts(self):
        """{id: number of transitive BEGETS descendants} for every note, in one pass."""
        children = {}
        for a, b in self.begets:
            children.setdefault(a, []).append(b)
        return {nid: len(self._reach(children, nid)) for nid in self.notes}

    @staticmethod
    def _reach(children, start):
        seen, frontier = set(), [start]
        while frontier:
            nxt = []
            for node in frontier:
                for child in children.get(node, ()):
                    if child not in seen and child != start:
                        seen.add(child)
                        nxt.append(child)
            frontier = nxt
        return seen

    def in_degree(self, mode):
        """{id: count} of inbound ratified binds of one mode."""
        counts = {}
        for _a, b in self.ratified(mode):
            counts[b] = counts.get(b, 0) + 1
        return counts

    def label(self, note_id):
        """A short human label for a note: its title, or a snippet of its body."""
        note = self.notes.get(note_id) or {}
        return note.get("title") or notelib.snippet(note.get("body"), 60)


def detect_communities(corpus, min_size=None):
    """Communities by synchronous label propagation over `corpus.adjacency()`.

    A re-implementation of notelib.detect_communities against an in-memory Corpus rather than
    the live DB, so it can run as-of a past instant. Same algorithm, same determinism (nodes in
    id order, ties broken by lowest label), so the two agree on the present-day graph — which
    tests/test_analytics_metrics.py checks.
    """
    if min_size is None:
        min_size = notelib.COMMUNITY_MIN_SIZE
    adj = corpus.adjacency()
    if not adj:
        return []
    label = {nid: nid for nid in adj}
    order = sorted(adj)
    for _ in range(notelib.LABELPROP_MAX_ITERS):
        changed = False
        for nid in order:
            if not adj[nid]:
                continue
            counts = {}
            for m in adj[nid]:
                counts[label[m]] = counts.get(label[m], 0) + 1
            top = max(counts.values())
            winner = min(lab for lab, c in counts.items() if c == top)
            if winner != label[nid]:
                label[nid] = winner
                changed = True
        if not changed:
            break
    groups = {}
    for nid, lab in label.items():
        groups.setdefault(lab, []).append(nid)
    return sorted((sorted(g) for g in groups.values() if len(g) >= min_size))


def hub_of(corpus, members):
    """The hub of a community: its highest-degree member, ties broken by id.

    Replaces the HUB_OF edge a human used to draw. Structural centrality is not the same claim
    as "this is the note that names the theme" — it is a guess — but it is the guess the digest
    needs for a seed, and it can never be stale.
    """
    adj = corpus.adjacency()
    members = [m for m in members if m in corpus.ids]
    if not members:
        return None
    return max(sorted(members), key=lambda m: len(adj.get(m, ())))
