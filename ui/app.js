/* The view: filters, selection, and the detail panel.
 *
 * Reads the whole corpus once from /api/graph and does every filter in the browser. At this
 * scale that is not a close call — the payload is tens of kilobytes and a refilter is a class
 * toggle. When the corpus outgrows one payload the window moves server-side; scripts/ui.py
 * already accepts since/until for exactly that day.
 *
 * Nothing in THIS file writes. Every affordance that does lives in ui/write.js and arrives here
 * as an HTML fragment plus one call to Write.bind — the same division scripts/notelib.py draws
 * between reading the graph and mutating it, so there is one place to look for either.
 */
import { Graph } from '/ui/graph.js';
import { Link } from '/ui/link.js';
import { Search } from '/ui/search.js';
import { Status } from '/ui/status.js';
import { Write } from '/ui/write.js';

const POSITIONS_KEY = 'indexia.positions.v1';
const LABELS_KEY = 'indexia.labels.v1';
const FILTERS_KEY = 'indexia.filters.v1';         // {open, width} — a view preference, like labels
const PANEL_WIDTH_KEY = 'indexia.panelwidth.v1';  // width only: open/closed is never persisted
const FILTERS_DEFAULT = 232;
const FILTERS_MIN = 180;
const FILTERS_MAX = 480;
const PANEL_DEFAULT = 380;
const PANEL_MIN = 320;
const PANEL_MAX = 640;
const CY_MIN = 240;   // the graph itself never yields past this, however far a pane is dragged
/* Below this, PANEL_MIN (320) already exceeds CY_MIN's own headroom on a typical phone width —
 * the two constants above were never meant to coexist with the graph on the same screen. Narrow
 * mode does not resize around that; it takes over the layout so only one of {filters, graph,
 * panel} is ever asking for space at once. */
const NARROW_QUERY = '(max-width: 700px)';
/* The payload shape this page knows how to read (scripts/ui.py CONTRACT_VERSION). Checked
 * because the page is long-lived — a tab left open across an upgrade would otherwise go on
 * filtering a shape that has moved, and now also POSTing against it. */
const CONTRACT = 2;
const CONTEXT_CAP = 500;      // out-of-window notes pulled in for context before we stop and say so
const DAY_MS = 86400000;

let snap = null;
const byId = new Map();          // note id  -> snapshot node
const edgeById = new Map();      // edge id  -> snapshot edge
const incident = new Map();      // note id  -> [edge, …]

let visible = { nodes: new Set(), edges: new Set() };
let selection = { nodes: new Set(), edges: new Set(), focus: null, kind: null };
/* The notes the reader has picked, in the order they were picked, and there are two slots because
 * two is what a bind takes. Ordered rather than a Set: `from` is outV and `to` is inV, and on an
 * `inhibits` bind the direction IS the claim (§6), so which one is first is information.
 *
 * This is the state that did not exist before. Shift+click used to union whole neighbourhoods and
 * keep no record of what had been clicked, so "the two notes I mean" was unaskable — the selection
 * knew what was lit up and nothing about why. */
let picks = [null, null];
let lastPick = null;      // the slot filled most recently: the note under focus, and what a walk records
let days = '182';
let labelsOn = true;
let syncMasters = [];    // one no-arg fn per master checkbox — see wireMaster
let filtersOpen = true;
let filtersWidth = FILTERS_DEFAULT;
let panelWidth = PANEL_DEFAULT;

const $ = (sel) => document.querySelector(sel);
const panel = $('#panel');
const main = $('main');
const filtersEl = $('#filters');
const filtersHandle = $('#filters-resizer');
const panelHandle = $('#panel-resizer');
const cyEl = $('#cy');

const narrowMQ = window.matchMedia(NARROW_QUERY);
const isNarrow = () => narrowMQ.matches;

