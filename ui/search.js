/* The search panel: a locator for the map.
 *
 * Reads, and only reads. Its header button carries no `data-write-ui`, so it survives
 * `ui.sh run --read-only` for the same reason ui/status.js does — a corpus you have decided not
 * to write to is exactly one you might still want to find your way around — and it wires itself
 * for the same reason: write.js only binds #bar when writes are on. It borrows the same `ctx`
 * both of those borrow rather than redefining esc/openPanel/labelOf.
 *
 * A result **highlights its note in place** and leaves the results up, which is the same move the
 * ratification queue and the status panel's community rows make: a list you went looking for is a
 * list you are still working through, and swapping it out for the first thing you clicked would
 * cost you the other nine. `open` beside each row is the other half, for when the answer is the
 * note itself and not its place in the graph.
 *
 * **Lexical only.** The semantic half of §8 is already on the note panel, as "near in meaning",
 * served off the nightly k-NN cache. Asking the vector index live rebuilds every vector in the
 * corpus — minutes, not seconds — and behind a search box that is not slow, it is a hang. See
 * State.search in scripts/ui.py for the whole of that argument.
 */
let ctx = null;

/* Which text column the query runs against. The keys are lexical_search's own keyword names
 * (scripts/notelib.py), so the panel and `search.sh` ask the same question in the same words
 * rather than in two translations of it. */
const FIELDS = [
  ['text', 'title or body'],
  ['title', 'title'],
  ['body', 'body'],
  ['source', 'source'],
];

/* Kept in the module rather than in the DOM, so that clicking a note and coming back to search
 * finds the query and its results still there. Retyping a search you already ran, because you
 * looked at one of its answers, is the specific annoyance this exists to avoid. */
const form = { q: '', field: 'text', author: '', since: '', until: '' };
let hits = null;        // null: nothing run yet. [] is a real answer — "no matches".
let ran = null;         // the form as it stood when `hits` came back, for the results header

// ---- rendering -------------------------------------------------------------
/* The creation day. `created_at` is READONLY-derived from the id (spec §4), so the two agree;
 * the id is the fallback because it is the one shape that is never null. */
