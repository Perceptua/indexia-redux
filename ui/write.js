/* Everything in the view that changes the corpus. Nothing outside this file writes.
 *
 * That is the same division scripts/notelib.py draws — every mutation goes through Ingestor or
 * LinkManager and nowhere else — kept on this side of the wire for the same reason: there is one
 * place to look to find out what the page can do to the graph, and one place a request can pick
 * up the two headers the server demands (see Handler._guard in scripts/ui.py).
 *
 * Every affordance is rendered as a `[data-write]` button and wired by `bind()` after the panel
 * HTML lands. The alternative — a listener attached at each call site — would mean app.js knew
 * how to issue a write, which is exactly what this file exists to prevent.
 */

const MODES = [
  ['catalyzes', 'catalyzes', 'A feeds B: it enables, extends or supports it'],
  ['inhibits', 'corrects', 'A corrects B. The direction IS the claim (§6) — if that is '
    + 'backwards, reject it and suggest it the other way round'],
  [null, 'untyped', 'related, with no verdict either way'],
];

let ctx = null;         // set by init(): the view's own helpers, so this file borrows and
let on = false;         // never redefines esc/labelOf/claimOf/openPanel/refresh
let toastTimer = null;
let inflight = false;

// ---- the wire --------------------------------------------------------------
/* The one place a write leaves the page. Content-Type and X-Indexia-Write are not decoration:
 * neither can be set on a cross-origin request without a CORS preflight, and the server answers
 * no OPTIONS, so they are what stop a page you happen to be visiting from writing to your corpus.
 */
async function post(path, payload) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Indexia-Write': '1' },
    body: JSON.stringify(payload || {}),
  });
  let data = {};
  try {
    data = await res.json();
  } catch (err) { /* no body, or not JSON — the status still carries the verdict */ }
  if (!res.ok) {
    const failure = new Error(data.error || `${res.status} ${res.statusText}`);
    failure.status = res.status;
    failure.data = data;
    throw failure;
  }
  return data;
}

/* The library's error messages name the fix — CosmeticDriftError spells out the exact
 * `link.sh suggest … --mode inhibits` to run instead — so they are shown verbatim and given
 * long enough to read. Paraphrasing them would throw away the useful half. */
function toast(message, kind) {
  const el = document.querySelector('#toast');
  el.textContent = message;
  el.className = kind === 'bad' ? 'bad' : '';
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, kind === 'bad' ? 14000 : 4000);
}

/* One write, start to finish: refuse to overlap with another, run it, then `after`, then say what
 * happened. Overlap matters because a graph write ends in a full re-read, and two of those racing
 * would leave the page showing whichever finished second. */
async function envelope(work, done, after) {
  if (inflight) return;
  inflight = true;
  document.body.classList.add('writing');
  try {
    const result = await work();
    if (after) await after();
    if (done) done(result);
  } catch (err) {
    toast(err.message, 'bad');
    if (err.status === 422) driftRemedy(err.data);
  } finally {
    inflight = false;
    document.body.classList.remove('writing');
  }
}

/* Something that changed the corpus: re-read it before saying so. */
const run = (work, done) => envelope(work, done, () => ctx.refresh());

/* Something that did not. Parking a file adds no note and no edge — nothing has reached the graph
 * until ingest commits it — so re-reading would be five whole-table scans to redraw the same
 * picture, once per dropped file. */
const runQuiet = (work, done) => envelope(work, done, null);

// ---- rendering helpers -----------------------------------------------------
const attrs = (o) => Object.entries(o)
  .filter(([, v]) => v !== null && v !== undefined)
  .map(([k, v]) => `${k}="${ctx.esc(v)}"`).join(' ');

function button(action, data, label, title) {
  return `<button type="button" class="wr" ${attrs({ 'data-write': action, ...data })}
    ${title ? `title="${ctx.esc(title)}"` : ''}>${ctx.esc(label)}</button>`;
}

/* Move 1's provocation, made actionable (§8.1): near in meaning, no edge between them. It goes
 * to `suggest`, never to `ratify` — the view proposes and the human disposes, and that split
 * (§8.2) is worth more than the one click it costs. */
function suggestButton(a, b) {
  return on ? button('suggest', { 'data-a': a, 'data-b': b }, 'link',
                     'propose a bind for ratification — this does not ratify it') : '';
}

/* The claim spelled out beside each verdict, because on an inhibits bind the direction is the
 * claim, and "ratify" on its own does not say which way it points. */
