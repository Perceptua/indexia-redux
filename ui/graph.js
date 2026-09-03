/* The renderer, and the only file that knows Cytoscape exists.
 *
 * Everything above it speaks the JSON contract from scripts/ui.py — nodes with `source`/`target`
 * edges — so swapping this file for a WebGL renderer is a rewrite of one module and nothing
 * else. See ui/vendor/README.md for when that becomes worth doing.
 *
 * The interface is eight functions:
 *   mount(el, snapshot, handlers)   build the graph, seed positions, lay it out
 *   setData(snapshot)               swap in a fresh corpus in place; never re-lays out
 *   setVisible(nodeIds, edgeIds)    show exactly these; never re-lays out
 *   setSelection({nodes, edges, focus})
 *   relayout(mode)                  're-layout' is a button, never a side effect
 *   fit()
 *   fitTo(ids)                      frame just these nodes, e.g. a community from the status panel
 *   positions() / setPositions(map) for persistence across reloads
 *
 * Two rules the rest of the app depends on:
 *   1. Filtering NEVER moves a node. Re-layout on interaction is what makes a graph feel like
 *      pyvis, and it destroys the spatial memory that makes a map worth having at all — which
 *      is the whole claim behind spec §11.3's "observer-centred spatial map".
 *   2. Selection dims the complement rather than highlighting the selection. Highlighting reads
 *      fine at a hundred nodes and disappears into the field at a thousand.
 */

/* Both modes are selected, not flipped. The three categorical hues are the first three slots of
 * the validated palette — the only three that clear the all-pairs colour-separation gates in
 * both modes, and a canvas of scattered nodes is an all-pairs case (any two communities can end
 * up adjacent). Communities past the third take the neutral, with the legend and the detail
 * panel carrying identity instead of hue. The light aqua sits below 3:1 on the light surface,
 * which obliges visible labels — they are on, and every selection names itself in the panel. */
const PALETTE = {
  light: {
    surface: '#fcfcfb', ink: '#0b0b0b', ink2: '#52514e', muted: '#898781',
    community: ['#2a78d6', '#eb6834', '#1baf7a'], other: '#898781',
    begets: '#898781', good: '#0ca30c', critical: '#d03b3b', pending: '#b8860b',
  },
  dark: {
    surface: '#1a1a19', ink: '#ffffff', ink2: '#c3c2b7', muted: '#898781',
    community: ['#3987e5', '#d95926', '#199e70'], other: '#898781',
    begets: '#898781', good: '#0ca30c', critical: '#d03b3b', pending: '#b8860b',
  },
};

const NODE_MIN = 12;         // px diameter at fitness 0 — still a comfortable click target
const NODE_SCALE = 7;        // px per sqrt(fitness): square-root, because fitness is unbounded
const NODE_MAX = 44;         // and linear sizing lets one hub swallow the viewport
const GHOST_SCALE = 0.62;    // out-of-window context notes: present, clearly not the subject
const TIMELINE_HEIGHT = 1600;

const dark = window.matchMedia('(prefers-color-scheme: dark)');
const pal = () => (dark.matches ? PALETTE.dark : PALETTE.light);

let cy = null;
let mode = 'force';

// ---- colour / size by data -------------------------------------------------
function nodeColor(el) {
  const c = el.data('community');
  const p = pal();
  return (c === null || c === undefined || c >= p.community.length) ? p.other : p.community[c];
}

function nodeSize(el) {
  return Math.min(NODE_MAX, NODE_MIN + NODE_SCALE * Math.sqrt(el.data('fitness') || 0))
    * (el.data('inWindow') ? 1 : GHOST_SCALE);
}

/* A bind reads as settled — green catalyzes, red inhibits — only once it carries one of those
 * two verdicts. Ratified-but-untyped ("related, no verdict either way", §7) gets the same
 * caution colour as anything still suggested: neither is a catalyzes/inhibits claim, so neither
 * should wear that claim's colour just because it might end up there. Line-style (below) is what
 * separates the two — this function is colour only. */
function pendingBind(el) {
  return el.data('type') === 'binds'
    && (el.data('status') !== 'ratified' || !el.data('mode'));
}

function edgeColor(el) {
  const p = pal();
  if (el.data('type') === 'begets') return p.begets;
  if (pendingBind(el)) return p.pending;
  return el.data('mode') === 'inhibits' ? p.critical : p.good;
}

