/* The status panel: what the machinery is doing, and what the corpus currently measures.
 *
 * Reads, and only reads. It lives outside ui/write.js because that file's invariant is that
 * nothing outside it writes, and the cheapest way to keep that true is for the panel that writes
 * nothing not to be in it. It lives outside ui/app.js because app.js is the graph view, and the
 * scheduler is not about the graph. It borrows the same `ctx` write.js does rather than
 * redefining esc/openPanel/labelOf — one definition of each, on this side of the wire too.
 *
 * Its header button carries no `data-write-ui`, so it survives `ui.sh run --read-only`: a corpus
 * you have decided not to write to is exactly one you might want to stand back and measure. That
 * also means it has to wire itself, since write.js only binds #bar when writes are on.
 *
 * There is deliberately no way to run a job from here. `knn-cache` absorbs the one LSM_VECTOR
 * rebuild — ~11 minutes at this corpus size — and a button that can hang the page for eleven
 * minutes is not a convenience. The scheduler picks up anything due within the minute anyway;
 * what this panel is for is noticing that it hasn't.
 */
let ctx = null;

// ---- small renderers -------------------------------------------------------
/* Every section arrives either as its data or as {error}, because scripts/ui.py wraps each one
 * separately — six reads behind one route, and one of them throwing must not cost the other
 * five. So every renderer checks first, and a broken section shows its reason in place. */
const broke = (s) => !!(s && !Array.isArray(s) && typeof s === 'object' && s.error);
const reason = (s) => `<p class="claim bad">${ctx.esc(s.error)}</p>`;

/* Relative, because "14h ago" is the question being asked and 2026-07-27T02:00:38.304737+00:00
 * is not. The exact stamp goes in the title, where it costs nothing and settles arguments. */
function rel(iso) {
  if (!iso) return 'never';
  const ms = new Date(iso).getTime() - Date.now();
  if (!Number.isFinite(ms)) return String(iso);
  const abs = Math.abs(ms);
  const [n, unit] = abs < 6e4 ? [Math.round(abs / 1e3), 's']
    : abs < 36e5 ? [Math.round(abs / 6e4), 'm']
      : abs < 864e5 ? [Math.round(abs / 36e5), 'h']
        : [Math.round(abs / 864e5), 'd'];
  return ms < 0 ? `${n}${unit} ago` : `in ${n}${unit}`;
}

const pill = (text, kind, title) => `<span class="pill${kind ? ` ${kind}` : ''}"
  ${title ? `title="${ctx.esc(title)}"` : ''}>${ctx.esc(text)}</span>`;

/* Spec §3.3: a set is autocatalytic when the catalysis relation restricted to it contains a
 * cycle — at least one ratified BINDS{catalyzes} feeding back to something upstream of itself,
 * since BEGETS alone is a DAG and can never close one. Yes/no and structural, not a threshold. */
const AUTOCATALYTIC_HINT = 'the catalysis relation inside this community contains a cycle — a '
  + 'note that catalyzes something upstream of itself (spec §3.3)';

/* Two places, uniformly. analytics rounds to six for the CLI, where the extra digits cost a
 * column; here they cost a reading, and nobody compares reproduction rates to the millionth. */
const dp = (n) => (typeof n === 'number' ? n.toFixed(2) : String(n));

/* Reuses app.js's own note-link shape, so openPanel wires these for free and a row here selects
 * the note in the graph exactly as a row in the detail panel does. */
const noteRow = (r, right) => `<button type="button" class="note-link" data-id="${ctx.esc(r.id)}">
  <span class="score">${ctx.esc(right)}</span>${ctx.esc(r.label)}</button>`;