function bindActions(edge) {
  if (!on || edge.type !== 'binds') return '';
  // An unratified bind is being decided; a ratified one is being relabelled, and §7 has a
  // separate rule for that. Sending both through `ratify` would log the second as a first
  // verdict and lose the distinction the grammar is drawing.
  const act = edge.status === 'ratified' ? 'retype' : 'ratify';
  const rows = MODES.map(([mode, verb, why]) => {
    const claim = mode === null
      ? `<strong>${ctx.esc(ctx.labelOf(edge.source))}</strong> and
         <strong>${ctx.esc(ctx.labelOf(edge.target))}</strong> are related`
      : `<strong>${ctx.esc(ctx.labelOf(edge.source))}</strong> ${verb}
         <strong>${ctx.esc(ctx.labelOf(edge.target))}</strong>`;
    return `<div class="verdict">
      ${button(act, { 'data-a': edge.source, 'data-b': edge.target, 'data-mode': mode },
               act === 'retype' ? verb : `ratify: ${verb}`, why)}
      <span class="sub-claim">${claim}</span></div>`;
  });
  return `<h3>${edge.status === 'ratified' ? 'change the verdict' : 'ratify'}</h3>
    ${rows.join('')}
    <div class="verdict">${button('reject', { 'data-a': edge.source, 'data-b': edge.target },
                                  'reject', 'delete the bind — a proposal is not corpus (§2.4)')}
      <span class="sub-claim muted">no relation after all</span></div>`;
}

const editButton = (noteId) => (on ? `<div class="verdict">${button(
  'edit', { 'data-id': noteId }, 'edit',
  'fix a typo in place — refused if it changes what the note means (§6)')}</div>` : '');

const composeFrom = (noteId) => (on ? `<h3>write from here</h3><div class="verdict">
  ${button('compose', { 'data-parent': noteId, 'data-mode': 'continue' }, 'continue')}
  ${button('compose', { 'data-parent': noteId, 'data-mode': 'branch' }, 'branch')}</div>` : '');

// ---- compose ---------------------------------------------------------------
/* No <form> element anywhere here, and that is on purpose twice over: a stray Enter cannot
 * submit one that does not exist, and a committed note cannot be un-added (§2.4 permits deleting
 * only a proposal). Ctrl+Enter commits, for the people who want it. */
function compose(opts) {
  const o = opts || {};
  const parentLabel = o.parent ? ctx.labelOf(o.parent) : null;
  const lineage = o.parent ? `
    <h3>lineage</h3>
    <div class="seg wide" role="group" aria-label="How this note follows its parent">
      <button type="button" data-cmode="continue" class="${o.mode !== 'branch' ? 'on' : ''}">
        continues</button>
      <button type="button" data-cmode="branch" class="${o.mode === 'branch' ? 'on' : ''}">
        branches</button>
      <button type="button" data-cmode="">unattached</button>
    </div>
    <p class="hint">from <span class="mono">${ctx.esc(parentLabel)}</span></p>` : '';

  ctx.openPanel(`
    <button type="button" class="close" title="Close (Esc)">×</button>
    <div class="kind">new note</div>
    <h2>${o.corrects ? 'a correction' : 'write a note'}</h2>
    ${o.corrects ? `<p class="claim">This will be committed as its own note and bound back to
      <strong>${ctx.esc(ctx.labelOf(o.corrects))}</strong> with a ratified
      <strong>inhibits</strong> — the correction the corpus actually supports (§6).</p>` : ''}
    <label class="field"><span>body</span>
      <textarea id="c-body" rows="10" placeholder="the note itself"></textarea></label>
    <label class="field"><span>title</span><input id="c-title" type="text"></label>
    <label class="field"><span>source</span>
      <input id="c-source" type="text" placeholder="where it came from"></label>
    <label class="field"><span>author</span><input id="c-author" type="text" value="human"></label>
    ${lineage}
    <div class="verdict actions">
      <button type="button" id="c-commit" class="on">commit</button>
      <span class="hint">Ctrl+Enter · this cannot be undone</span>
    </div>
    <footer>a note commits without its vector; the embed worker adds one within seconds
      (spec §12.3)</footer>`, 'compose');

  const panel = document.querySelector('#panel');
  let mode = o.parent ? (o.mode === 'branch' ? 'branch' : 'continue') : null;
  panel.querySelectorAll('[data-cmode]').forEach((b) => b.addEventListener('click', () => {
    panel.querySelectorAll('[data-cmode]').forEach((x) => x.classList.remove('on'));
    b.classList.add('on');
    mode = b.dataset.cmode || null;
  }));

  const value = (sel) => (panel.querySelector(sel).value || '').trim();
  const commit = () => run(async () => {
    const body = value('#c-body');
    if (!body) throw new Error('a note needs a body — that is the whole of it (spec §6)');
    const payload = { body, title: value('#c-title'), source_ref: value('#c-source'),
                      author: value('#c-author') };
    if (mode) { payload.parent = o.parent; payload.mode = mode; }
    const note = await post('/api/note', payload);
    if (o.corrects) {
      const bind = { a: note.note_id, b: o.corrects, mode: 'inhibits',
                     rationale: 'corrects the note it binds to' };
      await post('/api/link/suggest', bind);
      await post('/api/link/ratify', bind);
    }
    return note;
  }, (note) => {
    /* A note written during a walk is what that walk produced (§11.1) — recorded before the
     * selection below, so `last` is already set and the auto-select cannot also log a visit.
     * Only notes composed here: `staging/` ingest commits notes that were authored before this
     * sitting and merely imported during it, and calling those its produce would misread the
     * walk. That is a judgement, not a limitation — walk.sh produce takes any note id. */
    produced(note.note_id);
    ctx.selectNode(note.note_id, false);
    ctx.centerOn(note.note_id);
    toast(`committed ${note.note_id} — queued for embedding`
      + (walk ? ' · recorded as produced by this walk' : ''));
  });

  panel.querySelector('#c-commit').addEventListener('click', commit);
  panel.querySelector('#c-body').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) commit();
  });
  panel.querySelector('#c-body').focus();
}

