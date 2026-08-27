/* The link panel: two notes, picked deliberately, and the bind proposed between them.
 *
 * Everything else in this view that draws an edge starts from a list the machine made — the
 * "near in meaning, not linked" rows on a note, the digest's suggestions in the queue. This one
 * starts from the human: you go and find the two notes yourself. That is the same act §3.3 calls
 * the expensive one ("catalysis across a generational gap … a human has to see it and say so"),
 * and it had no affordance until now.
 *
 * **It proposes.** The button is `SUGGEST_LINK` and nothing else, because there is no one-step
 * ratified bind anywhere in the grammar — `LinkManager.ratify` refuses a pair with no edge between
 * them, and §12.8 says a view that writes should be a new *caller* of the ingestion path and not a
 * new path. What happens after the suggest lands is not a second gesture bolted on: the pair now
 * HAS an edge, so the panel repaints and renders that edge's own verdict buttons, which are the
 * same ones the ratification queue shows. Suggest and ratify stay two acts (§8.2); they just
 * happen to be two acts you can perform without leaving the pane.
 *
 * Nothing here POSTs. The action area renders `[data-write]` markup borrowed from ui/write.js
 * (suggestButton, bindActions) and hands it back to `Write.bind` — the same arrangement app.js
 * uses for the note panel, and the reason "nothing outside write.js writes" is still true.
 *
 * **Order is the claim.** `from` is outV and `to` is inV, kept exactly as picked, because on an
 * `inhibits` bind the direction IS the claim (§6) — hence the swap, which exists so that getting
 * it backwards costs one click rather than two fresh searches.
 *
 * The searches are body-only and show three. This is a picker, not the search panel: you are here
 * because you already know which two notes you mean, and a fourth row is a reason to type a better
 * word rather than to scroll. Lexical, for the reason ui/search.js gives at length — asking the
 * vector index live rebuilds every vector in the corpus, and behind a click that is a hang.
 */
const SHOW = 3;                  // rows per box
const PROBE = SHOW + 1;          // ask for one more than that, so "there are more" is a fact
const SLOTS = { a: 0, b: 1 };
const ROLE = { a: 'from', b: 'to' };

let ctx = null;
let live = false;                // is this panel what the pane is currently showing?
let pair = [null, null];         // app.js's picks, mirrored for rendering only
let painted = {};                // what each slot was last rendered as — see paintSlot

const box = {
  a: { q: '', hits: null, more: false },
  b: { q: '', hits: null, more: false },
};

// ---- rendering -------------------------------------------------------------
/* Same fallback ui/search.js uses: `created_at` is READONLY-derived from the id (spec §4), so when
 * it is missing the id still carries the day. */
function day(hit) {
  if (hit.created_at) return String(hit.created_at).slice(0, 10);
  const s = String(hit.id || '');
  return s.length >= 8 ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}` : '—';
}

function hitRow(slot, h) {
  return `<div class="qrow">
    <button type="button" class="claim link" data-take="${ctx.esc(h.id)}"
      title="put this note in the ${ROLE[slot]} slot">${ctx.esc(h.title || '(untitled)')}
      <span class="muted">${ctx.esc(day(h))} · ${ctx.esc(h.author || '—')}</span></button>
    <p class="snip">${ctx.esc(h.snippet)}</p></div>`;
}

/* Only the results, never the box above them — rebuilding an input under someone mid-word takes
 * the caret with it. The same split ui/search.js draws, for the same reason. */
function paintHits(slot) {
  const host = document.querySelector(`#l-${slot}-hits`);
  if (!host) return;
  const s = box[slot];
  if (s.hits === null) {
    host.innerHTML = '<p class="hint">Enter searches note bodies. Or click the note on the map.</p>';
    return;
  }
  if (!s.hits.length) {
    host.innerHTML = '<p class="muted">No body contains that. This is a substring filter over what '
      + 'the note actually says, not a search by meaning.</p>';
    return;
  }
  // A capped list has to say so, and `more` is measured rather than assumed: the fetch asks for
  // four and shows three, so this is the corpus answering, not the panel guessing.
  host.innerHTML = s.hits.slice(0, SHOW).map((h) => hitRow(slot, h)).join('')
    + (s.more ? `<p class="hint">more than ${SHOW} match — narrow it</p>` : '');
  host.querySelectorAll('[data-take]').forEach((btn) => btn.addEventListener('click', () => {
    const r = ctx.pick(slot, btn.dataset.take);
    if (!r.ok) { ctx.toast('that note is not in the graph — reload the page', 'bad'); return; }
    if (!r.visible) ctx.toast('chosen, but outside the current view — widen the window or adjust '
      + 'the filters to see it on the map');
  }));
}