/* Solid is "settled": a ratified bind, whatever its verdict, or a begets edge that continues the
 * line it is on. Dotted is "not yet, or not directly": a bind still waiting on a verdict — same
 * dotting regardless of what mode it might end up carrying — and a begets edge that branches
 * rather than continuing. Ratified-untyped is deliberately solid: the corpus already settled on
 * "related, no verdict either way" (§7), which is a decision, not a placeholder. */
function lineStyle(el) {
  if (el.data('type') === 'begets') return el.data('mode') === 'branches' ? 'dotted' : 'solid';
  return el.data('status') === 'ratified' ? 'solid' : 'dotted';
}

/* Direction is load-bearing on an inhibits bind — outV corrects inV (§6) — so the arrowhead
 * pairs shape with colour: a tee for inhibition (the standard notation), a triangle for
 * everything that feeds forward. That also keeps the green/red pair from being the only channel
 * carrying the distinction. */
function arrowShape(el) {
  return el.data('mode') === 'inhibits' ? 'tee' : 'triangle';
}

function stylesheet() {
  const p = pal();
  return [
    {
      selector: 'node',
      style: {
        'background-color': nodeColor,
        'width': nodeSize,
        'height': nodeSize,
        'border-width': 2,                 // a surface ring, so overlapping marks stay countable
        'border-color': (el) => (el.data('inhibits') > 0 ? p.critical : p.surface),
        'label': 'data(label)',
        'font-size': 9,
        'min-zoomed-font-size': 9,         // the single biggest frame-time lever in a labelled graph
        'text-wrap': 'ellipsis',
        'text-max-width': 110,
        'text-valign': 'bottom',
        'text-margin-y': 3,
        'color': p.ink2,
        // No style transitions anywhere in this sheet: Cytoscape animates them against
        // function-valued styles, and a mapper that re-evaluates mid-transition lands on a
        // stale value. Filtering and selection must be exact, not pretty.
        'opacity': (el) => (el.data('inWindow') ? 1 : 0.4),
        'text-opacity': (el) => (el.data('inWindow') ? 1 : 0),
      },
    },
    {
      selector: 'edge',
      style: {
        'curve-style': 'bezier',
        'line-color': edgeColor,
        'target-arrow-color': edgeColor,
        'target-arrow-shape': arrowShape,
        'arrow-scale': 0.75,
        'width': (el) => (el.data('type') === 'begets' ? 1.2 : 2),
        'line-style': lineStyle,
        'opacity': (el) => (el.data('status') === 'suggested' ? 0.5 : 0.75),
      },
    },
    // Selection: the complement recedes, and its labels go with it.
    { selector: '.dim', style: { 'opacity': 0.12, 'text-opacity': 0, 'z-index': 0 } },
    { selector: 'node.sel', style: { 'opacity': 1, 'text-opacity': 1, 'z-index': 20 } },
    { selector: 'edge.sel', style: { 'opacity': 1, 'width': 3, 'z-index': 20 } },
    /* The one or two notes actually clicked, as against the neighbourhood they lit up. Declared
       before .focus so that with two picked, both wear the ring and the one under focus is still
       the bolder of them. Which is A and which is B is NOT carried here on purpose: a ring cannot
       say "corrects", and the panel spells the direction out in a sentence for the same reason
       claimOf exists (ui/app.js). */
    { selector: 'node.pick', style: { 'border-width': 3, 'border-color': p.ink, 'z-index': 25 } },
    {
      selector: 'node.focus',
      style: { 'border-width': 3, 'border-color': p.ink, 'font-weight': 'bold', 'z-index': 30 },
    },
    { selector: 'node:active', style: { 'overlay-opacity': 0.08, 'overlay-color': p.ink } },
    /* Labels off, declared LAST so it beats .sel and .focus — with labels off, a selection must
     * not bring its own back. `label: ''` rather than a zero opacity: it skips the text pass
     * outright, which is where a labelled graph actually spends its frame. */
    { selector: 'node.nolabel', style: { 'label': '' } },
  ];
}

// ---- deterministic seeding -------------------------------------------------
/* cose starts from wherever the nodes already are. Seeding from a hash of the note id (rather
 * than Math.random) means the same corpus lays out the same way on every reload and on any
 * machine — the precondition for a position meaning anything. */
function seedPosition(id) {
  let h = 2166136261;
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  const a = ((h >>> 0) % 3600) / 3600 * Math.PI * 2;
  const r = 60 + ((h >>> 12) % 700);
  return { x: Math.cos(a) * r, y: Math.sin(a) * r };
}