function day(hit) {
  if (hit.created_at) return String(hit.created_at).slice(0, 10);
  const s = String(hit.id || '');
  return s.length >= 8 ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}` : '—';
}

/* What was actually asked, said back. A search with nothing filled in is a browse of the most
 * recent notes — which is also what `search.sh` with no arguments does — and saying so is what
 * keeps an unfiltered list from reading as "everything matched". */
function describe(f) {
  const parts = [];
  if (f.q) parts.push(`${FIELDS.find(([k]) => k === f.field)[1]} contains “${f.q}”`);
  if (f.author) parts.push(`author ${f.author}`);
  if (f.since && f.until) parts.push(`${f.since} to ${f.until}`);
  else if (f.since) parts.push(`on or after ${f.since}`);
  else if (f.until) parts.push(`on or before ${f.until}`);
  return parts.join(' · ');
}

function row(h) {
  return `<div class="qrow">
    <div class="hit">
      <button type="button" class="claim link" data-hit="${ctx.esc(h.id)}"
        title="show this note in the graph">${ctx.esc(h.title || '(untitled)')}
        <span class="muted">${ctx.esc(day(h))} · ${ctx.esc(h.author || '—')}</span></button>
      <button type="button" class="go" data-open="${ctx.esc(h.id)}"
        title="open this note in the panel">open</button>
    </div>
    <p class="snip">${ctx.esc(h.snippet)}</p>
    ${h.source_ref ? `<p class="snip src">source: ${ctx.esc(h.source_ref)}</p>` : ''}</div>`;
}

/* Only the results are re-rendered, never the whole panel — rebuilding the form under a reader
 * would take the focus and the caret out of the box they are still typing in. It is also why the
 * slot is looked up fresh and its absence is a no-op: a search that was in flight when the reader
 * clicked a node has nowhere to land, and that is fine. */
function results() {
  const slot = document.querySelector('#s-results');
  if (!slot) return;
  if (hits === null) {
    slot.innerHTML = '<p class="muted">Nothing searched yet. Leave the box empty to browse the '
      + 'most recent notes.</p>';
    return;
  }
  const what = describe(ran);
  // A capped list has to say so. "50 notes" when there are 90 is not a rounding error, it is the
  // panel answering a different question than the one that was asked.
  const head = ran.capped ? `the first ${hits.length} notes`
    : `${hits.length} ${hits.length === 1 ? 'note' : 'notes'}`;
  slot.innerHTML = `<h3>${head}</h3>
    <p class="hint">${what ? ctx.esc(what) : 'the most recent notes — no filters'}${
      ran.capped ? ' · narrow it to see the rest' : ''}</p>
    ${hits.length ? hits.map(row).join('')
      : '<p class="muted">No matches. These are substring filters over what the note actually '
        + 'says, not a search by meaning — for that, open a note and read its '
        + '<strong>near in meaning</strong> list.</p>'}`;
  wireResults(slot);
}

/* Highlight in place, or open. Neither is wired through app.js's own `.note-link` handler, which
 * always opens the detail panel — that is exactly the behaviour the left-hand button is avoiding,
 * so these rows deliberately do not carry that class. */
function wireResults(slot) {
  slot.querySelectorAll('[data-hit]').forEach((b) => b.addEventListener('click', () => {
    const r = ctx.highlightNote(b.dataset.hit);
    if (!r.ok) { ctx.toast('that note is not in the graph — reload the page', 'bad'); return; }
    if (!r.visible) ctx.toast('highlighted, but outside the current view — widen the window or '
      + 'adjust the filters to see it');
  }));
  slot.querySelectorAll('[data-open]').forEach((b) => b.addEventListener('click', () => {
    ctx.selectNode(b.dataset.open, false);
    ctx.centerOn(b.dataset.open);
  }));
}

// ---- the panel -------------------------------------------------------------
async function run() {
  const query = new URLSearchParams();
  if (form.q) { query.set('q', form.q); query.set('field', form.field); }
  ['author', 'since', 'until'].forEach((k) => { if (form[k]) query.set(k, form[k]); });

  let data;
  try {
    const res = await fetch(`/api/search?${query}`);
    data = await res.json();
    if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  } catch (err) {
    ctx.toast(err.message, 'bad');
    return;
  }
  hits = data.hits;
  ran = { ...form, capped: data.count >= data.limit };
  results();
}

function open() {
  // Named for the form control, not for `form.field` — which is the scope, and is the seg below.
  const input = (key, label, value, type) => `<label class="field"><span>${label}</span>
    <input id="s-${key}" type="${type || 'text'}" value="${ctx.esc(value)}"></label>`;

  ctx.openPanel(`
    <button type="button" class="close" title="Close (Esc)">×</button>
    <div class="kind">search</div>
    <h2>find a note</h2>
    ${input('q', 'words', form.q)}
    <div class="seg wide scope" role="group" aria-label="Where to look">
      ${FIELDS.map(([key, label]) => `<button type="button" data-field="${key}"
        class="${form.field === key ? 'on' : ''}">${label}</button>`).join('')}
    </div>
    ${input('author', 'author', form.author)}
    <div class="dates">
      ${input('since', 'from', form.since, 'date')}
      ${input('until', 'to', form.until, 'date')}
    </div>
    <div class="verdict actions">
      <button type="button" id="s-run" class="on">search</button>
      <button type="button" id="s-clear">clear</button>
      <span class="hint">Enter · both days included, UTC</span>
    </div>
    <div id="s-results"></div>
    <footer>the same filters <span class="mono">search.sh</span> combines, AND-ed together and
      read straight off the corpus — a search writes nothing and does not count as a visit
      (§13.2). A result highlights the note on the map; <strong>open</strong> reads it.</footer>`,
  'search');

  const panel = document.querySelector('#panel');
  const read = () => {
    ['q', 'author', 'since', 'until'].forEach((k) => {
      form[k] = (panel.querySelector(`#s-${k}`).value || '').trim();
    });
  };
  const go = () => { read(); run(); };

  panel.querySelectorAll('.field input').forEach((box) => {
    box.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
  });
  panel.querySelectorAll('[data-field]').forEach((b) => b.addEventListener('click', () => {
    panel.querySelectorAll('[data-field]').forEach((o) => o.classList.remove('on'));
    b.classList.add('on');
    form.field = b.dataset.field;
    // Only if something has already been run: changing where to look is a refinement of a search
    // you can see, and re-asking it is the point. On an empty panel it would just be noise.
    if (hits !== null) go();
  }));
  panel.querySelector('#s-run').addEventListener('click', go);
  panel.querySelector('#s-clear').addEventListener('click', () => {
    Object.assign(form, { q: '', field: 'text', author: '', since: '', until: '' });
    hits = null;
    ran = null;
    open();
  });

  results();
  panel.querySelector('#s-q').focus();
}

export const Search = {
  open,

  /* Borrow the view's helpers and wire the one button. Unconditional, like the status panel's:
   * this one is available on a server that refuses every write. */
  init(context) {
    ctx = context;
    const button = document.querySelector('[data-search]');
    if (button) button.addEventListener('click', open);
  },
};