/* §6's remedy, one click away. A refused edit is the corpus telling you the change is a new
 * claim rather than a typo, and the thing to do about it is exactly this. */
function driftRemedy(data) {
  const el = document.querySelector('#toast');
  if (!data || !data.note_id || !el) return;
  el.insertAdjacentHTML('beforeend',
    ` ${button('compose', { 'data-corrects': data.note_id }, 'write it as a correction instead')}`);
  bind(el);
}

// ---- edit in place ---------------------------------------------------------
function edit(noteId) {
  const slot = document.querySelector('#d-body');
  if (!slot || slot.querySelector('textarea')) return;
  const before = slot.textContent;
  slot.innerHTML = `<textarea id="e-body" rows="10"></textarea>
    <div class="verdict actions">
      <button type="button" id="e-save" class="on">save</button>
      <button type="button" id="e-cancel">cancel</button>
      <span class="hint">typos and formatting only</span>
    </div>`;
  const area = slot.querySelector('#e-body');
  area.value = before;
  area.focus();
  slot.querySelector('#e-cancel').addEventListener('click', () => { slot.textContent = before; });
  slot.querySelector('#e-save').addEventListener('click', () => run(
    () => post(`/api/note/${encodeURIComponent(noteId)}/correct`, { body: area.value }),
    (r) => {
      ctx.selectNode(noteId, false);
      toast(r.op_id ? 'corrected' : 'unchanged — nothing to correct, so nothing was logged');
    }));
}

// ---- the ratification queue, and the other five moves ----------------------
/* Only move 1 (semantically near, graph-far) ever writes a SUGGEST_LINK on its own (spec
 * §8.2). Move 2 (bridge candidates) proposes an actual link too, in the sense that a bridge
 * note is exactly the kind of thing worth `link.sh suggest`-ing by hand, so it shares the top
 * section with move 1 rather than joining the read-only ones below — but nothing stages it
 * automatically. Moves 3, 4 and 6 are write-a-note prompts (a theme, a tension, a debt) and
 * move 5 is a note to revisit, not a link to decide; none of the four has a verdict to
 * render. They still belong in this panel — it is where a reader comes looking for what the
 * corpus wants next — so they render read-only, straight off /api/provocations (the same read
 * the nightly digest does, on demand: scripts/provocation_digest.py build()).
 *
 * .note-link buttons are wired for free by app.js's openPanel (the same shape the detail
 * panel and status.js use) — no listener needed here, clicking one just selects the note. */
const noteChip = (id, title) => `<button type="button" class="note-link"
  data-id="${ctx.esc(id)}">${ctx.esc(title || ctx.labelOf(id))}</button>`;