// ---- sections --------------------------------------------------------------
function daemons(list) {
  if (broke(list)) return reason(list);
  // 'unknown' is not hedging. The pid file is read without lib.sh's pgrep fallback, because that
  // fallback WRITES a pid file and a GET may not — so a stale file cannot be told apart from a
  // daemon running under a pid nobody recorded, and saying "down" would be a claim, not a fact.
  const tone = { up: 'good', down: 'critical' };
  return `<div class="pills">${list.map((d) => `<span class="pill ${tone[d.state] || ''}"
    title="${ctx.esc(d.script)}${d.pid ? ` · pid ${d.pid}` : ''}"
    >${ctx.esc(d.name)} ${ctx.esc(d.state)}</span>`).join(' ')}</div>`;
}

function jobs(list) {
  if (broke(list)) return reason(list);
  return `<div class="jobs">
    <div class="jobrow head"><span>job</span><span>cadence</span><span>last</span><span>next</span></div>
    ${list.map((j) => `<div class="jobrow">
      <span class="mono">${ctx.esc(j.name)}</span>
      <span class="muted">${ctx.esc(j.kind)}</span>
      <span title="last run ${ctx.esc(j.last_run || 'never')}">${rel(j.last_run)}</span>
      <span class="${j.due ? 'due' : ''}" title="${j.blocked_until
        ? `backing off after a failed run until ${ctx.esc(j.blocked_until)} — then ${ctx.esc(j.next_run || '—')}`
        : `next run ${ctx.esc(j.next_run || '—')}`}"
        >${j.due ? 'due' : rel(j.next_run)}${j.blocked_until ? ' ⚠' : ''}</span>
    </div>`).join('')}
  </div>`;
}

function log(l) {
  if (broke(l)) return reason(l);
  // Escaped without exception: these lines carry whatever a failing job wrote to stderr.
  return `<p class="hint mono">${ctx.esc(l.path)}</p>`
    + (l.lines.length ? `<pre class="logtail">${ctx.esc(l.lines.join('\n'))}</pre>`
      : '<p class="muted">Nothing logged yet — the scheduler writes here when it runs a job.</p>');
}

function measured(m) {
  if (broke(m)) return reason(m);
  const c = m.criticality;
  return `
    <h3>criticality</h3>
    <p class="claim">${pill(c.band, c.band === 'critical' ? 'good' : 'critical')}
      ${ctx.esc(c.advice)}</p>
    <dl>
      <dt>mean degree</dt><dd class="terms">${c.mean_degree}
        <span class="muted">target ${c.target_degree[0]}–${c.target_degree[1]}</span></dd>
      <dt>median</dt><dd class="terms">${c.median_degree}</dd>
      <dt>unlinked</dt><dd class="terms">${c.orphans} of ${c.notes}
        <span class="muted">${Math.round(c.orphan_fraction * 100)}%</span></dd>
      <dt>avalanche</dt><dd class="terms">${c.avalanche}
        ${c.avalanche_large ? '<span class="muted">large</span>' : ''}</dd>
    </dl>

    <h3>fittest notes</h3>
    ${m.fitness.map((r) => noteRow(r, r.fitness)).join('')}

    <h3>least revisited</h3>
    <p class="hint">The corpus holds these and you have not come back to them. Only a recorded
      walk moves this number (§13.2) — browsing does not.</p>
    ${m.attention.map((r) => noteRow(r, r.visited)).join('')}

    <h3>communities</h3>
    ${m.communities.map((k) => `<div class="qrow">
      <button type="button" class="claim link" data-members="${k.members.map(ctx.esc).join(',')}"
        title="show this community in the graph">${k.size} notes
        <span class="muted">· hub ${ctx.esc(ctx.labelOf(k.hub))}</span></button>
      <p class="hint terms">
        ${pill(k.autocatalytic ? 'autocatalytic' : 'not autocatalytic',
          k.autocatalytic ? 'good' : '', AUTOCATALYTIC_HINT)}
        reproduction ${dp(k.reproduction_rate)} ·
        local criticality ${dp(k.criticality)}${k.autocatalytic
          ? ` · core of ${k.autocatalytic_core.length}` : ''}</p>
    </div>`).join('')}`;
}

