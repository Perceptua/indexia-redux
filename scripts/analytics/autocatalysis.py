"""Communities and autocatalysis — the "living unit", as a query (spec §3.3, §12.4, §13).

A cluster used to be a `Cluster` vertex: machine-detected, human-ratified, carrying four stored
diagnostics that a nightly job rewrote because ratifying a single bind could invalidate any of
them. Since v0.8.0 there is no vertex. A community is what label propagation finds in the graph
right now, and its diagnostics are read off the induced catalysis relation at the moment you ask.

The definitions are unchanged — they were always structural facts about edges, which is exactly
why storing them was redundant:

  * A set is **autocatalytic** when the catalysis relation restricted to it contains a CYCLE.
    Because BEGETS is a DAG, every such cycle must use at least one ratified BINDS{catalyzes} —
    lineage alone can never close. The minimal case is a note binding back to something upstream
    of itself: a conclusion feeding the premise that produced it. It is a yes/no structural fact,
    not a threshold, so it needs no calibration (§3.3).
  * **reproduction rate** = intra-community catalysis edges per day across the members' span.
  * **criticality** = mean internal degree; local and diagnostic (the corpus-level band lives in
    criticality.py).

All three are descriptive. Nothing gates on them, and nothing ever did.
"""
from . import common


def autocatalytic_core(edges, members):
    """Largest non-trivial strongly connected component of `edges` induced on `members`, i.e.
    the members forming the catalytic cycle. [] when acyclic.

    Iterative Tarjan — stdlib only, and iterative so a deep lineage cannot trip the recursion
    limit."""
    members = set(members)
    adj = {m: [] for m in members}
    for a, b in edges:
        if a in members and b in members and a != b:
            adj[a].append(b)

    index, low, on_stack, stack, counter, best = {}, {}, set(), [], [0], []
    for root in sorted(members):
        if root in index:
            continue
        work = [(root, iter(sorted(adj[root])))]
        index[root] = low[root] = counter[0]; counter[0] += 1
        stack.append(root); on_stack.add(root)
        while work:
            node, it = work[-1]
            nxt = next(it, None)
            if nxt is None:
                work.pop()
                if low[node] == index[node]:            # node roots an SCC
                    comp = []
                    while True:
                        w = stack.pop(); on_stack.discard(w); comp.append(w)
                        if w == node:
                            break
                    if len(comp) > len(best):
                        best = sorted(comp)
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
            elif nxt not in index:
                index[nxt] = low[nxt] = counter[0]; counter[0] += 1
                stack.append(nxt); on_stack.add(nxt)
                work.append((nxt, iter(sorted(adj[nxt]))))
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
    return best if len(best) > 1 else []


def reproduction_rate(corpus, members, edges=None):
    """New members produced per day: intra-community catalysis edges over the members' creation
    span in days (floored at 1). A v1 proxy — descriptive only."""
    members = set(members)
    if len(members) < 2:
        return 0.0
    pairs = corpus.catalysis_edges() if edges is None else edges
    intra = sum(1 for a, b in pairs if a in members and b in members)
    times = sorted(common.notelib.id_to_dt(m) for m in members)
    span_days = max((times[-1] - times[0]).total_seconds() / 86400.0, 1.0)
    return round(intra / span_days, 6)


def local_criticality(corpus, members):
    """Mean internal degree — the average number of intra-community neighbours (ratified BINDS +
    BEGETS) per member. Local and diagnostic; the regulated band is corpus-level."""
    members = set(members)
    if not members:
        return 0.0
    internal = sum(1 for a, b in list(corpus.ratified()) + list(corpus.begets)
                   if a in members and b in members)
    return round(2.0 * internal / len(members), 6)      # each edge touches two members


def report(corpus, min_size=None):
    """Every detected community with its hub and diagnostics, largest first."""
    edges = corpus.catalysis_edges()
    out = []
    for members in common.detect_communities(corpus, min_size=min_size):
        core = autocatalytic_core(edges, members)
        out.append({
            "members": members,
            "size": len(members),
            "hub": common.hub_of(corpus, members),
            "autocatalytic": bool(core),
            "autocatalytic_core": core,
            "reproduction_rate": reproduction_rate(corpus, members, edges=edges),
            "criticality": local_criticality(corpus, members),
        })
    out.sort(key=lambda c: (-c["size"], c["members"][0]))
    return out


def render(clusters, corpus, as_of=None, members=False, detected=None):
    """Human-readable summary. `members` lists every member, not just the hub.

    `detected` is how many communities were found *before* any filtering — the `autocatalysis`
    report shows only the ones that cycle, and reporting that count as "detected" would understate
    the corpus (1 of 9 read as "1 detected")."""
    n_auto = sum(1 for c in clusters if c["autocatalytic"])
    total = len(clusters) if detected is None else detected
    shown = ("" if detected is None or detected == len(clusters)
             else f", showing {len(clusters)}")
    lines = [f"communities — {total} detected, {n_auto} autocatalytic{shown}"
             + (f"  ·  as of {as_of}" if as_of else "")]
    if not clusters:
        lines.append("  (none — a community needs at least "
                     f"{common.notelib.COMMUNITY_MIN_SIZE} linked notes)"
                     if not total else
                     "  (none of them cycles under catalysis — normal, not a defect: the groups "
                     "hang together associatively, but nobody has said any part FEEDS another)")
    for i, c in enumerate(clusters, 1):
        mark = "◆ autocatalytic" if c["autocatalytic"] else "◇ not autocatalytic"
        lines.append(f"\n  [{i}] {c['size']} notes  ·  {mark}  ·  "
                     f"repro {c['reproduction_rate']}  ·  criticality {c['criticality']}")
        if c["hub"]:
            lines.append(f"      hub    {c['hub']}  {corpus.label(c['hub'])}")
        if c["autocatalytic_core"]:
            lines.append(f"      cycle  {', '.join(c['autocatalytic_core'])}")
        if members:
            for m in c["members"]:
                lines.append(f"      ·      {m}  {corpus.label(m)}")
    return "\n".join(lines)