function bridgeRows(cands) {
  return cands.map((c) => `<div class="qrow">
    <p class="rationale">spans ${c.spans} communities, degree ${c.degree} — a candidate bridge,
      not yet suggested as a link</p>
    ${noteChip(c.id, c.title)}</div>`).join('');
}

function themeRows(themes) {
  return themes.map((t) => `<div class="qrow">
    <p class="rationale">an unnamed theme running through ${t.size} notes — write a hub/structure
      note?</p>
    ${t.members.map((m) => noteChip(m.id, m.title)).join('')}</div>`).join('');
}

function contradictionRows(pairs) {
  return pairs.map((p) => `<div class="qrow">
    ${noteChip(p.new, p.new_title)} <span class="muted">corrects</span> ${noteChip(p.old, p.old_title)}
    ${p.reason ? `<p class="rationale">${ctx.esc(p.reason)}</p>` : ''}</div>`).join('');
}

function debtRows(items) {
  return items.map((r) => `<div class="qrow">
    <p class="rationale">${ctx.esc(r.prompt)}</p>
    ${noteChip(r.id, r.label)}</div>`).join('');
}

function revisitRows(m5) {
  const bucket = (label, items) => (items.length
    ? `<p class="hint">${label}</p>${items.map((c) => noteChip(c.id, c.title)).join('')}` : '');
  return bucket('orphans — no ratified link yet', m5.orphans)
    + bucket('inhibited — corrected, still re-encounterable', m5.inhibited)
    + bucket('on this day', m5.on_this_day);
}

async function queue() {
  const [linkRes, provRes] = await Promise.all([
    fetch('/api/links?status=suggested'), fetch('/api/provocations'),
  ]);
  const data = linkRes.ok ? await linkRes.json() : { links: [] };
  const prov = provRes.ok ? await provRes.json() : null;
  const label = (id, title) => ctx.esc(title || ctx.labelOf(id));
  const rows = data.links.map((l) => `<div class="qrow">
    <button type="button" class="claim link" data-a="${ctx.esc(l.a)}" data-b="${ctx.esc(l.b)}"
      title="show these two notes in the graph">${label(l.a, l.a_title)}
      <span class="muted">→</span> ${label(l.b, l.b_title)}</button>
    ${l.rationale ? `<p class="rationale">${ctx.esc(l.rationale)}</p>` : ''}
    <div class="verdict">
      ${MODES.map(([mode, verb, why]) => button(
        'ratify', { 'data-a': l.a, 'data-b': l.b, 'data-mode': mode }, verb, why)).join('')}
      ${button('reject', { 'data-a': l.a, 'data-b': l.b }, 'reject')}
    </div></div>`);

  const bridgeBlock = prov ? bridgeRows(prov.move2) : '';
  const writeRows = prov ? themeRows(prov.move3) + contradictionRows(prov.move4)
    + debtRows(prov.move6) : '';
  const revisitBlock = prov ? revisitRows(prov.move5) : '';

  ctx.openPanel(`
    <button type="button" class="close" title="Close (Esc)">×</button>
    <div class="kind">ratification queue</div>
    <h2>${data.links.length} suggested bind${data.links.length === 1 ? '' : 's'}</h2>
    <p class="hint">near-but-graph-far suggestions (move 1) await a verdict below; bridge
      candidates (move 2) are read-only until you suggest one yourself</p>
    ${rows.length ? rows.join('') : ''}${bridgeBlock}
    ${rows.length || bridgeBlock ? '' : '<p class="muted">Nothing waiting. Suggestions come from '
      + 'the provocation digest, and from the "near in meaning, not linked" list on any note.</p>'}

    <h3>notes to write</h3>
    <p class="hint">implicit themes, tensions to reconcile, and structural debt (moves 3, 4 &amp; 6)
      — each names a site to write from, never a claim to make</p>
    ${writeRows || '<p class="muted">Nothing surfaced right now.</p>'}

    <h3>suggested revisits</h3>
    <p class="hint">orphaned, corrected, and anniversary notes (move 5)</p>
    ${revisitBlock || '<p class="muted">Nothing surfaced right now.</p>'}

    <footer>the machine proposes, you dispose (spec §8.2) — a verdict on an
      <strong>inhibits</strong> bind asserts that the first note corrects the second</footer>`, 'queue');

  // The graph is what "explored directly" means here — highlight in place rather than routing
  // through showEdge, so the ratify/reject buttons below stay put while the reader looks around.
  document.querySelector('#panel').querySelectorAll('.claim.link').forEach((el) => {
    el.addEventListener('click', () => {
      const r = ctx.highlightEdgeBetween(el.dataset.a, el.dataset.b);
      if (!r.ok) { toast('that bind is gone — the queue may be stale', 'bad'); return; }
      if (!r.visible) toast('highlighted, but outside the current view — widen the window or '
        + 'enable "suggested" under binds to see it');
    });
  });
}