const esc = (s) => String(s === null || s === undefined ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

const labelOf = (id) => (byId.get(id) || {}).label || id;

// ---- boot ------------------------------------------------------------------
/* The three lookups every other function reads, rebuilt from a payload. Kept apart from boot()
 * because a write re-reads the corpus and has to rebuild all of them — a stale `incident` map
 * would show you the note you just linked with none of its links. */
function index(next) {
  snap = next;
  byId.clear();
  edgeById.clear();
  incident.clear();
  for (const n of snap.nodes) byId.set(n.id, n);
  for (const e of snap.edges) {
    edgeById.set(e.id, e);
    for (const end of [e.source, e.target]) {
      if (!incident.has(end)) incident.set(end, []);
      incident.get(end).push(e);
    }
  }
  const c = snap.counts;
  $('#corpus').textContent = snap.version !== CONTRACT
    ? `the server speaks payload v${snap.version} and this page reads v${CONTRACT} — reload`
    : `${c.notes} notes · ${c.begets} begets · ${c.binds} binds · ${snap.corpus.band}`
      + ` (mean degree ${snap.corpus.mean_degree}) · ${snap.corpus.communities} communities`;
}

/* Re-read the corpus and redraw in place, after something has written to it.
 *
 * `refresh=1` busts the server's minute-long snapshot cache: the whole point is to see what you
 * just did, and a stale payload would read as the write having been ignored. Graph.setData keeps
 * every position that survived, so the map does not rearrange itself under you. */
async function refresh() {
  const res = await fetch('/api/graph?refresh=1');
  if (!res.ok) throw new Error('could not re-read the graph');
  index(await res.json());
  buildLegend();
  Graph.setData(snap);
  const saved = loadPositions();
  if (saved) Graph.setPositions(saved);
  recompute();
}

async function boot() {
  const res = await fetch('/api/graph');
  if (!res.ok) {
    $('#corpus').textContent = 'could not load the graph — is the database up?';
    return;
  }
  index(await res.json());

  const context = {
    esc, labelOf, refresh, openPanel, selectNode, highlightEdgeBetween, highlightCommunity,
    highlightNote,
    centerOn: (id) => Graph.centerOn(id),
    toast: Write.toast,
    // The link panel's half of the selection: it names the slots, this file owns what is in them.
    pick, swapPicks, reselect, bindBetween, begetsBetween, claimOf,
    // Borrowed from the write surface rather than reimplemented, so the ratify buttons the link
    // panel shows are the SAME markup the queue and the edge panel show, wired the same way.
    suggestButton: Write.suggestButton,
    bindActions: Write.bindActions,
    bindWrites: (root) => Write.bind(root),
    openLink: () => Link.open(),
  };
  await Write.init(context);
  // The same helpers, and no await: none of these asks the server anything until it is opened,
  // and the graph should not wait on a panel nobody has looked at yet.
  Status.init(context);
  Search.init(context);
  Link.init(context);

  buildLegend();
  wire();

  Graph.mount($('#cy'), snap, {
    onNode: selectNode,
    onEdge: selectEdge,
    onBackground: clearSelection,
    onDrag: savePositions,
  });

  const saved = loadPositions();
  const restored = saved ? Graph.setPositions(saved) : 0;
  Graph.relayout('force');
  if (restored) Graph.setPositions(saved);   // cose moved everything; put the dragged ones back

  setLabels(readLabels());
  recompute();
  fromHash();
}

/* The legend is also the community filter. It is always present: with more than one series,
 * identity must never rest on colour alone — and only the first three communities carry a hue
 * at all (see ui/graph.js), so for the rest this list *is* the identity channel. */
function buildLegend() {
  // Rebuilt whenever the corpus is re-read, so it has to carry its own state across: a note
  // committed from the panel can create a community or empty one, and silently re-checking a
  // box the reader had unticked would change the view they are looking at without being asked.
  const wasOff = new Set([...document.querySelectorAll('#legend input')]
    .filter((i) => !i.checked).map((i) => i.dataset.community));
  const counts = new Map();
  for (const n of snap.nodes) {
    const key = n.community === null || n.community === undefined ? 'none' : String(n.community);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const rows = snap.communities.map((com) => {
    const key = String(com.index);
    const swatch = com.index < 3 ? `key c${com.index}` : 'key';
    const mark = com.autocatalytic ? '<span class="cy" title="cycles under catalysis">◆</span>' : '';
    return `<label class="row" title="hub: ${esc(com.hub_label || '')}">
      <input type="checkbox" data-community="${key}" ${wasOff.has(key) ? '' : 'checked'}>
      <span class="${swatch}"></span> ${com.index + 1} ${mark}
      <span class="size">${counts.get(key) || 0}</span></label>`;
  });
  rows.push(`<label class="row">
    <input type="checkbox" data-community="none" ${wasOff.has('none') ? '' : 'checked'}>
    <span class="key"></span> ungrouped
    <span class="size">${counts.get('none') || 0}</span></label>`);
  $('#legend').innerHTML = rows.join('');
  // These inputs are replaced wholesale, so they are wired here rather than in wire(), which
  // runs once and would leave a rebuilt legend inert.
  $('#legend').querySelectorAll('input')
    .forEach((el) => el.addEventListener('change', recompute));
}

/* A master checkbox (begets, binds, the "all" row above the community legend) is not itself an
 * extra filter — it is a stand-in for everything in its sub-list. A click cascades its new state
 * onto every sub, exactly like the request: "when the overall check is toggled, all items
 * underneath should be selected/deselected." The returned sync fn is the other half — called
 * after every recompute so the master's own tick mark (checked, or a dash for a mixed sub-set)
 * never drifts from what the subs actually say, instead of freezing at whatever it last was.
 * `subsOf` is re-run rather than captured once, because the legend's checkboxes are replaced
 * wholesale on every buildLegend(). */
function wireMaster(master, subsOf) {
  master.addEventListener('change', () => {
    subsOf().forEach((i) => { i.checked = master.checked; });
    recompute();
  });
  return () => {
    const subs = subsOf();
    const on = subs.filter((i) => i.checked).length;
    master.checked = on > 0;
    master.indeterminate = on > 0 && on < subs.length;
  };
}

function wire() {
  loadPaneState();
  applyFiltersWidth();
  applyPanelWidth();

  // Crossing the breakpoint mid-session (a rotated phone, a resized window) has to redrive both
  // appliers, not just the CSS — the panel's narrow width is computed from live layout
  // (applyPanelWidth), not a media query, so nothing here happens on its own. `resize` rather
  // than narrowMQ's own `change` event: verified against this page that a devtools/CDP viewport
  // override does not reliably fire MediaQueryList's `change` on every transition, even though
  // `.matches` itself reads correctly the moment something else asks — `resize` is the one signal
  // guaranteed to fire on every viewport-dimension change, including a real device rotation.
  window.addEventListener('resize', () => {
    applyFiltersWidth();
    applyPanelWidth();
  });

  // A grid-track resize (opening/closing the panel, crossing the breakpoint) resizes #cy's
  // container without resizing the window, and Cytoscape's own autoResize only listens for the
  // latter — leaving its canvas at stale pixel dimensions. cy.resize() only, never fit() or a
  // re-layout: this must not disturb the positions the reader has dragged into place.
  new ResizeObserver(() => Graph.resize()).observe(cyEl);

  // Narrow mode only: a second tap on the panel button that is already open closes it, since the
  // panel is the whole screen there and the small `×` is otherwise the only way out. Desktop
  // keeps today's open-only behavior — a capturing listener here runs before each panel module's
  // own bubbling click handler, so it can short-circuit before that handler reopens the same
  // panel with a fresh render.
  $('#bar').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-panel-source]');
    if (!btn || !isNarrow()) return;
    if (btn.getAttribute('aria-pressed') === 'true') {
      e.stopPropagation();
      clearSelection();
    }
  }, true);

  $('#togglefilters').addEventListener('click', () => {
    filtersOpen = !filtersOpen;
    applyFiltersWidth();
    saveFiltersState();
  });
  wireResizer(filtersHandle, {
    sign: 1, min: FILTERS_MIN, max: FILTERS_MAX, def: FILTERS_DEFAULT,
    get: () => filtersWidth,
    set: (w) => { filtersWidth = w; applyFiltersWidth(); },
    persist: saveFiltersState,
  });
  wireResizer(panelHandle, {
    sign: -1, min: PANEL_MIN, max: PANEL_MAX, def: PANEL_DEFAULT,
    get: () => panelWidth,
    set: (w) => { panelWidth = w; applyPanelWidth(); },
    persist: savePanelWidth,
  });

  // The legend and the masters (wireMaster, below) wire their own inputs, which is why both are
  // excluded here — bound in more than one place, a single toggle would recompute twice.
  document.querySelectorAll('#filters input:not([data-community]):not([data-master]), #filters select')
    .forEach((el) => el.addEventListener('change', recompute));

  syncMasters = [
    wireMaster($('[data-edge="begets"]'), () => [...document.querySelectorAll('[data-begets-mode]')]),
    wireMaster($('[data-edge="binds"]'),
      () => [...document.querySelectorAll('[data-bind-mode], [data-bind-status]')]),
    wireMaster($('#communities-all'), () => [...document.querySelectorAll('[data-community]')]),
  ];

  document.querySelectorAll('[data-days]').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('[data-days]').forEach((o) => o.classList.remove('on'));
    b.classList.add('on');
    days = b.dataset.days;
    recompute();
  }));

  document.querySelectorAll('[data-layout]').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('[data-layout]').forEach((o) => o.classList.remove('on'));
    b.classList.add('on');
    Graph.relayout(b.dataset.layout);
    savePositions();
  }));

  $('#labels').addEventListener('click', () => setLabels(!labelsOn));

  $('#relayout').addEventListener('click', () => { Graph.relayout(); savePositions(); });
  $('#fit').addEventListener('click', () => Graph.fit());
  $('#resetpos').addEventListener('click', () => {
    localStorage.removeItem(POSITIONS_KEY);
    Graph.relayout();
  });

  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') clearSelection(); });
  window.addEventListener('hashchange', fromHash);
}