function health(h) {
  if (broke(h)) return reason(h);
  const k = h.knn || {};
  return `<dl>
    <dt>k-NN cache</dt><dd class="terms">${k.cached} of ${k.embedded}
      ${k.stale ? pill('stale', 'critical') : pill('fresh', 'good')}</dd>
    ${(k.reasons || []).length
      ? `<dt></dt><dd class="muted">${ctx.esc(k.reasons.join('; '))}</dd>` : ''}
    <dt>built</dt><dd class="terms">${ctx.esc(k.built_at || 'never')}</dd>
    <dt>unembedded</dt><dd class="terms">${h.embed_backlog}
      ${h.embed_backlog ? '<span class="muted">embed-worker drains these</span>' : ''}</dd>
    <dt>waiting</dt><dd class="terms">${h.staged} typed · ${h.scans} handwritten</dd>
    <dt>operations</dt><dd class="terms">${h.ops}</dd>
  </dl>`;
}

// ---- the panel -------------------------------------------------------------
const shell = (body) => `
  <button type="button" class="close" title="Close (Esc)">×</button>
  <div class="kind">status</div>
  ${body}`;

async function open() {
  /* Opened first, filled second. Gathering this costs nine round trips to ArcadeDB — each one
   * 80-400ms, so the bill is the number of queries and not the size of the answers — which is
   * ~3 seconds cold, and a panel that shows nothing for three seconds after a click reads as a
   * broken button rather than a slow one. The 60s cache makes the second look instant; it is
   * the first, and every one after a write, that needs saying out loud. */
  ctx.openPanel(shell(`<h2>the machinery</h2>
    <p class="muted">Reading the corpus…</p>`), 'status');

  let data;
  try {
    const res = await fetch('/api/status');
    if (!res.ok) throw new Error(String(res.status));
    data = await res.json();
  } catch (err) {
    // Feature detection rather than a version number: /api/status is additive, so a server too
    // old to serve it is not a mismatch to be policed, just a route that is not there.
    ctx.openPanel(shell(`<h2>nothing to report</h2>
      <p class="muted">This server did not answer <span class="mono">/api/status</span> — it is
        older than this page. Restart it with <span class="mono">ui.sh stop &amp;&amp; ui.sh
        start</span>.</p>`), 'status');
    return;
  }

  ctx.openPanel(shell(`
    <h2>the machinery</h2>
    ${daemons(data.daemons)}
    ${jobs(data.jobs)}
    <p class="hint">Read-only on purpose: <span class="mono">knn-cache</span> absorbs the one
      vector-index rebuild, minutes rather than seconds, and the scheduler takes anything due
      within the tick. Nightly and weekly jobs only fire after 02:00 UTC.</p>

    <h3>scheduler log</h3>
    ${log(data.log)}

    <h2 class="spaced">the corpus</h2>
    ${measured(data.measured)}

    <h2 class="spaced">machinery health</h2>
    ${health(data.health)}

    <footer>measured on demand and stored nowhere — this panel writes nothing, not even an Op
      (spec §13). Same numbers as <span class="mono">analytics.sh</span></footer>`), 'status');

  // The graph is what "show this community" means — highlight and frame in place, the same move
  // as the queue's claim link (ui/write.js), so the status readout stays open while you look.
  document.querySelector('#panel').querySelectorAll('.claim.link[data-members]').forEach((el) => {
    el.addEventListener('click', () => {
      const ids = el.dataset.members.split(',').filter(Boolean);
      const r = ctx.highlightCommunity(ids);
      if (!r.ok) { ctx.toast('no notes from this community are in the current graph', 'bad'); return; }
      if (!r.visible) ctx.toast('highlighted, but outside the current view — widen the window or '
        + 'adjust filters to see it');
    });
  });
}

export const Status = {
  open,

  /* Borrow the view's helpers and wire the one button. Unconditional, unlike write.js's own
   * binding: this panel is available on a server that refuses every write. */
  init(context) {
    ctx = context;
    const button = document.querySelector('[data-status]');
    if (button) button.addEventListener('click', open);
  },
};