/* A slot is either a chosen note or a box to find one in. Guarded on what it last rendered,
 * because every pick repaints BOTH slots and the untouched one may be holding a half-typed query.
 * `painted` starts empty on open, so `undefined !== null` forces the first render. */
function paintSlot(slot) {
  const host = document.querySelector(`#l-${slot}`);
  if (!host) return;
  const id = pair[SLOTS[slot]] || null;
  if (painted[slot] === id) return;
  painted[slot] = id;

  host.innerHTML = id
    ? `<div class="lrole">${ROLE[slot]}</div>
       <div class="hit">
         <button type="button" class="claim link" data-centre="${ctx.esc(id)}"
           title="centre the map on this note">${ctx.esc(ctx.labelOf(id))}</button>
         <button type="button" class="go" data-drop="1" title="empty this slot">clear</button>
       </div>`
    : `<div class="lrole">${ROLE[slot]}</div>
       <label class="field"><span>find a note by its body</span>
         <input id="l-${slot}-q" type="text" value="${ctx.esc(box[slot].q)}"
           placeholder="words the note contains"></label>
       <div id="l-${slot}-hits"></div>`;

  const input = host.querySelector(`#l-${slot}-q`);
  if (input) {
    input.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      box[slot].q = (input.value || '').trim();
      search(slot);
    });
  }
  const centre = host.querySelector('[data-centre]');
  if (centre) centre.addEventListener('click', () => ctx.centerOn(centre.dataset.centre));
  const drop = host.querySelector('[data-drop]');
  if (drop) drop.addEventListener('click', () => ctx.pick(slot, null));

  if (!id) paintHits(slot);
}

/* What can be done with the pair as it stands, which is four different answers.
 *
 * The already-bound case is the one worth arriving at deliberately rather than through a 409:
 * §7 allows at most one bind per pair in one direction, `suggest` refuses a second either way
 * round, and the client already holds every edge — so the panel can show the bind and its verdict
 * instead of letting the button earn a refusal.
 */
function paintAction() {
  const host = document.querySelector('#l-act');
  if (!host) return;
  const [a, b] = pair;

  if (!a || !b) {
    host.innerHTML = `<p class="hint">Both slots have to be filled before anything can be
      proposed. Click a note on the map for <strong>${ROLE.a}</strong>, shift-click a second for
      <strong>${ROLE.b}</strong> — or search for them above.</p>`;
    return;
  }
  if (a === b) {
    host.innerHTML = '<p class="claim bad">Both slots hold the same note. A bind is a relation '
      + 'between two of them.</p>';
    return;
  }

  const bound = ctx.bindBetween(a, b);
  if (bound) {
    /* Two quite different situations, and one sentence cannot serve both. A `suggested` edge is
     * almost always the one this panel proposed a moment ago, and telling the reader it "already
     * exists" would describe their own last click back to them as somebody else's doing. A
     * `ratified` one is a standing claim they have walked into, and saying so is the useful half:
     * it is why the button they came for is not there. */
    const mine = bound.status === 'suggested';
    host.innerHTML = `<p class="claim">${ctx.claimOf(bound)}</p>
      <p class="hint">${mine
        ? 'Proposed, and waiting on a verdict — it is in the queue whether or not you give it one '
          + 'here. Suggesting and ratifying stay two acts (§8.2); this is the second.'
        : 'These two are already bound, so there is nothing left to propose — a note pair holds at '
          + 'most one bind, in one direction (§7).'}
        The verdict below is on the bind that exists, in the direction it was <em>stored</em>,
        which ${mine ? 'is the way round you picked them' : 'may not be the way round you picked '
          + 'them'}. Which is also why <strong>⇅ swap</strong> is greyed out: the order here
        decides nothing now, and turning the bind around means <strong>reject</strong>ing it and
        suggesting it the other way round.</p>
      ${ctx.bindActions(bound)}`;
    ctx.bindWrites(host);
    return;
  }

  // Lineage is already catalysis, within a line of descent, and asserting it a second time across
  // the associative layer is the thing §3.3's split exists to keep separate. Said, not refused:
  // an `inhibits` from a child back onto its parent is a correction, and a perfectly good claim.
  const lineage = ctx.begetsBetween(a, b);
  host.innerHTML = `
    <p class="claim"><strong>${ctx.esc(ctx.labelOf(a))}</strong> →
      <strong>${ctx.esc(ctx.labelOf(b))}</strong> — related, with no verdict either way</p>
    ${lineage ? `<p class="hint">These two are already lineage (<span class="mono">begets ·
      ${ctx.esc(lineage.mode || 'continues')}</span>), which is catalysis within one line of
      descent already (§3.3). A bind on top of it says something the lineage does not.
      <strong>⇅ swap</strong> is greyed out for that reason: the pair already has an order, and
      proposing against its grain should be a deliberate re-pick rather than one click.</p>` : ''}
    <div class="verdict actions">
      ${ctx.suggestButton(a, b)}
      <span class="hint">proposes it untyped — the verdict appears here next</span>
    </div>`;
  ctx.bindWrites(host);
}