// ---- filters ---------------------------------------------------------------
function readFilters() {
  const on = (attr, val) => {
    const el = document.querySelector(`[data-${attr}="${val}"]`);
    return !el || el.checked;
  };
  const set = (attr, vals) => new Set(vals.filter((v) => on(attr, v)));
  const communities = new Set(
    [...document.querySelectorAll('[data-community]')]
      .filter((i) => i.checked).map((i) => i.dataset.community));
  return {
    strict: $('#strict').checked,
    hideIsolated: $('#hideisolated').checked,
    orphans: $('#orphans').value,
    begets: on('edge', 'begets'),
    binds: on('edge', 'binds'),
    begetsModes: set('begets-mode', ['continues', 'branches']),
    bindModes: set('bind-mode', ['catalyzes', 'inhibits', 'untyped']),
    bindStatus: set('bind-status', ['ratified', 'suggested']),
    communities,
  };
}

/* Midnight UTC of the day `days` back — the same bound scripts/ui.py computes, so the client's
 * window and the payload's `in_window` never disagree at the default. */
function windowLow() {
  if (days === 'all') return null;
  const d = new Date(Date.now() - Number(days) * DAY_MS);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
}

function passesNode(n, f) {
  if (f.orphans === 'hide' && n.orphan) return false;
  if (f.orphans === 'only' && !n.orphan) return false;
  const key = n.community === null || n.community === undefined ? 'none' : String(n.community);
  return f.communities.has(key);
}

function passesEdge(e, f) {
  if (e.type === 'begets') return f.begets && (!e.mode || f.begetsModes.has(e.mode));
  return f.binds
    && f.bindModes.has(e.mode || 'untyped')
    && f.bindStatus.has(e.status || 'ratified');
}

/* Nodes are the primary filter target. An edge is visible iff its own filters admit it AND both
 * endpoints are visible; a node is never hidden merely because its edges are (except by the
 * explicit "hide isolated" toggle).
 *
 * The window then gets one concession, and it is a structural one rather than a nicety: BEGETS
 * points backwards in time, so a recency window amputates ancestry — every note in it whose
 * parent is older would read as a root with no origin, and "where did this come from" is the
 * question lineage exists to answer. So the direct out-of-window neighbours come along as
 * ghosts. Edges between two ghosts are dropped, or the closure cascades and the window stops
 * meaning anything. "strict" turns the whole concession off.
 */