// ---- dropping files into the two inboxes -----------------------------------
/* Uploads are the one thing in this file that writes without writing to the graph. Parking a
 * file adds no note, no edge and no Op — `ingest-staging` is still the act that commits a typed
 * note, and a photograph still waits for a person to read it. Hence runQuiet, and hence the
 * panel re-rendering itself rather than the corpus being re-read.
 *
 * The server routes on the extension, not on which zone caught the file. The two zones say what
 * each inbox is *for*; neither refuses anything, because a photo dropped on the typed one is
 * still a photo and telling somebody they aimed badly is not help.
 */
const MAX_UPLOAD = 16 * 1024 * 1024;   // scripts/ui.py UPLOAD_MAX_BYTES

const kb = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.ceil(n / 1024)} KB`);

/* readAsDataURL and slice past the comma — NOT btoa(String.fromCharCode(...bytes)), whose
 * argument spread overflows the stack somewhere around a megabyte, which is under every size
 * this route exists to accept. */
function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const s = String(reader.result || '');
      resolve(s.slice(s.indexOf(',') + 1));
    };
    reader.onerror = () => reject(new Error(`could not read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

/* One file at a time inside one envelope. Five parallel `runQuiet` calls would upload the first
 * and silently discard four, because the envelope is single-flight and returns nothing when it
 * is busy. A file that fails is recorded and the batch carries on: one unreadable drop should
 * not strand the four good ones behind it. */
async function uploadAll(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  await runQuiet(async () => {
    const out = [];
    for (const file of files) {
      if (file.size > MAX_UPLOAD) {
        out.push({ name: file.name, error: `${kb(file.size)} is over the ${kb(MAX_UPLOAD)} cap` });
        continue;
      }
      try {
        out.push(await post('/api/upload', { name: file.name, data: await readBase64(file) }));
      } catch (err) {
        out.push({ name: file.name, error: err.message });
      }
    }
    return out;
  }, (rows) => {
    const bad = rows.filter((r) => r.error);
    const typed = rows.filter((r) => r.kind === 'typed').length;
    const scanned = rows.filter((r) => r.kind === 'scan').length;
    const said = [];
    if (typed) said.push(`${typed} to staging/`);
    if (scanned) said.push(`${scanned} to staging/scans/`);
    if (bad.length) said.push(`${bad.length} refused — ${bad[0].error}`);
    toast(said.join(' · '), bad.length ? 'bad' : '');
    staging();
  });
}

function wireDrops(root) {
  root.querySelectorAll('.dropzone').forEach((zone) => {
    const picker = zone.querySelector('input[type=file]');
    zone.addEventListener('click', () => picker.click());
    picker.addEventListener('change', () => {
      uploadAll(picker.files);
      picker.value = '';        // so dropping the same file twice fires `change` twice
    });
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('over'));
    zone.addEventListener('drop', (e) => {
      e.preventDefault();
      zone.classList.remove('over');
      uploadAll(e.dataTransfer && e.dataTransfer.files);
    });
  });
}

// ---- the staging drop-box --------------------------------------------------
const dropzone = (title, hint) => `<div class="dropzone">
  <strong>${title}</strong><span class="hint">${hint}</span>
  <input type="file" multiple hidden></div>`;