/* The swap is a convenience for a form you are still composing, and it stops being one the moment
 * the two notes already stand in a relation. **Any** edge between them disables it, lineage as
 * much as association, because the rule is about the pair rather than about the edge type: two
 * notes the graph already orders are not two notes this form gets to reorder on a whim.
 *
 * The two cases are inert for different reasons, and the title says which.
 *
 * With a BINDS, flipping is inert outright: the action area renders from the edge's *stored*
 * direction, so reordering two labels would change nothing while appearing to. Reversing it for
 * real is reject-then-suggest-the-other-way — two Ops, one of which deletes a proposal — which is
 * not something a ⇅ should do quietly, so the title points at the `reject` directly below it.
 *
 * With a BEGETS, flipping would genuinely change what gets proposed, and that is exactly why it
 * should not be one click. Lineage cannot be reversed at all, so proposing a bind against its
 * grain is an assertion about a pair whose order is already settled (§3.3) — real, sometimes
 * right, and never accidental. Re-picking both notes still does it. That is the point: the cheap
 * gesture goes, the deliberate one stays.
 */
function paintSwap() {
  const btn = document.querySelector('#l-swap');
  if (!btn) return;
  const [a, b] = pair;
  const pinned = a && b && a !== b
    ? ctx.bindBetween(a, b) || ctx.begetsBetween(a, b)     // the bind first: it is the actionable one
    : null;
  btn.disabled = !!pinned;
  btn.title = !pinned ? 'put each note in the other slot'
    : (pinned.type === 'begets'
      ? `these two already stand in a lineage, and that order is not this form's to flip — pick
         them again the other way round if you mean to propose against it`
      : `these two are already bound, so the order here no longer decides anything — to reverse
         it, ${pinned.status === 'ratified' ? 'reject the bind' : 'reject the proposal'} below and
         pick them the other way round`).replace(/\s+/g, ' ');
}

function paint() {
  paintSlot('a');
  paintSlot('b');
  paintAction();
  paintSwap();          // after paintAction: what it says depends on the same edge lookup
}

// ---- the wire --------------------------------------------------------------
/* Body only, and four rows asked for to show three. `field=body` and `limit` are both already
 * query parameters on /api/search (scripts/ui.py), so this panel adds no route and no server
 * change — it asks the existing locator a narrower question. */
async function search(slot) {
  const query = new URLSearchParams({ field: 'body', limit: String(PROBE) });
  if (box[slot].q) query.set('q', box[slot].q);
  try {
    const res = await fetch(`/api/search?${query}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
    box[slot].hits = data.hits;
    box[slot].more = data.hits.length > SHOW;
  } catch (err) {
    ctx.toast(err.message, 'bad');
    return;
  }
  paintHits(slot);
}

function open() {
  live = true;
  painted = {};
  ctx.openPanel(`
    <button type="button" class="close" title="Close (Esc)">×</button>
    <div class="kind">link</div>
    <h2>bind two notes</h2>
    <div class="slot" id="l-a"></div>
    <div class="lswap">
      <button type="button" id="l-swap" title="put each note in the other slot">⇅ swap</button>
    </div>
    <div class="slot" id="l-b"></div>
    <div id="l-act"></div>
    <footer>this proposes a bind (<span class="mono">SUGGEST_LINK</span>, §12.3) and does not
      ratify it — the machine may only ever propose, and so may this button. Order is kept: on a
      <strong>corrects</strong> bind, from→to <em>is</em> the claim (§6).</footer>`, 'link');

  document.querySelector('#l-swap').addEventListener('click', () => ctx.swapPicks());
  paint();
}

export const Link = {
  open,

  /* Whether the pane is currently this panel. app.js asks before it does the two things that
   * would otherwise destroy a half-filled form: swapping the pane out for a clicked note's
   * details, and landing a completed write back on one half of the pair. */
  holding: () => live,

  /* Called by app.js whenever the pane stops being this panel — a header button, Escape, a click
   * on the background. The mode ends with the panel, so there is never a moment where clicking a
   * note fills a slot nobody can see. */
  close() { live = false; },

  /* The picks changed. Repaints in place rather than reopening: the panel is a pure function of
   * (the pair, the graph), so this is also what turns a suggest into its own ratify buttons — the
   * write refreshes the corpus, the pair now has an edge, and the action area renders it. */
  onPicks(next) {
    pair = next;
    if (live) paint();
  },

  init(context) { ctx = context; },
};
