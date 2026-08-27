"""Criticality — is the corpus at the edge of chaos? (spec §11.3, §13)

Between frozen order (too sparse, too hierarchical — no novelty) and chaos (too densely or
randomly linked — no stable structure). Measured by link density and by how far a perturbation
spreads before dying out.

**This observes; it does not regulate.** The spec used to describe the criticality monitor as
part of a homeostatic loop holding the corpus at a setpoint, but the job only ever logged its
reading — nothing acted on it. v0.8.0 makes that honest: the measurement is a report, and the
loop closes through the human, who reads it and decides whether to link more or write more. That
is the dial (§11.5) applied to the corpus's own metabolism — the machine measures, the human
disposes.

Moved verbatim in substance from notelib.criticality_report, reparameterized onto a Corpus so it
can be asked as of a past instant.
"""
MIN_DEGREE = 1.0    # target band for mean ratified-BINDS degree (§10)
MAX_DEGREE = 6.0
AVALANCHE_K = 3     # a perturbation exciting more than k notes is a large avalanche


def report(corpus, min_degree=MIN_DEGREE, max_degree=MAX_DEGREE, k=AVALANCHE_K):
    """Mean/median ratified-BINDS degree against the target band, the orphan set, and an
    avalanche proxy: how far a one-note perturbation spreads in 2 hops over the associative +
    structural graph. `band` is 'sparse' (too frozen), 'critical' (in band) or 'dense'."""
    ldeg = {}
    for a, b in corpus.ratified():
        ldeg[a] = ldeg.get(a, 0) + 1
        ldeg[b] = ldeg.get(b, 0) + 1
    note_ids = sorted(corpus.notes)
    degrees = sorted(ldeg.get(nid, 0) for nid in note_ids)
    n = len(degrees)
    mean_deg = sum(degrees) / n if n else 0.0
    median_deg = degrees[n // 2] if n else 0
    orphan_ids = [nid for nid in note_ids if ldeg.get(nid, 0) == 0]

    adj = corpus.adjacency()
    avalanche = 0
    if adj:
        seed = max(sorted(adj), key=lambda x: len(adj[x]))   # from the best-connected note
        seen, frontier = {seed}, {seed}
        for _ in range(2):
            nxt = set()
            for u in frontier:
                nxt |= adj[u] - seen
            seen |= nxt
            frontier = nxt
        avalanche = len(seen) - 1

    band = ("sparse" if mean_deg < min_degree else
            "dense" if mean_deg > max_degree else "critical")
    return {"notes": n, "mean_degree": round(mean_deg, 4), "median_degree": median_deg,
            "orphans": len(orphan_ids), "orphan_ids": orphan_ids[:10],
            "orphan_fraction": round(len(orphan_ids) / n, 4) if n else 0.0,
            "avalanche": avalanche, "avalanche_large": avalanche > k,
            "band": band, "target_degree": [min_degree, max_degree]}


ADVICE = {
    "sparse": "under-linked — ratify more binds, or let the digest propose more",
    "critical": "in band",
    "dense": "over-linked — the structure is getting hard to see; write hub notes",
}


def render(rep, as_of=None):
    """Human-readable summary."""
    lines = ["criticality" + (f"  ·  as of {as_of}" if as_of else "")]
    lines.append(f"  band          {rep['band']}  ({ADVICE[rep['band']]})")
    lines.append(f"  mean degree   {rep['mean_degree']}  "
                 f"(target {rep['target_degree'][0]}–{rep['target_degree'][1]})")
    lines.append(f"  median degree {rep['median_degree']}")
    lines.append(f"  notes         {rep['notes']}")
    lines.append(f"  orphans       {rep['orphans']}  ({rep['orphan_fraction']:.1%})")
    if rep["orphan_ids"]:
        lines.append("                " + ", ".join(rep["orphan_ids"]))
    lines.append(f"  avalanche     {rep['avalanche']} note(s) in 2 hops"
                 + ("  (large)" if rep["avalanche_large"] else ""))
    return "\n".join(lines)