/* An element's identity versus its readings. id/source/target ARE the element and cannot move;
 * everything else is something the server recomputed and a redraw may legitimately change —
 * fitness, community and the window flag all shift when a single edge is drawn anywhere in the
 * corpus, which is exactly what happens when you ratify one from the panel. */
const nodeState = (n) => ({
  label: n.label, fitness: n.fitness, community: n.community,
  status: n.status, inhibits: n.inhibits, inWindow: n.in_window, t: n.t,
});
const edgeState = (e) => ({ type: e.type, mode: e.mode, status: e.status });

const nodeEl = (n) => ({
  group: 'nodes', data: { id: n.id, ...nodeState(n) }, position: seedPosition(n.id),
});
const edgeEl = (e) => ({
  group: 'edges', data: { id: e.id, source: e.source, target: e.target, ...edgeState(e) },
});

function coseOptions() {
  return {
    name: 'cose',
    animate: false,
    randomize: false,                    // start from the seeded positions, above
    fit: true,
    padding: 48,
    nodeOverlap: 14,
    nodeRepulsion: 9000,
    gravity: 0.3,
    numIter: 1200,
    /* Association pulls harder than lineage. BEGETS is ubiquitous and mostly says "later"; a
     * ratified BINDS is the expensive, human-made claim, and it is the structure you came to
     * look at. A suggested one is only a proposal, so it pulls least. */
    idealEdgeLength: (e) => {
      if (e.data('type') === 'begets') return 100;
      return e.data('status') === 'suggested' ? 140 : 60;
    },
  };
}

/* Timeline: keep cose's x, replace y with the note's rank in time. Rank, not the raw instant —
 * a corpus written in bursts would otherwise pile every burst onto one line. Lineage then reads
 * top-to-bottom and the BEGETS DAG cannot cross itself temporally. */
function applyTimeline() {
  const dated = cy.nodes().filter((n) => n.data('t') !== null && n.data('t') !== undefined);
  const ordered = dated.sort((a, b) => a.data('t') - b.data('t'));
  const span = Math.max(1, ordered.length - 1);
  ordered.forEach((n, i) => {
    n.position('y', (i / span) * TIMELINE_HEIGHT - TIMELINE_HEIGHT / 2);
  });
}