async function staging() {
  const res = await fetch('/api/staging');
  const data = res.ok ? await res.json() : { files: [], dir: '', scans: [], scans_dir: '' };
  const scans = data.scans || [];
  const rows = data.files.map((f) => `<div class="qrow">
    <p class="claim ${f.ok ? '' : 'bad'}">
      <span class="mono">${ctx.esc(f.name)}</span>
      ${f.title ? `<br>${ctx.esc(f.title)}` : ''}
      ${f.begets ? `<br><span class="muted">${ctx.esc(f.begets[1])} ${ctx.esc(f.begets[0])}</span>`
        : ''}</p>
    ${f.ok ? button('ingest', { 'data-name': f.name }, 'ingest')
      : `<p class="rationale">${ctx.esc(f.error)}</p>`}</div>`);

  // "all" means all, including the ones that will fail — they belong in staging/failed/, which
  // is where you go to find out why, and leaving them in the drop-box helps nobody.
  const all = data.files.length > 1
    ? `<div class="verdict actions">${button('ingest', {}, `ingest all ${data.files.length}`,
        'anything that fails moves to staging/failed/ with its reason')}</div>` : '';

  // No ingest button on a scan, and that absence is the honest part: turning handwriting into
  // notes needs a person and a model reading the page together, with a ratification step in the
  // middle. The panel says where to do that rather than offering a button that cannot.
  const scanRows = scans.map((s) => `<div class="qrow">
    <p class="claim"><span class="mono">${ctx.esc(s.name)}</span>
      <br><span class="muted">${kb(s.bytes)} · ${ctx.esc(s.modified)}</span></p></div>`);

  ctx.openPanel(`
    <button type="button" class="close" title="Close (Esc)">×</button>
    <div class="kind">inbox</div>
    <h2>${data.files.length} typed · ${scans.length} handwritten</h2>
    <div class="drops">
      ${dropzone('typed', '.md .txt .note → staging/')}
      ${dropzone('handwritten', '.jpg .png .pdf → staging/scans/')}
    </div>
    <p class="hint">Drop or click. A file already named for a note id keeps it; anything else is
      given a fresh one and the name it arrived as becomes its <span class="mono">source_ref</span>.</p>

    <h3>typed — ${ctx.esc(data.dir)}</h3>
    ${all}
    ${rows.length ? rows.join('')
      : '<p class="muted">Nothing waiting. A dropped file may carry <span class="mono">key: '
        + 'value</span> lines and a lone <span class="mono">---</span> before the body; plain '
        + 'prose is a note too.</p>'}

    <h3>handwritten — ${ctx.esc(data.scans_dir)}</h3>
    ${scanRows.length ? scanRows.join('')
      : '<p class="muted">Nothing waiting.</p>'}
    ${scans.length ? '<p class="hint">Parked, not read. Start a Claude session and invoke the '
      + '<span class="mono">transcribe-notes</span> skill — it transcribes verbatim, asks you to '
      + 'ratify each note, and writes them into staging/.</p>' : ''}

    <footer>ingest is <span class="mono">ingest-staging.sh</span>, same batch and same rules; each
      file lands in processed/ or failed/. A dropped file is not yet a note — nothing reaches the
      graph until it is ingested</footer>`, 'staging');

  wireDrops(document.querySelector('#panel'));
}

// ---- walks (spec §11.1, §13) -----------------------------------------------
/* A walk is the reader moving through the corpus, recorded: the notes visited, and the notes the
 * sitting produced. It is the ONLY thing in this system permitted to move `Note.visited` (§13.2),
 * and that permission is what the whole button is arranged around.
 *
 * Browsing is still not walking. What keeps that true now that the page can record one is that a
 * visit needs a walk id, a walk id exists only because somebody pressed `walk`, and until they do,
 * selecting notes writes nothing whatsoever. The mode is explicit, it is visible in three channels
 * at once (label, colour, `aria-pressed`), and it ends only when pressed again. A counter that
 * moved on a page view would measure neither human nor machine; a counter that moves only inside a
 * declared sitting measures exactly the thing §13.2 wants measured.
 *
 * These writes deliberately do NOT go through `envelope`. That guard is single-flight because a
 * graph write ends in a full re-read of the corpus, and two of those racing would leave the page
 * showing whichever finished second — but a visit re-reads nothing, and a reader mid-walk clicks
 * faster than a round trip. Under `envelope` the second click of a pair would be silently dropped,
 * which is the one failure a recorder must not have. So walk writes get their own promise chain:
 * serialized (the trail's order is part of what it records, and the server assigns `seq` from what
 * it has already committed) but never blocking, and never dimming the panel.
 */
let walk = null;        // {id, visited: Set, produced: n} while a run is open; null otherwise
let armed = null;       // the selected note a walk would begin at — null greys the button out
let last = null;        // the note the trail is already on; see visit()
let chain = Promise.resolve();