function recompute() {
  const f = readFilters();
  const lo = windowLow();

  const eligible = new Set();
  const inWindow = new Set();
  for (const n of snap.nodes) {
    if (!passesNode(n, f)) continue;
    eligible.add(n.id);
    if (lo === null || (n.t !== null && n.t >= lo)) inWindow.add(n.id);
  }

  const admitted = snap.edges.filter(
    (e) => passesEdge(e, f) && eligible.has(e.source) && eligible.has(e.target));

  let context = [];
  if (!f.strict) {
    const ctx = new Set();
    for (const e of admitted) {
      const a = inWindow.has(e.source);
      const b = inWindow.has(e.target);
      if (a && !b) ctx.add(e.target);
      else if (b && !a) ctx.add(e.source);
    }
    context = [...ctx].sort((x, y) => (byId.get(y).t || 0) - (byId.get(x).t || 0));
  }
  const hidden = Math.max(0, context.length - CONTEXT_CAP);
  context = context.slice(0, CONTEXT_CAP);

  const nodes = new Set([...inWindow, ...context]);
  const edges = new Set();
  for (const e of admitted) {
    if (!nodes.has(e.source) || !nodes.has(e.target)) continue;
    if (!inWindow.has(e.source) && !inWindow.has(e.target)) continue;
    edges.add(e.id);
  }

  if (f.hideIsolated) {
    const touched = new Set();
    for (const id of edges) {
      touched.add(edgeById.get(id).source);
      touched.add(edgeById.get(id).target);
    }
    for (const id of [...nodes]) if (!touched.has(id)) nodes.delete(id);
  }

  visible = { nodes, edges };
  Graph.setWindow(inWindow);
  Graph.setVisible(nodes, edges);

  const parts = [`showing ${inWindow.size} of ${snap.counts.notes} notes`];
  if (context.length) parts.push(`+ ${context.length} context`);
  if (hidden) parts.push(`(${hidden} more context hidden)`);
  parts.push(`· ${edges.size} edges`);
  $('#windowhint').textContent = parts.join(' ');

  applySelection();
  syncMasters.forEach((sync) => sync());
}

// ---- selection -------------------------------------------------------------
/* Neighbours are one hop over the edges that are ACTUALLY ON SCREEN. Selecting a neighbour
 * reachable only through a hidden edge would be inexplicable — the selection has to agree with
 * what you can see. Edges among the neighbours are not selected: the click didn't claim that.
 *
 * With two picks it is the union of both neighbourhoods, and the edge between them — if there is
 * one and it is on screen — comes along on its own, since it is incident to both. That is worth
 * knowing: pick two notes that are already bound and the bind lights up without being asked for.
 *
 * `picks` rides along separately from `nodes`, because "these are the two I chose" and "these are
 * lit up" are different claims and the map draws them differently. */
function neighbourhood(ids, focus) {
  const nodes = new Set();
  const edges = new Set();
  for (const id of ids) {
    nodes.add(id);
    for (const e of incident.get(id) || []) {
      if (!visible.edges.has(e.id)) continue;
      edges.add(e.id);
      nodes.add(e.source === id ? e.target : e.source);
    }
  }
  return { nodes, edges, focus: focus || null, kind: ids.length ? 'note' : null,
           picks: new Set(ids) };
}

const EMPTY = () => ({ nodes: new Set(), edges: new Set(), focus: null, kind: null });

/* Forget the pair. Called wherever the selection stops being about notes at all — an edge, a
 * community, Escape — because each of those is a different claim, and leaving two slots quietly
 * filled behind one of them would mean the next shift+click refused a third note for reasons the
 * reader could no longer see. */
function dropPicks() {
  picks = [null, null];
  lastPick = null;
  Link.onPicks([null, null]);
}

/* The one place `selection` is assigned, so that everything which cares about it hears once and
 * hears the same thing. A walk records the note under focus (ui/write.js), and `focus` is exactly
 * the right signal: it is set by selecting a note and by nothing else — an edge, a community and a
 * refilter all leave it null or leave it alone, and none of those is somebody reading a note. */
function setSelection(next) {
  selection = next;
  applySelection();
  Write.onSelection(selection.kind === 'note' ? selection.focus : null);
}

/* A plain click means one note; shift means "and this one too", up to two.
 *
 * Two is not an arbitrary cap. Multi-select here exists so that a pair can be named, and a pair is
 * what a bind takes — a third note would be a selection with no verb behind it. So the third
 * shift+click is refused out loud rather than silently swallowed: pressed twice with nothing
 * happening and no reason given, a control reads as broken.
 *
 * Shift on a note that is already picked releases it, which is the only way to make room. That is
 * also the whole of the deselect story: a plain click starts over.
 */
function selectNode(id, additive) {
  if (!byId.has(id)) return;
  if (!additive) {
    picks = [id, null];
    lastPick = id;
  } else {
    const at = picks.indexOf(id);
    if (at !== -1) {
      picks[at] = null;
      if (lastPick === id) lastPick = picks.find(Boolean) || null;
    } else if (picks[0] && picks[1]) {
      Write.toast('two notes is the limit — shift-click one of them again to release it');
      return;
    } else {
      picks[picks[0] ? 1 : 0] = id;
      lastPick = id;
    }
  }
  commitPicks();
}

/* The one place a change to `picks` becomes a selection, a panel and a URL.
 *
 * The early return is what makes `link` a mode rather than a panel: while it holds the pane,
 * clicking a note fills a slot and does not swap the form out for that note's details. The same
 * click means something it did not mean a moment ago — exactly the bargain the walk button
 * already strikes (ui/write.js) — and like that one it is visible in the header the whole time.
 */
function commitPicks() {
  const ids = picks.filter(Boolean);
  setSelection(ids.length ? neighbourhood(ids, lastPick) : EMPTY());
  Link.onPicks([picks[0], picks[1]]);
  if (Link.holding()) return;
  if (!ids.length) {
    closePanel();
    if (location.hash) history.replaceState(null, '', location.pathname);
    return;
  }
  showNote(lastPick);
  if (location.hash !== `#note=${lastPick}`) {
    history.replaceState(null, '', `#note=${lastPick}`);
  }
}

/* The link panel's way in, and the swap that goes with it. Same `{ok, visible}` shape
 * highlightNote returns and for the same reason: a note found by searching the corpus can be one
 * this page's /api/graph read has never seen, and far more often one the window is hiding, so the
 * caller has to be able to tell "gone" from "there, but not on screen". */
