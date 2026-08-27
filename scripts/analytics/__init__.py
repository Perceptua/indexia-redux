"""Indexia analytics — abstractions computed over the graph, never stored in it (spec §13).

Operations write the graph: ingestion, embedding, the BINDS ratification flow, suggestion
expiry, the provocation digest, the k-NN cache, and walk recording. Those are in scripts/ and
notelib.py.

Analytics read it: fitness, criticality, communities, autocatalysis, walk history and replay.
Those are here, and they write nothing at all — not a property, not an edge, not even an Op
saying a report ran. Enforced by tests/test_analytics_readonly.py.

The boundary is the point. A stored metric has to be maintained, and maintaining it means a job
that rewrites it whenever the graph moves — which is how Indexia ended up with a nightly pass
recomputing a fitness number nothing read. Computed on demand, a metric cannot go stale, costs
nothing to change, and can be asked of the graph as it stood last month (`--as-of`).
"""