// ---- the interface ---------------------------------------------------------
export const Graph = {
  mount(el, snap, handlers) {
    cy = cytoscape({
      container: el,
      elements: [...snap.nodes.map(nodeEl), ...snap.edges.map(edgeEl)],
      style: stylesheet(),
      minZoom: 0.08,
      maxZoom: 4,
      selectionType: 'single',
      boxSelectionEnabled: false,
      autounselectify: true,        // selection is ours to manage, not Cytoscape's
    });

    const additive = (ev) => !!(ev.originalEvent && ev.originalEvent.shiftKey);
    cy.on('tap', 'node', (ev) => handlers.onNode(ev.target.id(), additive(ev)));
    cy.on('tap', 'edge', (ev) => handlers.onEdge(ev.target.id(), additive(ev)));
    cy.on('tap', (ev) => { if (ev.target === cy) handlers.onBackground(); });
    if (handlers.onDrag) cy.on('dragfree', 'node', handlers.onDrag);

    dark.addEventListener('change', () => cy.style(stylesheet()));
    return cy;
  },

  /* Swap in a fresh corpus, in place. Returns how many notes are new.
   *
   * The contents change; the layout does not. Everything that survives keeps the position it
   * has — including one you dragged it to — and anything new arrives at its deterministic seed.
   * Re-laying out here would undo the entire argument for dragging (rule 1 at the top of this
   * file) in the most annoying way available: commit one note, and every place you had learned
   * moves. Survivors also get their data refreshed, because ratifying a single bind changes
   * fitness and can redraw community membership right across the graph.
   */
  setData(snap) {
    const nodes = new Map(snap.nodes.map((n) => [n.id, n]));
    const edges = new Map(snap.edges.map((e) => [e.id, e]));
    let added = 0;
    cy.batch(() => {
      cy.edges().forEach((e) => { if (!edges.has(e.id())) cy.remove(e); });
      cy.nodes().forEach((n) => { if (!nodes.has(n.id())) cy.remove(n); });
      cy.nodes().forEach((n) => {
        n.data(nodeState(nodes.get(n.id())));
        nodes.delete(n.id());
      });
      cy.edges().forEach((e) => {
        e.data(edgeState(edges.get(e.id())));
        edges.delete(e.id());
      });
      // Nodes before edges: an edge cannot be added to an endpoint that is not there yet.
      for (const n of nodes.values()) { cy.add(nodeEl(n)); added++; }
      for (const e of edges.values()) cy.add(edgeEl(e));
    });
    return added;
  },

  /* Which notes are the subject and which are only context. The snapshot ships the server's
   * verdict for its own default window; the moment the reader moves the window, this is what
   * keeps ghosting honest — otherwise a context note would render at full strength, labelled,
   * claiming to be part of the view it is only propping up. */
  setWindow(inWindowIds) {
    cy.batch(() => {
      cy.nodes().forEach((n) => n.data('inWindow', inWindowIds.has(n.id())));
    });
  },

  /* Labels on or off for the whole graph. Independent of everything else: ghosts stay unlabelled
   * either way, and a selection still dims the complement — this only decides whether the graph
   * is legible up close or readable as shape at a glance. */
  setLabels(on) {
    cy.batch(() => {
      if (on) cy.nodes().removeClass('nolabel');
      else cy.nodes().addClass('nolabel');
    });
  },

  /* Show exactly these. Positions are untouched: filtering is a visibility question and moving
   * the survivors would answer a different one. */
  setVisible(nodeIds, edgeIds) {
    cy.batch(() => {
      cy.nodes().forEach((n) => n.style('display', nodeIds.has(n.id()) ? 'element' : 'none'));
      cy.edges().forEach((e) => e.style('display', edgeIds.has(e.id()) ? 'element' : 'none'));
    });
  },

  setSelection(sel) {
    const any = sel && (sel.nodes.size || sel.edges.size);
    cy.batch(() => {
      cy.elements().removeClass('sel focus dim pick');
      if (!any) return;
      cy.nodes().forEach((n) => n.addClass(sel.nodes.has(n.id()) ? 'sel' : 'dim'));
      cy.edges().forEach((e) => e.addClass(sel.edges.has(e.id()) ? 'sel' : 'dim'));
      // Before .focus, so a node that is both keeps the class list in the order the stylesheet
      // resolves them — one of two picks reads as picked, the active one reads as both.
      if (sel.picks) sel.picks.forEach((id) => cy.getElementById(id).addClass('pick'));
      if (sel.focus) cy.getElementById(sel.focus).addClass('focus');
    });
  },

  relayout(next) {
    if (next) mode = next;
    cy.layout(coseOptions()).run();
    if (mode === 'timeline') {
      applyTimeline();
      cy.fit(cy.nodes(':visible'), 48);
    }
  },

  fit() {
    const vis = cy.nodes(':visible');
    cy.animate({ fit: { eles: vis.length ? vis : cy.nodes(), padding: 48 } }, { duration: 180 });
  },

  /* Tell Cytoscape its container changed size. cy's own autoResize only listens for the window
   * resizing; a grid-track change (the panel opening, narrow mode taking over the layout) resizes
   * #cy without resizing the window, and cy's canvas keeps stale pixel dimensions until told
   * otherwise — no re-layout, no fit(), positions must not move because of this. */
  resize() {
    if (cy) cy.resize();
  },

  /* Frame a specific set of nodes regardless of `display` — a community from the status panel is
   * corpus-wide (spec §13.2) and can easily span outside the current window, so this animates to
   * wherever the members actually are rather than only what `fit()`'s :visible would catch. */
  fitTo(ids) {
    const eles = cy.nodes().filter((n) => ids.has(n.id()));
    if (eles.length) cy.animate({ fit: { eles, padding: 48 } }, { duration: 220 });
  },

  centerOn(id) {
    const n = cy.getElementById(id);
    if (n.length) cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 0.9) },
                             { duration: 220 });
  },

  positions() {
    const out = {};
    cy.nodes().forEach((n) => { const p = n.position(); out[n.id()] = { x: p.x, y: p.y }; });
    return out;
  },

  setPositions(map) {
    let hits = 0;
    cy.batch(() => {
      cy.nodes().forEach((n) => {
        const p = map[n.id()];
        if (p && typeof p.x === 'number' && typeof p.y === 'number') { n.position(p); hits++; }
      });
    });
    return hits;
  },
};