function pick(slot, id) {
  const at = slot === 'a' ? 0 : 1;
  if (id === null) {
    if (lastPick === picks[at]) lastPick = picks[1 - at];
    picks[at] = null;
    commitPicks();
    return { ok: true, visible: true };
  }
  if (!byId.has(id)) return { ok: false };
  if (picks[1 - at] === id) picks[1 - at] = null;   // it cannot be both halves of its own pair
  picks[at] = id;
  lastPick = id;
  commitPicks();
  Graph.centerOn(id);
  return { ok: true, visible: visible.nodes.has(id) };
}

function swapPicks() {
  picks = [picks[1], picks[0]];
  commitPicks();
}

/* The one bind between two notes, whichever way round it was stored, and the same for lineage.
 * §7 allows at most one bind per pair in one direction and `suggest` refuses a second either way
 * (LinkExists → 409). Every edge is already on this page, so the link panel can say "these two
 * are already bound" instead of letting a button go and earn the refusal. */
const between = (type) => (a, b) => (incident.get(a) || []).find((e) => e.type === type
  && ((e.source === a && e.target === b) || (e.source === b && e.target === a))) || null;
const bindBetween = between('binds');
const begetsBetween = between('begets');

/* Where a write lands the reader afterwards. Outside link mode that is the note itself, which is
 * what every one of these did before. Inside it the PAIR is the subject and has to survive its own
 * verdict — re-selecting one half would empty a slot at the exact moment the bind it names came
 * into existence. Repainting instead is also how a suggest becomes its own ratify buttons: the
 * write has already re-read the corpus, so the pair now has an edge and the panel renders it. */
function reselect(id) {
  if (Link.holding()) { commitPicks(); return; }
  selectNode(id, false);
}

function selectEdge(id) {
  const e = edgeById.get(id);
  if (!e) return;
  // In link mode the subject is a pair of notes, and an edge is not one. Swapping the pane out for
  // it would empty two slots the reader is in the middle of filling, so the tap is simply not for
  // this mode. Shift does nothing to an edge in either mode now: shift is the pairing gesture, and
  // pairing is something only notes can be the subject of.
  if (Link.holding()) return;
  dropPicks();
  setSelection({ nodes: new Set([e.source, e.target]), edges: new Set([id]),
                 focus: null, kind: 'edge' });
  showEdge(e);
}

/* Used by the queue panel (ui/write.js) to jump to a suggested bind from its claim text, without
 * swapping the queue out for the edge's own detail panel — the ratify/reject buttons stay right
 * where the reader is looking. Matches on (source, target) because the queue only carries note
 * ids, never an edge id, and BINDS direction is exactly outV->inV both places (scripts/ui.py).
 * `visible` tells the caller whether anything actually lit up: a suggestion can easily name a
 * note outside the current window, and centering on a hidden edge would otherwise look like the
 * click did nothing. */
function highlightEdgeBetween(a, b) {
  const e = (incident.get(a) || [])
    .find((x) => x.type === 'binds' && x.source === a && x.target === b);
  if (!e) return { ok: false };
  dropPicks();
  setSelection({ nodes: new Set([a, b]), edges: new Set([e.id]), focus: null, kind: 'edge' });
  Graph.centerOn(e.id);
  return { ok: true, visible: visible.edges.has(e.id) };
}

/* Used by the search panel (ui/search.js) to jump to a hit: exactly the selection a click on the
 * node itself would make, and then centre on it — but WITHOUT opening the note's detail panel,
 * which would swap the results out for the first thing you clicked in them. Same bargain the two
 * functions around it strike, for the same reason.
 *
 * The hash is deliberately left alone: `#note=` is what selectNode publishes as "this is the note
 * the page is about", and a highlight from a list is not that claim yet.
 *
 * `ok` and `visible` are different answers. A search reads the corpus directly, so it can name a
 * note this page's own /api/graph read has never seen (something committed since) — and it can
 * far more often name one the current window or filters are hiding, which is why the caller has
 * to be able to tell "gone" from "there, but not on screen". */
function highlightNote(id) {
  if (!byId.has(id)) return { ok: false };
  picks = [id, null];
  lastPick = id;
  setSelection(neighbourhood([id], id));
  Link.onPicks([id, null]);
  Graph.centerOn(id);
  return { ok: true, visible: visible.nodes.has(id) };
}

/* Used by the status panel (ui/status.js) to jump to a detected community: select every member
 * this page knows about and frame the graph to them, leaving the status panel open exactly like
 * highlightEdgeBetween leaves the queue open. Community membership is corpus-wide (spec §13.2),
 * computed independently of this page's own /api/graph read, so `ids` is filtered against `byId`
 * rather than trusted outright — a write between the two reads could otherwise hand this an id
 * the graph has never heard of. */
function highlightCommunity(ids) {
  const nodes = new Set(ids.filter((id) => byId.has(id)));
  if (!nodes.size) return { ok: false };
  const edges = new Set();
  for (const id of nodes) {
    for (const e of incident.get(id) || []) {
      if (nodes.has(e.source) && nodes.has(e.target)) edges.add(e.id);
    }
  }
  dropPicks();
  setSelection({ nodes, edges, focus: null, kind: 'community' });
  Graph.fitTo(nodes);
  return { ok: true, visible: [...nodes].some((id) => visible.nodes.has(id)) };
}

function clearSelection() {
  dropPicks();
  setSelection(EMPTY());
  closePanel();
  if (location.hash) history.replaceState(null, '', location.pathname);
}

function applySelection() {
  const chosen = selection.picks || new Set();
  Graph.setSelection({
    nodes: new Set([...selection.nodes].filter((id) => visible.nodes.has(id))),
    edges: new Set([...selection.edges].filter((id) => visible.edges.has(id))),
    focus: visible.nodes.has(selection.focus) ? selection.focus : null,
    picks: new Set([...chosen].filter((id) => visible.nodes.has(id))),
  });
}