function enqueue(work) {
  chain = chain.then(work).catch((err) => {
    toast(`walk: ${err.message}`, 'bad');
    // 400 is the server saying it does not know this walk — it was deleted, or the id never
    // existed. Going on would be recording into nothing while showing a red button that claims
    // otherwise, so the mode ends here and says why.
    if (err.status === 400 && walk) { walk = null; last = null; paintWalk(); }
  });
  return chain;
}

/* Three channels, never colour alone: the label says what pressing it does, `aria-pressed` says
 * whether the mode is on, and the colour is the glanceable third. The count rides in the label
 * because a recorder with no visible tally is indistinguishable from one that has quietly
 * stopped — every visit that lands moves a number the reader can see. */
function paintWalk() {
  const b = document.querySelector('#walk');
  if (!b) return;
  const running = !!walk;
  b.disabled = !running && !armed;
  b.classList.toggle('walking', running);
  b.classList.toggle('armed', !running && !!armed);
  b.setAttribute('aria-pressed', String(running));
  b.textContent = running ? `save walk · ${walk.visited.size}` : 'walk';
  b.title = running
    ? `close this walk — ${walk.visited.size} note(s) visited, ${walk.produced} produced`
    : armed ? `begin a walk from ${ctx.labelOf(armed)}`
      : 'select a note to begin a walk from it';
}

/* Called by app.js on every change of the selected note — and only the *note*: selecting an edge
 * or a community focuses nothing, and neither arms the button nor records a visit, because neither
 * is a note somebody read. */
function onSelection(noteId) {
  armed = noteId;
  if (walk && noteId) visit(noteId);
  paintWalk();
}

/* `last` skips the note the trail is already standing on, and it earns its keep twice. Re-selecting
 * what you are already looking at is not a move through the graph; and it is what stops the
 * auto-select that follows a commit from also logging a visit, since PRODUCE explicitly counts as
 * no visit at all — writing a note is not reading one (§13). Coming back to a note later still
 * records, because `last` has moved on by then; the server counts it once per walk regardless
 * (WalkManager._record_visit), so the trail stays honest either way. */
function visit(noteId) {
  if (!walk || noteId === last) return;
  const id = walk.id;
  last = noteId;
  enqueue(async () => {
    await post('/api/walk/visit', { walk: id, note: noteId });
    if (walk && walk.id === id) { walk.visited.add(noteId); paintWalk(); }
  });
}

function produced(noteId) {
  if (!walk || !noteId) return;
  const id = walk.id;
  last = noteId;                 // the select that follows a commit is not a reading of it
  enqueue(async () => {
    await post('/api/walk/produce', { walk: id, note: noteId });
    if (walk && walk.id === id) { walk.produced += 1; paintWalk(); }
  });
}

function startWalk() {
  const seed = armed;
  if (!seed || walk) return;
  enqueue(async () => {
    const r = await post('/api/walk/start', { seed });
    walk = { id: r.walk_id, visited: new Set([seed]), produced: 0 };
    last = seed;                 // START_WALK visits the seed itself, at seq 0
    paintWalk();
    toast(`walk started at ${ctx.labelOf(seed)} — every note you select now counts as a visit`);
  });
}

/* The mode ends the moment it is pressed, before the save lands. Anything else would go on
 * recording into a walk the reader has just said they are done with. */
function endWalk() {
  const w = walk;
  if (!w) return;
  walk = null;
  last = null;
  paintWalk();
  enqueue(async () => {
    await post('/api/walk/save', { walk: w.id });
    toast(`walk saved — ${w.visited.size} visited, ${w.produced} produced ·`
      + ` analytics.sh walk ${w.id}`);
    // The only walk write that re-reads: `visited` is on every node in the payload and inside
    // fitness, so the map catches up once, at the end of the sitting, rather than redrawing
    // itself under a reader on every click.
    await ctx.refresh();
  });
}

/* A walk lives in the Op log and nowhere else, so a reload has to ask the log whether one is still
 * open — otherwise refreshing the page would orphan it, recording nothing and never closing. This
 * also picks up a walk opened from `walk.sh`, which is not a quirk: it is the same person's
 * unclosed sitting, and being able to close it is the useful thing to offer. */
