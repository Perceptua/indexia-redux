# Vendored assets

One file, pinned, checked in, and served from disk. The repo takes no package manager on either
side of the wire: there is no `pip install` for the Python and there is no `npm install` here.

| File | Version | SHA-256 | License |
|---|---|---|---|
| `cytoscape.min.js` | 3.34.0 | `9c2a3bf2592e0b14a1f7bec07c03a54f16dedf32af9cd0af155c716aa6c87bc3` | MIT |

Re-fetch and verify:

```bash
curl -sfL -o ui/vendor/cytoscape.min.js https://unpkg.com/cytoscape@3.34.0/dist/cytoscape.min.js
sha256sum ui/vendor/cytoscape.min.js
```

**Never load it from a CDN.** The whole system is loopback-only and must work with the network
off; a CDN fetch would also tell someone else's server that this machine is running Indexia.

Cytoscape has no transitive dependencies, so vendoring is one file and the guarantee that
`bash scripts/ui.sh start` works offline is preserved. It was chosen over a hand-rolled canvas
renderer because the parts it supplies — hit-testing under zoom, label culling
(`min-zoomed-font-size`), a force layout, and a selection/neighbourhood model — are exactly the
parts that make a hand-rolled graph feel like pyvis, and over a WebGL renderer (Sigma) because
at this corpus size WebGL buys nothing and would cost the two features this view is made of.

`ui/graph.js` is the only file that knows Cytoscape exists. If a filtered view ever routinely
exceeds ~2,000 visible nodes, or panning drops below ~30 fps, replacing that one file with
Sigma v3 + graphology + ForceAtlas2 in a worker is the planned move; the JSON contract already
speaks `source`/`target`, which both libraries take unchanged.