function fromHash() {
  const m = /^#note=(.+)$/.exec(location.hash);
  if (!m) return;
  const id = decodeURIComponent(m[1]);
  if (!byId.has(id) || selection.focus === id) return;
  selectNode(id, false);
  Graph.centerOn(id);
}

// ---- the detail panel ------------------------------------------------------
/* Which header button, if any, the pane's current content came from — "+ note"/queue/inbox/status
 * all render into this same #panel, so this is what lets exactly one of them read as pressed, the
 * way the layout and date-window segments already do. A node or edge selected on the graph calls
 * openPanel with no source, which is what clears all four: the pane is showing neither. */
function setPanelSource(source) {
  document.querySelectorAll('[data-panel-source]').forEach((b) => {
    const active = b.dataset.panelSource === source;
    b.classList.toggle('on', active);
    b.setAttribute('aria-pressed', String(active));
  });
}

function openPanel(html, source) {
  // Anything else filling the pane ends link mode, because the mode and the form are the same
  // thing: with the slots off screen, a click that quietly filled one would be a write affordance
  // nobody can see. `link` itself is exempt, since that call IS the panel rendering.
  if (source !== 'link') Link.close();
  panel.innerHTML = html;
  panel.hidden = false;
  applyPanelWidth();
  applyFiltersWidth();   // panel opening narrow forces the rail closed — see applyFiltersWidth
  panel.scrollTop = 0;
  panel.querySelector('.close').addEventListener('click', clearSelection);
  panel.querySelectorAll('.note-link').forEach((b) => b.addEventListener('click', () => {
    selectNode(b.dataset.id, false);
    Graph.centerOn(b.dataset.id);
  }));
  Write.bind(panel);      // the one point at which a rendered write affordance becomes live
  setPanelSource(source);
}

function closePanel() {
  Link.close();
  panel.hidden = true;
  applyPanelWidth();
  applyFiltersWidth();   // hands the rail back exactly as the reader left it — see applyFiltersWidth
  setPanelSource();
}

/* The view reads freely and writes only where it says so. Every write below goes through the
 * ratification flow or the ingestion path, each of which lands its own Op — nothing changes the
 * graph without the log entry that records it (spec §12.3). */
const FOOTER = '<footer>every change here is logged as an Op (spec §12.3)</footer>';

function noteLink(id, tag) {
  return `<button type="button" class="note-link" data-id="${esc(id)}">${esc(labelOf(id))}
    ${tag ? `<span class="tag">${esc(tag)}</span>` : ''}</button>`;
}

function group(title, rows) {
  return rows.length ? `<h3>${title}</h3>${rows.join('')}` : '';
}

function showNote(id) {
  const n = byId.get(id);
  if (!n) return;

  const parents = [];
  const children = [];
  const bindsOut = [];
  const bindsIn = [];
  for (const e of incident.get(id) || []) {
    const other = e.source === id ? e.target : e.source;
    const tag = `${e.mode || 'untyped'}${e.status ? ` · ${e.status}` : ''}`;
    if (e.type === 'begets') (e.target === id ? parents : children).push(noteLink(other, e.mode));
    else (e.source === id ? bindsOut : bindsIn).push(noteLink(other, tag));
  }

  const when = n.t === null ? '—' : new Date(n.t).toISOString().replace('T', ' ').slice(0, 19);
  openPanel(`
    <button type="button" class="close" title="Close (Esc)">×</button>
    <div class="kind">note</div>
    <h2>${esc(n.title || '(untitled)')}</h2>
    <dl>
      <dt>id</dt><dd class="mono">${esc(n.id)}</dd>
      <dt>address</dt><dd class="mono" id="d-address">…</dd>
      <dt>written</dt><dd>${esc(when)} UTC</dd>
      <dt>author</dt><dd id="d-author">…</dd>
      <dt>status</dt><dd>${esc(n.status)}${n.orphan ? ' · unlinked' : ''}</dd>
      <dt>visited</dt><dd>${n.visited} walk(s)</dd>
    </dl>
    <div class="claim terms">
      fitness <strong>${n.fitness}</strong>
      <span class="muted">= +${n.catalyzes} catalyzed − ${n.inhibits} inhibited
      + ${n.descendants} descendant(s) + ${n.visited} visit(s)</span>
    </div>
    <div class="body" id="d-body">…</div>
    <div id="d-source"></div>
    ${Write.editButton(n.id)}
    ${group('parents', parents)}
    ${group('children', children)}
    ${group('binds out', bindsOut)}
    ${group('binds in', bindsIn)}
    <div id="d-nearest"></div>
    ${Write.composeFrom(n.id)}
    ${FOOTER}`);

  fetch(`/api/note/${encodeURIComponent(id)}`)
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => { if (d && selection.focus === id) fillNote(d); })
    .catch(() => {});
}