async function resumeWalk() {
  try {
    const res = await fetch('/api/walk');
    if (!res.ok) return;         // an older server that has no such route: nothing to resume
    const open = (await res.json()).walk;
    if (!open || !open.id) return;
    walk = { id: open.id,
             visited: new Set((open.visited || []).map((v) => v.note_id).filter(Boolean)),
             produced: (open.produced || []).length };
    paintWalk();
    toast(`a walk is still open — ${walk.visited.size} visited so far. Selecting notes goes on`
      + ' recording into it; press "save walk" to close it.');
  } catch (err) { /* no route, or no server — the button simply stays grey */ }
}

// ---- wiring ----------------------------------------------------------------
const ACTIONS = {
  compose: (d) => compose({ parent: d.parent, mode: d.mode, corrects: d.corrects }),
  edit: (d) => edit(d.id),
  // One button, two verbs, decided by the mode it is currently in — which is the whole design:
  // there is never a moment where both "start" and "save" are available to press.
  walk: () => (walk ? endWalk() : startWalk()),
  queue: () => queue(),
  staging: () => staging(),
  link: () => ctx.openLink(),
  /* All four land through `ctx.reselect` rather than selecting an endpoint outright. Everywhere
   * but the link panel that is the same thing it always was — the note, reselected against the
   * corpus the write just re-read. In the link panel it is the difference between the pair
   * surviving its own verdict and one of its slots emptying at the moment the bind appeared. */
  suggest: (d) => run(() => post('/api/link/suggest', { a: d.a, b: d.b }),
                      () => { ctx.reselect(d.a); toast('suggested — it is in the queue'); }),
  // `data-mode` is dropped from the markup when the mode is null (see attrs), so undefined here
  // means untyped — which is a verdict, not an omission, and the server tells the two apart on
  // whether the key is present at all (State.link). Hence the explicit null.
  ratify: (d) => run(
    () => post('/api/link/ratify', { a: d.a, b: d.b, mode: d.mode === undefined ? null : d.mode }),
    (r) => { ctx.reselect(r.a); toast(`ratified as ${r.mode || 'untyped'}`); }),
  retype: (d) => run(
    () => post('/api/link/retype', { a: d.a, b: d.b, mode: d.mode === undefined ? null : d.mode }),
    (r) => { ctx.reselect(r.a); toast(`now reads as ${r.mode || 'untyped'}`); }),
  reject: (d) => run(() => post('/api/link/reject', { a: d.a, b: d.b }),
                     () => { ctx.reselect(d.a); toast('rejected — the bind is gone'); }),
  ingest: (d) => run(
    () => post('/api/staging/ingest', d.name ? { names: [d.name] } : {}),
    (r) => {
      const good = r.files.filter((f) => f.ok).length;
      toast(`ingested ${good} of ${r.files.length}`, good === r.files.length ? '' : 'bad');
      staging();
    }),
};

/* Wire every write affordance under `root`. Called once per panel render, and on the toast when
 * a refused edit offers its remedy. `data-mode` is absent for an untyped ratify — and absent is
 * not the same as null on the server (see State.link), so it is sent explicitly. */
function bind(root) {
  root.querySelectorAll('[data-write]').forEach((el) => {
    const action = ACTIONS[el.dataset.write];
    if (action) el.addEventListener('click', () => action(el.dataset));
  });
}

export const Write = {
  get on() { return on; },

  /* Ask the server whether it takes writes at all, and borrow the view's helpers. A server
   * started --read-only says so here, and every affordance below then renders as nothing —
   * better than offering a button that answers 403. */
  async init(context) {
    ctx = context;
    // A file dropped anywhere but a zone would otherwise navigate the tab to it, losing the graph
    // and every position dragged into place. Refused everywhere, so a near miss is a no-op — and
    // refused even on a --read-only server, where losing the view is no less annoying.
    ['dragover', 'drop'].forEach((evt) =>
      document.addEventListener(evt, (e) => e.preventDefault()));
    try {
      const res = await fetch('/api/health');
      on = res.ok && (await res.json()).writes === true;
    } catch (err) {
      on = false;
    }
    document.querySelectorAll('[data-write-ui]').forEach((el) => { el.hidden = !on; });
    if (on) {
      bind(document.querySelector('#bar'));
      await resumeWalk();      // before the first selection lands, so a resumed walk records it
    }
    paintWalk();
    return on;
  },

  bind,
  toast,
  compose,
  suggestButton,
  bindActions,
  editButton,
  composeFrom,
  onSelection,
  /* Whether a run is open, for anything that needs to say so. Read-only on purpose: the mode is
   * started and ended by the button and by nothing else. */
  get walking() { return !!walk; },
};