function fillNote(d) {
  const set = (sel, html) => { const el = panel.querySelector(sel); if (el) el.innerHTML = html; };
  set('#d-address', esc(d.folgezettel || '—'));
  set('#d-author', esc(d.author || '—'));
  set('#d-body', esc(d.body));
  set('#d-source', d.source_ref
    ? `<p class="muted">source: ${esc(d.source_ref)}</p>` : '');

  /* A note that is near in meaning but has no edge is exactly move 1's provocation (§8.1), and
   * it is free here — the graph is already on the page. Since v0.8.1 it is also actionable: the
   * button proposes the bind, and a human still ratifies it (§8.2). */
  const linked = new Set((incident.get(d.id) || [])
    .map((e) => (e.source === d.id ? e.target : e.source)));
  const rows = d.nearest.map((h) => {
    const unlinked = !linked.has(h.id);
    return `<div class="nearrow">
      <button type="button" class="note-link" data-id="${esc(h.id)}">
        <span class="score">${h.score.toFixed(3)}</span>${esc(h.label)}
        ${unlinked ? '<span class="tag">near in meaning, not linked</span>' : ''}</button>
      ${unlinked ? Write.suggestButton(d.id, h.id) : ''}</div>`;
  });
  /* A note committed a moment ago has neither a vector nor a cache row yet, and "run
   * knn-cache.sh" is the wrong instruction for it — the worker is already on its way. */
  const nothingCached = d.nearest.length === 0 || !d.knn_cached;
  set('#d-nearest', !nothingCached
    ? group('near in meaning', rows)
    : '<h3>near in meaning</h3><p class="muted">'
      + (d.visited === 0 && !d.knn_cached
        ? 'nothing cached yet — a new note is embedded within seconds, and its neighbours appear'
          + ' after the next cache rebuild'
        : 'no cached neighbours — run <span class="mono">scripts/knn-cache.sh</span>') + '</p>');
  panel.querySelectorAll('#d-nearest .note-link').forEach((b) => b.addEventListener('click', () => {
    selectNode(b.dataset.id, false);
    Graph.centerOn(b.dataset.id);
  }));
  Write.bind(panel.querySelector('#d-nearest'));
}

/* An arrowhead is too easy to misread, and on an inhibits bind the direction IS the claim —
 * outV corrects inV (§6). So the panel spells the claim out in a sentence. */
function claimOf(e) {
  const a = esc(labelOf(e.source));
  const b = esc(labelOf(e.target));
  if (e.type === 'begets') {
    return e.mode === 'branches'
      ? `<strong>${a}</strong> branches into <strong>${b}</strong>`
      : `<strong>${a}</strong> is continued by <strong>${b}</strong>`;
  }
  if (e.mode === 'catalyzes') return `<strong>${a}</strong> catalyzes <strong>${b}</strong>`;
  if (e.mode === 'inhibits') return `<strong>${a}</strong> corrects <strong>${b}</strong>`;
  return `<strong>${a}</strong> and <strong>${b}</strong> are related — no verdict either way`;
}

function showEdge(e) {
  const pill = e.mode === 'catalyzes' ? 'pill good'
    : e.mode === 'inhibits' ? 'pill critical' : 'pill';
  const when = e.t === null ? '—' : new Date(e.t).toISOString().replace('T', ' ').slice(0, 19);
  openPanel(`
    <button type="button" class="close" title="Close (Esc)">×</button>
    <div class="kind">${e.type === 'begets' ? 'begets — lineage' : 'binds — association'}</div>
    <h2><span class="${pill}">${esc(e.mode || 'untyped')}</span>
      ${e.status ? `<span class="pill">${esc(e.status)}</span>` : ''}</h2>
    <p class="claim">${claimOf(e)}</p>
    <dl><dt>drawn</dt><dd>${esc(when)} UTC</dd></dl>
    <div id="d-rationale"></div>
    <h3>from</h3>${noteLink(e.source)}
    <h3>to</h3>${noteLink(e.target)}
    ${Write.bindActions(e)}
    ${FOOTER}`);

  if (e.type !== 'binds') return;
  fetch(`/api/note/${encodeURIComponent(e.source)}`)
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (!d || selection.kind !== 'edge') return;
      const hit = d.binds.find((b) => b.other === e.target && b.direction === 'out');
      const el = panel.querySelector('#d-rationale');
      if (hit && hit.rationale && el) {
        el.innerHTML = `<h3>rationale</h3><p class="rationale">${esc(hit.rationale)}</p>`;
      }
    })
    .catch(() => {});
}

// ---- labels ----------------------------------------------------------------
/* On by default: at this size the map is meant to be read, not only shaped. Off turns it into
 * pure structure — which is the better reading once the corpus outgrows legible labels, and the
 * cheapest frame there is, since the text pass is where a labelled graph spends itself.
 * Persisted, because it is a reading preference and not a filter. */
function setLabels(on) {
  labelsOn = on;
  const b = $('#labels');
  b.classList.toggle('on', on);
  b.setAttribute('aria-pressed', String(on));
  Graph.setLabels(on);
  try {
    localStorage.setItem(LABELS_KEY, on ? '1' : '0');
  } catch (err) { /* private mode or quota — the toggle still works for this session */ }
}

function readLabels() {
  try {
    return localStorage.getItem(LABELS_KEY) !== '0';
  } catch (err) {
    return true;
  }
}

// ---- dragged positions -----------------------------------------------------
/* A graph that reshuffles on every reload has no places in it, and §11.3's spatial map is a
 * claim that place carries meaning. Deterministic seeding (ui/graph.js) gets the same layout
 * every time; this makes it yours — drag the hubs where you think they belong and they stay. */
let saveTimer = null;
function savePositions() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      localStorage.setItem(POSITIONS_KEY, JSON.stringify(Graph.positions()));
    } catch (err) { /* private mode or quota — the layout is still deterministic without it */ }
  }, 400);
}

function loadPositions() {
  try {
    const raw = localStorage.getItem(POSITIONS_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (err) {
    return null;
  }
}

// ---- pane sizing -------------------------------------------------------------
/* Collapsing #filters and resizing either pane both come down to five grid-track widths on
 * <main> (ui/style.css), and these two functions are the only place that ever sets them — one
 * source of truth for what the tracks currently are, called after anything that changes either
 * pane's open state or width. Both panes persist their width the same way `labels` persists
 * on/off (below) — a view preference, not something the corpus has an opinion about — but only
 * #filters' open/closed state is saved: the detail panel starts every reload closed on purpose,
 * showing whatever the reader selects next rather than standing open on what they had open last.
 */
function applyFiltersWidth() {
  const narrow = isNarrow();
  const panelOpenNarrow = narrow && !panel.hidden;
  // `filtersOpen` stays the reader's real preference even while it is visually overridden below
  // — nothing here writes it, so a panel opened on a phone and later closed hands the rail back
  // exactly as it was, and desktop is untouched (panelOpenNarrow is always false there).
  const effOpen = filtersOpen && !panelOpenNarrow;
  main.style.setProperty('--filters-width', `${effOpen ? filtersWidth : 0}px`);
  main.style.setProperty('--filters-handle', effOpen && !narrow ? '6px' : '0px');
  filtersEl.hidden = !effOpen;
  // A drag handle for a pane that is either 0 or the whole screen has nothing to do — narrow
  // mode retires both resizers regardless of open state.
  filtersHandle.hidden = !effOpen || narrow;
  filtersHandle.setAttribute('aria-valuemin', String(FILTERS_MIN));
  filtersHandle.setAttribute('aria-valuemax', String(FILTERS_MAX));
  filtersHandle.setAttribute('aria-valuenow', String(filtersWidth));
  const btn = $('#togglefilters');
  // Nothing to toggle onto screen while the panel fills it, so the control itself steps aside
  // rather than sitting there offering a chevron that would do nothing visible.
  btn.hidden = panelOpenNarrow;
  const label = filtersOpen ? 'Hide the view & filters pane' : 'Show the view & filters pane';
  btn.textContent = filtersOpen ? '‹' : '›';
  btn.setAttribute('aria-pressed', String(filtersOpen));
  btn.title = label;
  btn.setAttribute('aria-label', label);
}

function applyPanelWidth() {
  const narrow = isNarrow();
  const open = !panel.hidden;
  // On narrow, the panel does not grow toward a stored preference — it takes the full track,
  // computed from the live layout rather than PANEL_DEFAULT/PANEL_MIN/PANEL_MAX, which describe
  // a desktop-sized pane and are never applied here. The graph's `minmax(0, 1fr)` track absorbs
  // the rest by shrinking toward 0 — no separate narrow grid template needed.
  const width = open ? (narrow ? Math.round(main.getBoundingClientRect().width) : panelWidth) : 0;
  main.style.setProperty('--panel-width', `${width}px`);
  main.style.setProperty('--panel-handle', open && !narrow ? '6px' : '0px');
  panelHandle.hidden = !open || narrow;
  panelHandle.setAttribute('aria-valuemin', String(PANEL_MIN));
  panelHandle.setAttribute('aria-valuemax', String(PANEL_MAX));
  panelHandle.setAttribute('aria-valuenow', String(panelWidth));
}

function loadPaneState() {
  try {
    const saved = JSON.parse(localStorage.getItem(FILTERS_KEY));
    if (saved && typeof saved.open === 'boolean') filtersOpen = saved.open;
    if (saved && typeof saved.width === 'number') {
      filtersWidth = clamp(saved.width, FILTERS_MIN, FILTERS_MAX);
    }
  } catch (err) { /* private mode, quota, or nothing saved yet — the defaults still work */ }
  try {
    const w = Number(localStorage.getItem(PANEL_WIDTH_KEY));
    if (w) panelWidth = clamp(w, PANEL_MIN, PANEL_MAX);
  } catch (err) { /* ditto */ }
}

function saveFiltersState() {
  try {
    localStorage.setItem(FILTERS_KEY, JSON.stringify({ open: filtersOpen, width: filtersWidth }));
  } catch (err) { /* private mode or quota — the toggle still works for this session */ }
}

function savePanelWidth() {
  try {
    localStorage.setItem(PANEL_WIDTH_KEY, String(panelWidth));
  } catch (err) { /* ditto */ }
}

/* One drag handle, mouse/touch or keyboard. `sign` is which way "the handle moves right" changes
 * the pane it belongs to: +1 for #filters (dragging right grows it), -1 for #panel (dragging
 * right shrinks it, since it is the panel's LEFT edge that is moving). Arrow keys use the same
 * sign, so "→" always means "move the handle right" on both handles, whichever way that resolves
 * for the pane behind it.
 *
 * Listens on `document` rather than the handle itself, because a fast drag routinely carries the
 * pointer off a 6px-wide strip — losing the drag the moment that happens would read as broken.
 */
function wireResizer(handle, { sign, min, max, def, get, set, persist }) {
  const cap = () => Math.min(max, window.innerWidth - CY_MIN);
  let startX = 0;
  let startWidth = 0;

  function onMove(e) {
    set(clamp(startWidth + sign * (e.clientX - startX), min, cap()));
  }
  function onUp() {
    document.removeEventListener('pointermove', onMove);
    document.removeEventListener('pointerup', onUp);
    main.classList.remove('resizing');
    handle.classList.remove('active');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    persist();
  }
  handle.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    startX = e.clientX;
    startWidth = get();
    main.classList.add('resizing');
    handle.classList.add('active');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onUp);
    e.preventDefault();
  });
  // A reset, the same as double-clicking any other divider — the escape hatch for whatever a
  // drag or a run of arrow-key presses left the pane at.
  handle.addEventListener('dblclick', () => { set(def); persist(); });
  handle.addEventListener('keydown', (e) => {
    const step = e.shiftKey ? 32 : 8;
    if (e.key === 'ArrowLeft') set(clamp(get() - sign * step, min, cap()));
    else if (e.key === 'ArrowRight') set(clamp(get() + sign * step, min, cap()));
    else if (e.key === 'Home') set(min);
    else if (e.key === 'End') set(cap());
    else return;
    e.preventDefault();
    persist();
  });
}

boot();
