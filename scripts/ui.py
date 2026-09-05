#!/usr/bin/env python3
"""ui — a graph view of the corpus you can also act on (spec §11.3 mod 6, §7, §13).

    ui.sh start | stop | status         # the server as a daemon
    ui.sh run [--port N]                # the server in the foreground (debugging)
    ui.sh run --read-only               # the pre-v0.8.1 surface: reads only, every write 403
    ui.sh run --tailscale               # reachable from the tailnet, HTTPS (see scripts/tailscale.py)
    ui.sh run --snapshot [--json]       # the graph payload, no server at all
    ui.sh run --note <id> [--json]      # one note's detail payload

Serves a small static page that draws the graph, and the JSON it reads and writes:

    GET  /                              the page
    GET  /ui/<file>                     its assets
    GET  /api/graph[?days&since&until&as_of&refresh]
    GET  /api/note/<id>
    GET  /api/search[?q&field&author&since&until&limit]   lexical, never semantic
    GET  /api/links[?status&limit]      the ratification queue
    GET  /api/provocations[?refresh]    moves 2-6, read live (§8.1) — move 1 is /api/links
    GET  /api/staging                   what is waiting in staging/ (parsed) and staging/scans/ (listed)
    GET  /api/status                    the maintenance clock, the daemons, and the measurements
    GET  /api/walk                      the walk currently open, if any
    GET  /api/health
    POST /api/note                      ADD_NOTE
    POST /api/note/<id>/correct         CORRECT_COSMETIC
    POST /api/link/<action>             SUGGEST_LINK | RATIFY_LINK | RETYPE_LINK | REJECT_LINK
    POST /api/walk/<action>             START_WALK | VISIT | PRODUCE | SAVE_WALK
    POST /api/staging/ingest            ADD_NOTE, in a batch
    POST /api/upload                    a file into staging/ or staging/scans/ — no Op, see State.upload

**This module composes writes; it never composes SQL.** Every mutation above is a call into
notelib's Ingestor, LinkManager or WalkManager — the three doors to the graph, and the only three
— each of which runs the write and the `Op` that records it inside one transaction, so nothing can
reach the graph from here without the log entry that says it did (§12.3). That is the guarantee
that replaced "the server speaks only GET", and it is a stronger one: a raw `db.command` call
still does not appear anywhere in this file, and the tests grep the source to keep it that way.

Two things the view still must not do:

  * **never move Note.visited except inside a walk the reader explicitly started** (§13.2, and
    see State.walk). The rule was never "the view may not count attention" — it is that only a
    *recorded walk* may, because a number both a human and a daemon could increment would
    measure neither. Browsing is still not walking, and the whole weight of that distinction now
    rests on one thing: `visited` moves only through WalkManager, only while a walk is open, and
    a walk is only ever opened by someone pressing the button that says so. Committing a note is
    not walking it either — PRODUCE deliberately counts no visit.
  * **never query the vector index live.** Adding one embedding invalidates the whole
    LSM_VECTOR graph, so the next live query rebuilds every vector in the corpus (~11 min at
    N=101). Behind a mouse click that is not slow, it is a hang. Semantic neighbours come out
    of KnnCache or not at all, and nothing here embeds inline except the one write whose whole
    purpose is to measure an embedding (see State.correct).

The browser never talks to ArcadeDB. It could not do so safely — the DB authenticates with the
root password, which would have to be shipped to the client — and it should not do so anyway:
label, fitness, community and folgezettel are defined here in Python, and a second definition in
JavaScript would drift from this one silently.

Interface, not fidelity (§11.3 mod 6): what goes over the wire is a projection chosen for
thinking-fitness. Note bodies are deliberately NOT in the graph payload — a body per node is
~5 MB at 10k notes to render dots nobody can read — so the graph carries a label and the body
arrives on click.
"""
import argparse
import json
import os
import ssl
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import inbox  # noqa: E402  (the two drop zones, shared with nothing else — see State.upload)
import ingest_staging  # noqa: E402  (the staging batch, shared with ingest-staging.sh)
import notelib  # noqa: E402  (import needs the path above)
import scheduler  # noqa: E402  (its JOBS table and cadence arithmetic; main() is __main__-guarded)
import tailscale  # noqa: E402  (--tailscale: MagicDNS IP + cert provisioning)
from analytics import autocatalysis, common, criticality, debt, fitness, walks  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.realpath(os.path.join(REPO, "ui"))
TAILSCALE_CERT_DIR = os.path.join(REPO, "certs", "tailscale")

CONTRACT_VERSION = 2     # bumped when the payload shape changes in a way the page must notice.
                         # 2: the write surface. The page checks it and says so, rather than a
                         # tab left open across an upgrade POSTing against a shape that moved.
DEFAULT_WINDOW_DAYS = 182   # ~6 months: the default reading window, long enough that a line of
                            # thought is still whole and short enough to be a working set
DEFAULT_PORT = 8420      # fixed by default because localStorage (dragged positions) is per-origin
BIND_HOST = "127.0.0.1"  # loopback by default — this serves note bodies (and accepts writes)
                         # over plain HTTP unless --tailscale opts into a wider bind + TLS
                         # (README security)
SNAPSHOT_TTL = 60.0      # seconds; a personal corpus changes on human timescales, and every
                         # rebuild is five whole-table scans
KNN_SHOW = 8             # semantic neighbours in the detail panel, off the nightly cache

# The search panel. `field` names which text column the query runs against; the names are
# lexical_search's own keywords, so the panel and `search.sh` are asking the same question in the
# same words rather than in two translations of it. "text" (title OR body) is the default because
# it is the one people mean by "search".
SEARCH_FIELDS = ("text", "title", "body", "source")
SEARCH_LIMIT = 50        # rows per search; the panel is a locator, not a second view of the corpus
SEARCH_MAX = 200

WRITE_HEADER = "X-Indexia-Write"   # see Handler._guard — its absence from a cross-origin request
                                   # is what refuses the request
MAX_BODY = 1 << 20       # 1 MiB — a note is prose somebody typed, and prose does not weigh this
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
# Always allowed. --tailscale adds exactly one more name at runtime — the provisioned MagicDNS
# FQDN, on Handler.allowed_host — never a wildcard; see Handler._host_allowed.
LINK_ACTIONS = ("suggest", "ratify", "retype", "reject")
# The four a button can reach, and since v0.8.3 that is four of the five a walk has: `fork` and
# `delete` are decisions about a walk you are no longer taking, and belong wherever past walks are
# listed — which is nowhere in this view yet. They stay on walk.sh.
WALK_ACTIONS = ("start", "visit", "produce", "save")

# The one route that really is a file upload, and the only one whose cap is raised for it. A
# photograph of a notecard is megabytes, so the cap moves — but it moves on one path rather than
# on MAX_BODY, because every other route still takes prose and should still say so at 1 MiB.
UPLOAD_PATH = "/api/upload"
UPLOAD_MAX_BYTES = inbox.MAX_BYTES   # 16 MiB, decoded — the size of the actual file
UPLOAD_MAX_WIRE = 24 << 20           # base64 costs 4/3 of that, and the JSON around it a little more

# ---- the status panel -------------------------------------------------------
# Where the daemons leave their pid files and the scheduler its log. Read straight rather than
# through scripts/lib.sh, which is shell — but the names have to match it, so a daemon started by
# scheduler.sh is the one this reports on. INDEXIA_SCHEDULER_LOG is exported by scheduler.sh and
# not by ui.sh, so a customized log path is invisible here; the panel prints what it read.
RUN_DIR = os.environ.get("INDEXIA_RUN_DIR") or os.path.join(os.path.expanduser("~"), ".indexia")
SCHEDULER_LOG = os.environ.get("INDEXIA_SCHEDULER_LOG") or os.path.join(RUN_DIR, "scheduler.log")
DAEMONS = (("scheduler", "scripts/scheduler.py"),
           ("embed-worker", "scripts/embed_worker.py"),
           ("ui", "scripts/ui.py"))
LOG_TAIL = 40            # lines of scheduler log the panel shows
LOG_TAIL_BYTES = 64 << 10   # how far back to seek for them; the log reached 726 KB before rotation
FITNESS_SHOW = 10
ATTENTION_SHOW = 10
PROVOKE_SHOW = 5         # candidates per move in the queue panel's read-only sections


class ReadOnly(RuntimeError):
    """Raised when a write arrives at a server started with --read-only."""


# ---- timestamps (spec §4) ---------------------------------------------------
def epoch_ms(value):
    """Epoch milliseconds from any timestamp-ish value — a compact note id or a round-tripped
    DATETIME — or None when it carries no parseable instant, which is how a null created_at reads.

    Computed here and never in the browser. A DATETIME round-trips space-separated with no 'Z'
    ('2026-07-23 13:42:10.130') while an id is '20260723T115735449Z', so the wire carries a plain
    number and the format trap stays on this side of it.
    """
    digits = notelib._dt_digits(value)
    if len(digits) < 14:
        return None
    born = datetime.strptime(digits[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return int(born.timestamp()) * 1000 + int(digits[14:17].ljust(3, "0") or 0)


def _day_ms(day, what, plus_days=0):
    """Epoch millis of midnight UTC on a YYYY-MM-DD day, optionally shifted forward."""
    if not notelib.DAY_RE.match(day or ""):
        raise ValueError(f"--{what} must be a date as YYYY-MM-DD (got {day!r})")
    midnight = (datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                + timedelta(days=plus_days))
    return int(midnight.timestamp()) * 1000


def window_bounds(days=DEFAULT_WINDOW_DAYS, since=None, until=None):
    """(since, until, lo_ms, hi_ms) for the reading window.

    `since`/`until` are YYYY-MM-DD and both **inclusive of the day named**, matching
    notelib.lexical_search — since=D until=D is all of day D. Given neither, the window is the
    last `days` days; `days=None` means no lower bound at all.

    The comparison runs against each note's id, because the id IS the creation instant (§4) and
    created_at is READONLY-derived from it — which keeps the window clear of DATETIME entirely.
    When the corpus outgrows a single payload this is the filter that moves into SQL, as
    `created_at >= date(:since)`: exact and index-backed on a vertex (tests/test_search_dates.py
    pins it), unlike the same predicate over an edge property (ddl/schema.sql §144-176).
    """
    if since is None and days is not None:
        since = (notelib.now_utc() - timedelta(days=int(days))).strftime("%Y-%m-%d")
    return (since, until,
            _day_ms(since, "since") if since else None,
            _day_ms(until, "until", plus_days=1) if until else None)


# ---- the graph snapshot (spec §12.2) ----------------------------------------
# Corpus gives us the whole graph in three queries plus every metric for free, but it drops two
# things a *view* needs and an analytic does not: BEGETS.mode (continues vs branches) and
# BINDS.rationale. Widening it would ripple through adjacency, catalysis_edges, descendants and
# the criticality measures, all of which unpack 2-tuples — growing the shared read so a viewer
# can style an arrow is the wrong direction. So the two edge lists are re-read here, filtered by
# the same rule Corpus applies. Five whole-table scans per snapshot, all read-only. (The same
# call scripts/search.py:69 makes about --inhibited: filter in Python, say why.)
BEGETS_SQL = "SELECT outV().id AS a, inV().id AS b, mode, created_at FROM BEGETS"
BINDS_SQL = ("SELECT outV().id AS a, inV().id AS b, mode, status, created_at, rationale "
             "FROM BINDS")


def _edge_rows(db, sql, ids, cutoff):
    """Edge rows whose endpoints both survive the as-of cutoff, and which were drawn before it.

    An edge with a null created_at predates the property: kept in the present-day view (the graph
    plainly has them), dropped from an as-of view, exactly as Corpus._read_edges does. Note there
    is no WHERE on a date here and never can be — a null DATETIME *matches* `< date(...)` on this
    build, and the row counts come back wrong (ddl/schema.sql §144-176).
    """
    out = []
    for r in notelib.rows(db.query(sql)):
        a, b = r.get("a"), r.get("b")
        if not (a and b) or a not in ids or b not in ids:
            continue
        if cutoff:
            drawn = r.get("created_at")
            if not drawn or notelib._dt_digits(drawn) > cutoff:
                continue
        out.append(r)
    return out


def _edge_id(prefix, a, b, seen):
    """A stable id for an edge: '{b|d}:{out}>{in}', suffixed only on the rare duplicate pair.

    Stability is what lets a selection survive a refilter or a refresh, so it is a contract, not
    a convenience. Read order is deterministic (one query, no LIMIT), so the suffix is too.
    """
    base = f"{prefix}:{a}>{b}"
    n = seen.get(base, 0)
    seen[base] = n + 1
    return base if not n else f"{base}#{n}"


def snapshot(db, days=DEFAULT_WINDOW_DAYS, since=None, until=None, as_of=None):
    """The whole graph as one renderer-agnostic payload: {version, window, counts, corpus,
    communities, nodes, edges}.

    **Every metric is corpus-wide, always** — fitness, community, degree and the orphan flag are
    computed over the entire graph and never over the window. A note's community does not change
    because you narrowed a date range, and a community detected on a windowed subgraph would
    silently disagree with `analytics.sh communities`; the view would then be lying about the
    corpus. The window is a visibility flag on top (`in_window`), nothing more.

    The payload is the whole corpus, so the page can refilter without asking again. At ~100 notes
    that is a few tens of kilobytes. `since`/`until` set the window the payload reports; they do
    not trim it. Trimming is the crossover change (see window_bounds), not the v1 behaviour.
    """
    corpus = common.Corpus(db, as_of=as_of)
    cutoff = notelib._dt_digits(as_of) if as_of else None
    since, until, lo, hi = window_bounds(days=days, since=since, until=until)

    begets = _edge_rows(db, BEGETS_SQL, corpus.ids, cutoff)
    binds = _edge_rows(db, BINDS_SQL, corpus.ids, cutoff)

    scores = {r["id"]: r for r in fitness.report(corpus)}
    clusters = autocatalysis.report(corpus)
    crit = criticality.report(corpus)
    community_of = {m: i for i, c in enumerate(clusters) for m in c["members"]}

    # Orphan means what `analytics.sh criticality` means by it — touched by no ratified BINDS.
    # Not "has no edges at all": a note deep in a lineage is not unlinked, it is un-associated,
    # and those are different readings. Kept identical so the two never disagree.
    bound = set()
    for a, b in corpus.ratified():
        bound.add(a)
        bound.add(b)

    degree = {}
    for r in begets + binds:
        degree[r["a"]] = degree.get(r["a"], 0) + 1
        degree[r["b"]] = degree.get(r["b"], 0) + 1

    nodes = []
    for nid in sorted(corpus.notes):
        note = corpus.notes[nid]
        t = epoch_ms(nid)
        f = scores.get(nid) or {}
        nodes.append({
            "id": nid,
            "label": corpus.label(nid),            # title, else a 60-char snippet — the canonical
            "title": note.get("title"),            # short label every report already uses
            "status": note.get("status"),
            "t": t,
            "visited": note.get("visited") or 0,
            "fitness": f.get("fitness", 0.0),
            "catalyzes": f.get("catalyzes", 0),
            "inhibits": f.get("inhibits", 0),
            "descendants": f.get("descendants", 0),
            "degree": degree.get(nid, 0),
            "community": community_of.get(nid),
            "orphan": nid not in bound,
            "in_window": (t is not None
                          and (lo is None or t >= lo) and (hi is None or t < hi)),
        })

    seen = {}
    edges = []
    for r in begets:
        edges.append({"id": _edge_id("d", r["a"], r["b"], seen), "type": "begets",
                      "source": r["a"], "target": r["b"], "mode": r.get("mode"),
                      "status": None, "t": epoch_ms(r.get("created_at")), "sign": 0})
    for r in binds:
        mode = r.get("mode")
        edges.append({"id": _edge_id("b", r["a"], r["b"], seen), "type": "binds",
                      "source": r["a"], "target": r["b"], "mode": mode,
                      "status": r.get("status"), "t": epoch_ms(r.get("created_at")),
                      "sign": notelib.bind_sign(mode)})

    return {
        "version": CONTRACT_VERSION,
        "generated_at": notelib.format_id(notelib.now_utc()),
        "as_of": as_of,
        "window": {"since": since, "until": until, "days": days},
        "counts": {"notes": len(nodes),
                   "in_window": sum(1 for n in nodes if n["in_window"]),
                   "begets": len(begets), "binds": len(binds)},
        "corpus": {"mean_degree": crit["mean_degree"], "band": crit["band"],
                   "orphans": crit["orphans"], "communities": len(clusters)},
        "communities": [{"index": i, "size": c["size"], "hub": c["hub"],
                         "hub_label": corpus.label(c["hub"]) if c["hub"] else None,
                         "autocatalytic": c["autocatalytic"]}
                        for i, c in enumerate(clusters)],
        "nodes": nodes,
        "edges": edges,
    }


# ---- one note in full -------------------------------------------------------
NOTE_SQL = ("SELECT id, title, body, created_at, author, source_ref, status, visited "
            "FROM Note WHERE id = :n")
INCIDENT_BINDS_SQL = ("SELECT outV().id AS a, inV().id AS b, mode, status, rationale "
                      "FROM BINDS WHERE outV().id = :n OR inV().id = :n")


def _label(row):
    """The same short label Corpus.label gives, for rows read outside a Corpus."""
    return (row or {}).get("title") or notelib.snippet((row or {}).get("body"), 60)


def note_detail(db, note_id):
    """Everything the side panel shows that the graph payload does not carry: the body itself,
    source_ref, the Folgezettel address, the incident binds with their rationale, and the cached
    semantic neighbours. None when the note does not exist.

    The semantic neighbours come straight out of KnnCache. They are never asked of the vector
    index live: adding any embedding invalidates the whole LSM_VECTOR graph, so the next live
    query rebuilds every vector in the corpus — measured at ~11 minutes for 101 notes. Behind a
    mouse click that is not slow, it is a hang. When the cache has no row for a note, say so and
    stop (§12.6).
    """
    notelib.validate_id(note_id)
    row = notelib.first_row(db.query(NOTE_SQL, {"n": note_id}))
    if not row.get("id"):
        return None

    binds = []
    for r in notelib.rows(db.query(INCIDENT_BINDS_SQL, {"n": note_id})):
        a, b = r.get("a"), r.get("b")
        if not (a and b):
            continue
        binds.append({"other": b if a == note_id else a,
                      "direction": "out" if a == note_id else "in",
                      "mode": r.get("mode"), "status": r.get("status"),
                      "rationale": r.get("rationale")})

    pairs = notelib.knn_cached_neighbors(db, note_id)
    nearest, cached = [], pairs is not None
    if pairs:
        top = [(nid, sc) for nid, sc in pairs if nid != note_id][:KNN_SHOW]
        hydrated = notelib._hydrate_notes(db, [nid for nid, _ in top])
        for nid, score in top:
            if nid in hydrated:
                nearest.append({"id": nid, "label": _label(hydrated[nid]),
                                "score": round(score, 6)})

    return {"id": row["id"], "title": row.get("title"), "body": row.get("body"),
            "created_at": row.get("created_at"), "author": row.get("author"),
            "source_ref": row.get("source_ref"), "status": row.get("status"),
            "visited": row.get("visited") or 0,
            "folgezettel": notelib.folgezettel(db, note_id),
            "binds": binds, "nearest": nearest, "knn_cached": cached}


# ---- the machinery, watched from outside it --------------------------------
def daemon_state(name, script):
    """{name, script, state, pid} for one daemon, from its pid file alone.

    Mirrors `daemon_pid` in scripts/lib.sh minus the two things it does that a read may not: it
    deletes a pid file it finds stale, and it falls back to a `pgrep` whose result it then
    *writes*. A GET has to leave the filesystem exactly as it found it (tests/test_ui_readonly.py
    checks the pid files are untouched), so this only ever looks.

    Losing that fallback is why 'unknown' exists here. Without the scan, a pid file that has gone
    stale cannot be told apart from a daemon running under a pid the file never learned, and
    'down' would be a claim this cannot support. `ui` is the exception and reports itself up by
    construction: it is the process answering the request.
    """
    out = {"name": name, "script": script, "state": "down", "pid": None}
    if script.endswith("ui.py"):
        return dict(out, state="up", pid=os.getpid())
    try:
        with open(os.path.join(RUN_DIR, f"{name}.pid"), encoding="utf-8") as fh:
            pid = int((fh.readline() or "").strip())
    except (OSError, ValueError):
        return out                       # no pid file: nothing has ever claimed to be running
    out["pid"] = pid
    try:
        os.kill(pid, 0)
    except PermissionError:
        pass                             # it exists — it is just not ours to signal
    except OSError:
        return out                       # gone; the file is stale
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            named = script.encode() in fh.read().replace(b"\0", b" ")
    except OSError:
        return dict(out, state="unknown")    # no procfs, or not ours to read
    # A live pid whose command line is something else means the number was reused, which is the
    # case the pid file alone cannot see and the reason lib.sh checks the same thing.
    return dict(out, state="up" if named else "down")


def log_tail(path, lines=LOG_TAIL, window=LOG_TAIL_BYTES):
    """The last few lines of a log file, read from its end.

    Seeked rather than read whole: this log reached 726 KB before it was rotated, and pulling all
    of that through memory to show forty lines of it is the kind of thing that is fine until it
    is not. `errors='replace'` because a seek to a byte offset lands mid-character sooner or
    later, and the first line is dropped for the same reason — after a seek it is a fragment.
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - window))
            raw = fh.read()
    except OSError:
        return []                        # never written: the daemon has not logged yet
    rows = raw.decode("utf-8", errors="replace").splitlines()
    if size > window and rows:
        rows = rows[1:]
    return rows[-lines:]


def job_table(now=None):
    """The maintenance clock as rows: what runs, when it last did, and when it next will.

    Assembled from scheduler's own JOBS table and its own cadence arithmetic rather than from a
    copy, so a job added there appears here and a cadence rule changed there is obeyed here. The
    state file is read directly — it is JSON in the user's home directory, and reading it is not
    the same as talking to the daemon, which may not even be running.
    """
    now = now or notelib.now_utc()
    state = scheduler._load(scheduler.STATE_DEFAULT)
    rows = []
    for job in scheduler.JOBS:
        last = scheduler.last_run(state, job.name)
        # A job that keeps failing is not due when its cadence says it is — it is backing off.
        # Both the panel's "next" and its "due" have to know that, or they contradict the loop:
        # the row would read `due` while the daemon sits waiting, and the next-run time would be
        # one already in the past. `blocked_until` is also what tells the two apart for a reader,
        # so it is reported rather than merely folded into the arithmetic.
        blocked = scheduler.blocked_until(state, job.name)
        nxt = scheduler.next_run(job.kind, last, now, blocked)
        rows.append({"name": job.name, "script": job.script, "kind": job.kind,
                     "last_run": last.isoformat() if last else None,
                     "next_run": nxt.isoformat() if nxt else None,
                     "blocked_until": blocked.isoformat() if blocked else None,
                     "due": scheduler._due(job.kind, last, now, blocked)})
    return rows


def _section(build):
    """Run one section of the status payload, or return the error it raised in its place.

    Deliberately broad. Six independent reads sit behind one route, and letting one of them out
    would 502 the whole panel — taking the job table with it, which is precisely the half you
    want when something is broken enough to throw.
    """
    try:
        return build()
    except Exception as e:               # noqa: BLE001 — see above; a broken section is data here
        return {"error": f"{type(e).__name__}: {e}"}


# ---- render (the CLI half of compute -> render -> print) --------------------
def render_snapshot(snap):
    """Human-readable summary of a payload — enough to see the view is sane without a browser."""
    c, w, g = snap["counts"], snap["window"], snap["corpus"]
    span = (f"{w['since']} .. {w['until'] or 'now'}" if w["since"] else "everything")
    lines = [f"graph — {c['notes']} note(s), {c['begets']} begets, {c['binds']} binds"
             + (f"  ·  as of {snap['as_of']}" if snap["as_of"] else "")]
    lines.append(f"  window        {span}  ({c['in_window']} note(s) in it)")
    lines.append(f"  corpus        {g['band']}  ·  mean degree {g['mean_degree']}  ·  "
                 f"{g['orphans']} orphan(s)  ·  {g['communities']} community/ies")
    for com in snap["communities"]:
        mark = "◆" if com["autocatalytic"] else "◇"
        lines.append(f"  [{com['index']}] {mark} {com['size']:>3} notes  hub {com['hub']}  "
                     f"{com['hub_label']}")
    return "\n".join(lines)


def render_note(note):
    """Human-readable detail — the same fields the side panel shows."""
    lines = [f"{note['id']}  ·  {note.get('title') or '(untitled)'}"]
    lines.append(f"  address       {note['folgezettel'] or '(none)'}")
    lines.append(f"  written       {note.get('created_at')}  by {note.get('author')}")
    lines.append(f"  status        {note.get('status')}  ·  visited {note['visited']}")
    if note.get("source_ref"):
        lines.append(f"  source        {note['source_ref']}")
    lines.append("")
    lines.append(f"  {note.get('body')}")
    if note["binds"]:
        lines.append("")
        for b in note["binds"]:
            arrow = "->" if b["direction"] == "out" else "<-"
            lines.append(f"  {arrow} {b['other']}  {b.get('mode') or 'untyped'}"
                         f"/{b.get('status')}  {b.get('rationale') or ''}".rstrip())
    if not note["knn_cached"]:
        lines.append("\n  (no cached neighbours — run scripts/knn-cache.sh)")
    for n in note["nearest"]:
        lines.append(f"  ~ {n['score']:.3f}  {n['id']}  {n['label']}")
    return "\n".join(lines)


# ---- HTTP -------------------------------------------------------------------
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                ".png": "image/png", ".ico": "image/x-icon"}


class State:
    """The server's shared state: a DB client for reading, another for writing, and the last
    snapshot per window.

    The reads live behind one lock. Arcade is only unsafe across threads while a transaction
    session is open and the read path never opens one, but serializing also means a page that
    refilters twenty times does not start twenty rebuilds.

    The writers get their **own** Arcade, because the transaction session is instance state and
    every mutation opens one — sharing the read client would let a snapshot land inside a
    commit's transaction. Their own lock, too: reads must not queue behind a correction waiting
    on Ollama, and a write must not queue behind five whole-table scans.
    """

    def __init__(self, db, ttl=SNAPSHOT_TTL, writable=True, embedder=notelib._UNSET):
        self.db = db
        self.ttl = ttl
        self.lock = threading.Lock()
        self.cache = {}
        self.status_cache = None         # (monotonic, payload) — its own slot; see status()
        self.provocations_cache = None   # (monotonic, payload) — its own slot; see provocations()
        self.writable = writable
        # The seam Ingestor already exposes. A test passes None, so driving the routes needs no
        # Ollama — and leaves no stray vector to invalidate the ANN index (tests/test_ui_write.py).
        self.embedder = embedder
        self.write_lock = threading.Lock()
        self.park_lock = threading.Lock()
        self._ingestor = self._links = self._walks = None

    # -- reading --
    def graph(self, key, refresh=False):
        with self.lock:
            hit = self.cache.get(key)
            if hit and not refresh and time.monotonic() - hit[0] < self.ttl:
                return hit[1]
            days, since, until, as_of = key
            snap = snapshot(self.db, days=days, since=since, until=until, as_of=as_of)
            self.cache[key] = (time.monotonic(), snap)
            return snap

    def note(self, note_id):
        with self.lock:
            return note_detail(self.db, note_id)

    def search(self, q=None, field="text", author=None,
               since=None, until=None, limit=SEARCH_LIMIT):
        """Find notes by metadata — the read half of §8, narrowed to the half a click can afford.

        **Lexical only, and that is not a v1 shortcut.** A semantic query has to embed the query
        string and then ask the LSM_VECTOR index, and asking it live rebuilds every vector in the
        corpus (~11 min at N=101) the moment anything has been embedded since the last query. The
        panel's nearest-in-meaning list already gives §8.1's semantic reading, off the nightly
        cache; `search.sh -q` gives it on the command line, where waiting is a decision. Behind a
        search box it would be a hang, so this box does not offer one.

        Filters AND together, exactly as `search.sh` combines them — same function, same rules, so
        the panel cannot find something the CLI would not. With nothing filled in it is a browse of
        the most recent notes, which is also what `search.sh` with no arguments does.

        Bodies are dropped on the way out. The panel shows a snippet, and the whole body is one
        click away on /api/note/<id> — putting fifty of them on the wire to render fifty rows is
        the same trade the graph payload already refuses (see this module's docstring).
        """
        if field not in SEARCH_FIELDS:
            raise ValueError(f"field must be one of {', '.join(SEARCH_FIELDS)} (got {field!r})")
        limit = max(1, min(int(limit), SEARCH_MAX))
        terms = {"text": None, "title": None, "body": None, "source": None}
        if q:
            terms[field] = q
        with self.lock:
            hits = notelib.lexical_search(self.db, author=author,
                                          since=since, until=until, limit=limit, **terms)
        return {"query": q, "field": field, "count": len(hits), "limit": limit,
                "hits": [{k: h[k] for k in
                          ("id", "title", "snippet", "author", "created_at",
                           "source_ref")} for h in hits]}

    def links(self, status=None, limit=100):
        """The ratification queue — BINDS rows with their endpoint titles.

        LinkManager is the writer class, but `list` is a SELECT that opens no transaction, and
        reading the queue through the same object that ratifies it is what keeps the panel and
        `link.sh list` showing the same rows.
        """
        with self.lock:
            rows = notelib.LinkManager(self.db).list(status=status, limit=limit)
        return {"links": rows, "count": len(rows)}

    def provocations(self, refresh=False):
        """Moves 2-6, read live for the queue panel's non-link sections (§8.1).

        Move 1 already has a persisted queue — `links()` above, fed by the nightly digest or
        `provoke.sh --stage` — because it is the one move that proposes an actual link. The
        other five never stage anything (spec: only move 1 may write a SUGGEST_LINK), so
        there is nothing for a second queue to hold; this instead renders the same read the
        digest does, on demand, so a reader who cleared the link queue still has something to
        look at without waiting for tomorrow night's run.

        Cached like `status()` for the same reason: it is a panel a reader reopens, not a page
        load, and move 3 in particular asks the k-NN cache up to `sample` times.
        """
        with self.lock:
            hit = self.provocations_cache
            if hit and not refresh and time.monotonic() - hit[0] < self.ttl:
                return hit[1]
            corpus = common.Corpus(self.db)
            move6 = debt.report(corpus, limit=PROVOKE_SHOW)
            for r in move6:
                r["prompt"] = debt.prompt(r)
            payload = {
                "move2": notelib.move2_candidates(self.db, k=PROVOKE_SHOW),
                "move3": notelib.move3_candidates(self.db, k=PROVOKE_SHOW),
                "move4": notelib.move4_candidates(self.db, k=PROVOKE_SHOW),
                "move5": notelib.move5_candidates(self.db),
                "move6": move6,
                "move6_quiet": "" if move6 else debt.diagnosis(corpus),
            }
            self.provocations_cache = (time.monotonic(), payload)
            return payload

    def staging_preview(self):
        """What is sitting in both inboxes: staging/ parsed and validated but not committed, and
        staging/scans/ merely listed.

        The staging half is `ingest-staging.sh --dry-run`, exactly — same function, same
        intra-batch parent resolution — so the panel cannot promise something the real ingest
        then refuses. A dry run reads and moves nothing, which is why a GET may do it.

        The scans half is a directory listing and can be nothing more. Turning a photograph into
        notes takes a person and a model reading the page together, with a ratification step in
        between (the transcribe-notes skill); there is no dry run of that, and a panel that
        offered one would be promising the one thing here nobody can do in a click. Both arrive
        together because one panel shows both, and two fetches for one panel is two chances for
        it to be half-drawn.
        """
        files = ingest_staging.staged_files(ingest_staging.DEFAULT_STAGING)
        with self.lock:
            ing = notelib.Ingestor(self.db, embedder=None)   # dry run: it will never be asked
            records = ingest_staging.ingest_all(ing, files, dry_run=True)
        return {"dir": ingest_staging.DEFAULT_STAGING, "files": records,
                "scans_dir": inbox.SCANS, "scans": inbox.scans_waiting()}

    def status(self, refresh=False):
        """The machinery and the measurements: what the scheduler will do next, whether the
        daemons are alive, and what the corpus currently measures.

        **Its own Corpus, not the graph payload's.** `snapshot` is a projection chosen for
        thinking-fitness (see its docstring) and mean degree with an avalanche proxy is not that;
        widening it would put operational numbers on every page load to save three table scans on
        a panel nobody opens per second. Its own cache slot for the same reason — a different
        question, asked far less often, and invalidated by different things.

        Everything below reads. Nothing here writes an Op, moves `Note.visited`, or touches a pid
        file, and `walks` is imported for `visits` alone: `walks.replay` can fall through to a
        live vector query, which behind a click is not slow but a hang (see the module docstring).
        """
        with self.lock:
            hit = self.status_cache
            if hit and not refresh and time.monotonic() - hit[0] < self.ttl:
                return hit[1]

            def measured(corpus):
                crit = criticality.report(corpus)
                return {
                    "criticality": dict(crit, advice=criticality.ADVICE[crit["band"]]),
                    "fitness": fitness.report(corpus, limit=FITNESS_SHOW),
                    # Ascending: the notes the corpus holds and the reader never returns to are
                    # the question worth asking, not the ones already known to be favourites.
                    "attention": walks.visits(corpus, limit=ATTENTION_SHOW, ascending=True),
                    "communities": autocatalysis.report(corpus),
                }

            def health(corpus):
                # Every query here is an HTTP round trip to ArcadeDB costing 80-400 ms, so what
                # this section costs is the NUMBER of queries and not the rows they return —
                # measured, after guessing wrong about it. Hence the backlog is subtracted from
                # two counts already in hand rather than asked for: knn_cache_status has just
                # counted the embedded notes, and Corpus has just read every note there is.
                knn = notelib.knn_cache_status(self.db)
                return {
                    "knn": knn,
                    "embed_backlog": max(0, len(corpus.notes) - knn["embedded"]),
                    "ops": notelib.first_row(self.db.query(
                        "SELECT count(*) AS n FROM Op")).get("n") or 0,
                    "staged": len(ingest_staging.staged_files(ingest_staging.DEFAULT_STAGING)),
                    "scans": len(inbox.scans_waiting()),
                }

            # One Corpus for both sections that need one. Asking twice would be three more table
            # scans for an identical answer; when it fails, both sections say the same thing
            # rather than one of them inventing a number without it.
            read = _section(lambda: common.Corpus(self.db))
            unread = read if isinstance(read, dict) else None

            payload = {
                "version": CONTRACT_VERSION,
                "generated_at": notelib.format_id(notelib.now_utc()),
                "writes": self.writable,
                "jobs": _section(job_table),
                "daemons": _section(lambda: [daemon_state(n, s) for n, s in DAEMONS]),
                "log": _section(lambda: {"path": SCHEDULER_LOG,
                                         "lines": log_tail(SCHEDULER_LOG)}),
                "measured": unread or _section(lambda: measured(read)),
                "health": unread or _section(lambda: health(read)),
            }
            self.status_cache = (time.monotonic(), payload)
            return payload

    def health(self):
        with self.lock:
            n = notelib.first_row(self.db.query("SELECT count(*) AS n FROM Note")).get("n") or 0
        return {"ok": True, "notes": n, "version": CONTRACT_VERSION, "writes": self.writable,
                "generated_at": notelib.format_id(notelib.now_utc())}

    # -- writing (spec §7, §12.3) --
    def write(self, do):
        """Run a mutation under the write lock, then drop the snapshot cache.

        `do` is called with (ingestor, links, walks) — the three writer classes notelib exposes,
        and between them every way the graph can change — and returns what goes on the wire.
        Dropping the cache is what makes the graph the page refetches afterwards include what it
        just did; without it the write would appear to have been ignored for up to a minute. A
        walk visit counts too: `visited` is on every node in the payload and inside fitness.
        """
        if not self.writable:
            raise ReadOnly("this server was started --read-only")
        with self.write_lock:
            if self._ingestor is None:      # built on first use, under the lock that owns them
                wdb = notelib.Arcade()
                self._ingestor = notelib.Ingestor(wdb, embedder=self.embedder)
                self._links = notelib.LinkManager(wdb)
                self._walks = notelib.WalkManager(wdb)
            result = do(self._ingestor, self._links, self._walks)
        with self.lock:
            self.cache.clear()
            self.status_cache = None     # Op count, backlog and staging depth all just moved
            self.provocations_cache = None   # a new/ratified/rejected note or bind can move any move
        return result

    def add_note(self, payload):
        """ADD_NOTE ○ — commit a note (spec §12.3), never embedding inline.

        `embed=False` is not a default being echoed back, it is the point. An Ollama call inside
        a click is a hang, and the backlog is already a predicate on the corpus
        (`Note.embedding IS NULL`) that embed-worker.sh drains every few seconds: committing
        without a vector *is* enqueueing, so the view needs no queue of its own.
        """
        fields = {k: payload.get(k) for k in
                  ("id", "title", "body", "created_at", "author", "source_ref")}
        return self.write(lambda ing, _links, _wm: dict(
            ing.commit(fields, embed=False, parent=payload.get("parent"),
                       mode=payload.get("mode"))._asdict(), ok=True))

    def correct(self, note_id, payload):
        """CORRECT_COSMETIC ○ — the corpus's one in-place edit (spec §6), and the one write
        here that must embed inline: the embedding IS the drift measurement, so there is no
        asynchronous version of it. It is a Python cosine against the stored vector, not a
        query — this still never touches the LSM_VECTOR index.
        """
        return self.write(lambda ing, _links, _wm: dict(
            ing.correct_cosmetic(note_id, payload.get("body"))._asdict(), ok=True))

    def link(self, action, payload):
        """SUGGEST_LINK ● / RATIFY_LINK ○ / RETYPE_LINK ○ / REJECT_LINK ○ (spec §7).

        `mode` is forwarded only when the request actually carried the key. `ratify` draws a
        distinction json.loads cannot — leaving the mode alone versus setting it to untyped —
        and a missing key has to mean the first, or opening the panel and clicking "ratify"
        would silently strip the mode off a typed suggestion.
        """
        if action not in LINK_ACTIONS:
            raise ValueError(f"unknown link action {action!r} — " + ", ".join(LINK_ACTIONS))
        a, b = payload.get("a"), payload.get("b")
        if not (a and b):
            raise ValueError("a and b are required — the two note ids the bind runs between")
        has_mode = "mode" in payload
        mode, rationale = payload.get("mode"), payload.get("rationale")

        def run(_ing, links, _wm):
            if action == "suggest":
                r = links.suggest(a, b, rationale=rationale, mode=mode)
            elif action == "ratify":
                r = (links.ratify(a, b, mode=mode, rationale=rationale) if has_mode
                     else links.ratify(a, b, rationale=rationale))
            elif action == "retype":
                if not has_mode:
                    raise ValueError("retype needs a mode: catalyzes, inhibits, or none")
                r = links.retype(a, b, mode)
            else:
                r = links.reject(a, b)
            return dict(r._asdict(), ok=True)
        return self.write(run)

    def walk(self, action, payload):
        """START_WALK ○ / VISIT ○ / PRODUCE ○ / SAVE_WALK ○ (spec §11.1, §13).

        **The one route here that moves `Note.visited`,** and the only one that ever may. §13.2
        reserves that counter for recorded walks so that it measures human attention and not
        machine activity, and what makes this route honest rather than a hole in that rule is
        that it cannot fire on its own: a visit needs a `walk` id, a walk id only exists because
        somebody pressed `walk`, and until they do, clicking notes writes nothing at all. That is
        the same line the CLI draws — `walk.sh visit` also takes a walk id it cannot invent — and
        the page is now simply a fourth caller of it, as it is for ADD_NOTE and the link verbs.

        Nothing here dedups: `WalkManager._record_visit` already counts a note once per walk
        however often you come back to it, and logs the re-visit as the trail event it is. The
        page is therefore free to send every selection without having to remember the trail, and
        the counting rule lives in exactly one place (tests/test_walk_ops.py pins it).
        """
        if action not in WALK_ACTIONS:
            raise ValueError(f"unknown walk action {action!r} — " + ", ".join(WALK_ACTIONS))
        walk_id, note_id = payload.get("walk"), payload.get("note")
        if action != "start" and not walk_id:
            raise ValueError("walk is required — the id START_WALK handed back")
        if action in ("visit", "produce") and not note_id:
            raise ValueError("note is required — the note being visited or produced")

        # `wm`, not `walks`: `walks` is already this module's name for analytics.walks, and a local
        # that shadows it would make "which walks is this?" a question the reader has to answer
        # twice — once here and once in tests/test_ui_write.py, whose AST check pins that the
        # analytics module is reached for `visits` and nothing else (walks.replay can fall through
        # to a live vector query). Shadowing it would break that check by looking exactly like the
        # thing it exists to catch. `wm` is what scripts/walk.py calls a WalkManager anyway.
        def run(_ing, _links, wm):
            if action == "start":
                # `intent` is accepted but never sent by the page: a prompt in front of a
                # one-click button is friction on the gesture the button exists to make cheap.
                # The walk is not left anonymous for it — WalkManager.start derives one from the
                # seed, in Python, where every other label in this payload is also defined.
                # `walk.sh start --intent` is still how a run gets a stated goal (§11.3).
                seed = payload.get("seed")
                if not seed:
                    raise ValueError("seed is required — the note the walk begins at")
                r = wm.start(payload.get("intent"), seed)
            elif action == "visit":
                r = wm.visit(walk_id, note_id)
            elif action == "produce":
                r = wm.produce(walk_id, note_id)
            else:
                r = wm.save(walk_id)
            return dict(r._asdict(), ok=True)
        return self.write(run)

    def walk_open(self):
        """The walk currently open, or None — so that a reload does not strand one.

        A walk lives in the Op log and nowhere else, so "am I still walking?" is a question only
        the log can answer; keeping the answer in the page would mean a refresh silently orphaned
        an active walk, leaving it counting nothing and never closing. Newest-first and `active`
        only, which also picks up a walk opened from `walk.sh` — that is not a quirk, it is the
        same person's unclosed reading session, and offering to close it is the useful thing to do
        with it.
        """
        with self.lock:
            walks = notelib.list_walks(self.db, status="active", limit=1)
        return {"walk": walks[0] if walks else None, "writes": self.writable}

    # -- parking a file (NOT a write: spec §12.3) --
    def park(self, do):
        """Run a filesystem drop — a file into staging/ or staging/scans/ — under its own lock.

        Deliberately **not** State.write, and the difference is the whole claim: write exists to
        put something in the graph inside the transaction that logs the `Op` saying it did, and a
        parked file enters no transaction because it enters no graph. No note exists, nothing is
        embedded, and nothing is logged; `ingest-staging` is still the act that commits it, and a
        photograph still waits for a person to read it. So this builds no Ingestor and drops no
        snapshot cache — there is no snapshot it could have changed.

        Its own lock because minting is read-then-write: two drops landing in the same
        millisecond would both find the id free and both take it.
        """
        if not self.writable:
            raise ReadOnly("this server was started --read-only")
        with self.park_lock:
            result = do()
        with self.lock:
            self.status_cache = None     # the queue depths it reports just changed
        return result

    def upload(self, payload):
        """A file dropped on the page, parked in whichever inbox its extension names.

        The extension routes it, not the drop zone that caught it: the page offers two, but a
        photograph dropped on the typed one is still a photograph, and refusing it would be
        pedantry where saying where it went is help. Validation is entirely inbox.save's —
        it raises ValueError for every malformed field, which _dispatch turns into a 400.
        """
        return self.park(lambda: dict(
            inbox.save(payload.get("name"), payload.get("data"), max_bytes=UPLOAD_MAX_BYTES),
            ok=True))

    def staging_ingest(self, payload):
        """ADD_NOTE ○, in a batch — the same run `ingest-staging.sh` performs, moving each file
        into staging/processed/ or staging/failed/ as it goes.

        `names` narrows it to particular files and leaves the rest of the directory for a later
        pass. Never embeds inline, for the reason add_note gives.
        """
        wanted = payload.get("names")
        files = ingest_staging.staged_files(ingest_staging.DEFAULT_STAGING)
        if wanted is not None:
            if not isinstance(wanted, list):
                raise ValueError("names must be a list of staged filenames")
            keep = set(wanted)
            files = [(n, p) for n, p in files if n in keep]
        return self.write(lambda ing, _links, _wm: {
            "ok": True, "dir": ingest_staging.DEFAULT_STAGING,
            "files": ingest_staging.ingest_all(ing, files, embed=False)})


def _host_port(value):
    """(host, port) from a `Host` header or an `Origin` URL — (None, None) if it parses as
    neither. A bare authority needs the leading '//' or urlsplit reads it as a path."""
    if not value:
        return None, None
    try:
        parsed = urllib.parse.urlsplit(value if "://" in value else "//" + value)
        return parsed.hostname, parsed.port
    except ValueError:                      # a non-numeric port; treat it as unparseable
        return None, None


class Handler(BaseHTTPRequestHandler):
    """The HTTP surface: do_GET reads, do_POST writes, and there is deliberately no third verb.

    **do_OPTIONS is absent on purpose, and its absence is load-bearing** — see _guard. Adding
    one would quietly undo the CSRF defence, so if a future change seems to want it, that is the
    thing to argue with first.
    """

    server_version = "indexia-ui"
    protocol_version = "HTTP/1.1"     # keep-alive; every reply below sets Content-Length
    state = None

    # Set by make_server()/serve() when running --tailscale: the one extra Host/Origin value
    # accepted beyond loopback (the provisioned MagicDNS FQDN). None means loopback-only, which
    # remains the default.
    allowed_host = None

    def log_message(self, fmt, *args):
        print(f"[ui] {self.address_string()} {fmt % args}", flush=True)

    # -- replies --
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:      # a refused write, whose body we never read off the socket
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _fail(self, code, message):
        self._json({"error": message}, code=code)

    def _static(self, rel):
        full = os.path.realpath(os.path.join(UI_DIR, rel.lstrip("/")))
        ctype = STATIC_TYPES.get(os.path.splitext(full)[1].lower())
        if not full.startswith(UI_DIR + os.sep) or not ctype or not os.path.isfile(full):
            return self._fail(404, "not found")
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def _host_allowed(self, host):
        """Loopback, always — plus `allowed_host` when `--tailscale` set one. No wildcard: the
        tailnet name accepted is exactly the FQDN the cert was issued for, nothing derived from
        the request itself, so a hostile Host header cannot talk its way past this by guessing."""
        return host in LOCAL_HOSTS or (self.allowed_host is not None and host == self.allowed_host)

    # -- the write guard --
    def _guard(self, path):
        """Refuse anything that is not this page talking to its own server.

        Returns (refusal, length): either a (code, message) to refuse with and None, or None and
        the body length the reader may then trust. Handing the length back is not tidiness — it
        is what makes "the cap this checked is the cap the read honours" true by construction
        rather than by two places agreeing to parse the same header the same way.

        `path` decides **only** how big a body may be. Every check that decides whether the
        request is ours at all is above that line and asks nothing about the route: there is no
        path here that skips a check, and if a future one seems to want to, that is the thing to
        argue with rather than the place to add it.

        No authentication was needed while the server only answered GET: a page you happen to
        visit can send a cross-origin request, but it cannot read the reply, so there was
        nothing to steal. A write needs no reply to do damage — the same page could have put
        notes in the corpus. Three cheap checks close that, and the last one does the work:

          * `Host` must name us. A hostile DNS name that resolves to 127.0.0.1 (rebinding) does
            not, so this is the check that makes "loopback only" mean what it says. Under
            `--tailscale` exactly one more name is accepted — `allowed_host`, the FQDN the cert
            was actually issued for — never a wildcard, so a rebind attempt still has nothing to
            aim at (see `_host_allowed`).
          * `Origin`, when the browser sends one, must be us.
          * the request must carry `Content-Type: application/json` AND X-Indexia-Write.

        Neither of those last two can be set on a cross-origin request without a CORS
        preflight, and **this class defines no do_OPTIONS**, so the preflight gets http.server's
        501 and the browser never sends the request at all. A plain <form> — the one thing that
        can POST cross-origin with no preflight — cannot set either one under any circumstance.
        """
        port = self.server.server_address[1]
        host, hport = _host_port(self.headers.get("Host"))
        if not self._host_allowed(host) or (hport is not None and hport != port):
            return (403, "refused: Host must name this server"), None
        origin = self.headers.get("Origin")
        if origin:
            ohost, oport = _host_port(origin)
            # Origin's scheme decides the default port when the browser omits an explicit one.
            # This server is plain HTTP on loopback and HTTPS under --tailscale, never both, so
            # allowed_host alone (set only in the latter case) tells the two apart.
            default_port = 443 if self.allowed_host else 80
            if not self._host_allowed(ohost) or (oport or default_port) != port:
                return (403, f"refused: cross-origin write from {origin}"), None
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return (415, "refused: a write must be Content-Type: application/json"), None
        if not self.headers.get(WRITE_HEADER):
            return (403, f"refused: a write must carry the {WRITE_HEADER} header"), None

        # Below here the request is ours; what is left is whether we can trust its length.
        # A chunked body carries no Content-Length, so the reader below would take it for an
        # empty one and leave the bytes on the socket to be parsed as the *next* request.
        if self.headers.get("Transfer-Encoding"):
            return (400, "refused: send a body with Content-Length; chunked is not read here"), None
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return (400, "Content-Length must be a number"), None
        # A negative length passes a `> cap` test and then means "read to EOF" — on a keep-alive
        # connection with no socket timeout, that is a thread parked until the client goes away.
        if length < 0:
            return (400, "Content-Length must not be negative"), None
        cap = UPLOAD_MAX_WIRE if path == UPLOAD_PATH else MAX_BODY
        if length > cap:
            return (413, f"body too large: {length} bytes, and the cap is {cap}"), None
        return None, length

    def _read_json(self, length):
        """The request body as a dict, reading exactly the length _guard validated. ValueError
        carries the message to send as a 400."""
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise ValueError(f"body is not valid JSON: {e}")
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
        return payload

    def _dispatch(self, route):
        """Run a route, turning the library's exceptions into the statuses they mean.

        DuplicateNote and LinkExists subclass ValueError, so they have to be caught above it or
        "you already have this" would go out as a malformed request. CosmeticDriftError gets its
        numbers onto the wire and not only into its message, because the page offers §6's remedy
        — commit the correction as a new note and bind it back with `inhibits` — and it should
        be able to show you the drift it is offering that for.
        """
        try:
            route()
        except ReadOnly as e:
            self._fail(403, str(e))
        except (notelib.DuplicateNote, notelib.LinkExists) as e:
            self._fail(409, str(e))
        except notelib.CosmeticDriftError as e:
            self._json({"error": str(e), "note_id": e.note_id, "drift": e.drift,
                        "max_drift": e.max_drift}, code=422)
        except ValueError as e:
            self._fail(400, str(e))
        except notelib.EmbedderUnavailable as e:
            self._fail(503, f"the embedder is unavailable: {e}")
        except notelib.ArcadeError as e:
            print(f"[ui] query failed: {e}\n  SQL: {e.sql}", flush=True)
            self._fail(502, f"query failed: {e}")
        except (BrokenPipeError, ConnectionResetError) as e:
            # The client hung up part-way. There is nobody left to answer, and the default is a
            # traceback on the log for something that is not this server's mistake.
            self.close_connection = True
            print(f"[ui] client disconnected: {e}", flush=True)

    # -- routes --
    def do_GET(self):
        self._dispatch(self._get)

    def _get(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        def one(name):
            return (query.get(name) or [None])[0] or None

        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/ui/"):
            return self._static(path[len("/ui/"):])
        if path == "/api/health":
            return self._json(self.state.health())
        if path == "/api/graph":
            return self._json(self.state.graph(_graph_key(query),
                                               refresh=bool(query.get("refresh"))))
        if path == "/api/search":
            limit = one("limit")
            return self._json(self.state.search(
                q=one("q"), field=one("field") or "text", author=one("author"),
                since=one("since"), until=one("until"),
                limit=int(limit) if limit else SEARCH_LIMIT))
        if path == "/api/links":
            limit = one("limit")
            return self._json(self.state.links(status=one("status"),
                                               limit=int(limit) if limit else 100))
        if path == "/api/provocations":
            return self._json(self.state.provocations(refresh=bool(query.get("refresh"))))
        if path == "/api/staging":
            return self._json(self.state.staging_preview())
        if path == "/api/status":
            return self._json(self.state.status(refresh=bool(query.get("refresh"))))
        if path == "/api/walk":
            return self._json(self.state.walk_open())
        if path.startswith("/api/note/"):
            note = self.state.note(urllib.parse.unquote(path[len("/api/note/"):]))
            return self._json(note) if note else self._fail(404, "no such note")
        return self._fail(404, "not found")

    def do_POST(self):
        # Parsed once and handed to both, so the cap the guard applied and the route that runs
        # can never be reading two different paths out of the same request.
        path = urllib.parse.urlparse(self.path).path
        refused, length = self._guard(path)
        if refused:
            # The body is still sitting unread on the socket, so this connection cannot carry
            # another request — the leftover bytes would be parsed as one. Close it.
            self.close_connection = True
            return self._fail(*refused)
        self._dispatch(lambda: self._post(path, length))

    def _post(self, path, length):
        # A --read-only server refuses before it looks at anything. Every route below validates its
        # own payload and raises ValueError -> 400 when it is short of a field, and on a server
        # that was never going to run the write that is the wrong answer: it reports on a request
        # nobody asked it to inspect, and it varies by route, so "does this server write?" would
        # get a different reply depending on how well-formed the thing it refused happened to be.
        # State.write and State.park still carry their own check — this is the HTTP-level one, and
        # they are the guarantee for any caller that is not HTTP.
        if not self.state.writable:
            # The body is still on the socket. Left there it would be parsed as the *next*
            # request, so drain it — _guard has already capped how much that can be.
            if length:
                self.rfile.read(length)
            raise ReadOnly("this server was started --read-only")
        payload = self._read_json(length)
        if path == "/api/note":
            return self._json(self.state.add_note(payload))
        if path.startswith("/api/note/") and path.endswith("/correct"):
            note_id = urllib.parse.unquote(path[len("/api/note/"):-len("/correct")])
            return self._json(self.state.correct(note_id, payload))
        if path.startswith("/api/link/"):
            return self._json(self.state.link(path[len("/api/link/"):], payload))
        if path.startswith("/api/walk/"):
            return self._json(self.state.walk(path[len("/api/walk/"):], payload))
        if path == "/api/staging/ingest":
            return self._json(self.state.staging_ingest(payload))
        if path == UPLOAD_PATH:
            return self._json(self.state.upload(payload))
        return self._fail(404, "not found")


def _graph_key(query):
    """(days, since, until, as_of) from the query string — also the snapshot cache key.

    `days=all` drops the lower bound. An explicit `since` wins over `days`, matching
    window_bounds. Bad input raises ValueError, which the handler turns into a 400.
    """
    def one(name):
        v = (query.get(name) or [None])[0]
        return v or None

    days, since = one("days"), one("since")
    if since:
        days = None
    elif days is None:
        days = DEFAULT_WINDOW_DAYS
    elif days == "all":
        days = None
    else:
        try:
            days = int(days)
        except ValueError:
            raise ValueError(f"days must be a whole number of days or 'all' (got {days!r})")
    as_of = one("as_of")
    if as_of:
        notelib.validate_id(as_of)
    return (days, since, one("until"), as_of)


def make_server(db, host=BIND_HOST, port=DEFAULT_PORT, writable=True, embedder=notelib._UNSET,
                tls=None, allowed_host=None):
    """A bound, not-yet-serving HTTP server. Split out so a test can bind port 0 and drive the
    real routes rather than a stand-in (tests/test_ui_write.py).

    ``tls``, if given, is a ``(cert_path, key_path)`` pair — see scripts/tailscale.py — and the
    socket is wrapped after binding so a bad cert fails at startup, not on the first request.
    ``allowed_host`` is the extra Host/Origin value the write guard accepts beyond loopback (the
    tailnet's MagicDNS FQDN); see Handler._host_allowed. Passing one without the other is legal
    but pointless — --tailscale in main() always sets both together.

    Handler.state is a class attribute, so two servers in one process share it — fine for the
    one process that ever makes two, which shuts the first down before building the second."""
    Handler.state = State(db, writable=writable, embedder=embedder)
    Handler.allowed_host = allowed_host
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        raise SystemExit(f"[ui] cannot bind {host}:{port} — {e}  "
                         f"(already running? scripts/ui.sh status)")
    httpd.daemon_threads = True
    if tls is not None:
        cert_path, key_path = tls
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    return httpd


def serve(db, host=BIND_HOST, port=DEFAULT_PORT, writable=True, tls=None, allowed_host=None,
          shown_host=None):
    """Run the server until interrupted. Binds loopback only, unless `tls`/`allowed_host` widen
    it for --tailscale — see main().

    ``shown_host`` overrides the hostname printed in the URL — used for --tailscale, where the
    cert is issued for the tailnet MagicDNS name, not the bind address (a raw Tailscale IP) itself.
    """
    httpd = make_server(db, host=host, port=port, writable=writable, tls=tls,
                        allowed_host=allowed_host)
    scheme = "https" if tls is not None else "http"
    shown = shown_host or host
    print(f"[ui] serving {scheme}://{shown}:{port}/  — "
          + ("writes go through Ingestor/LinkManager/WalkManager, each with its Op (spec §12.3)"
             if writable else "read-only, writes nothing (spec §13)"), flush=True)
    if allowed_host:
        # Bound to the tailnet IP alone (--tailscale overrides --host), so this is the ONLY
        # address the socket answers on — a plain http://127.0.0.1 request gets connection
        # refused, not a second way in. Only the write guard's allowlist gained an entry.
        print(f"[ui]   loopback is not reachable while bound this way — use {shown} from any "
              "device on the tailnet, including this one", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[ui] stopped", flush=True)
    finally:
        httpd.server_close()


def main():
    p = argparse.ArgumentParser(
        prog="ui", description="Graph view of the Indexia corpus (spec §11.3 mod 6, §7, §13).")
    g = p.add_argument_group("print a payload and exit, instead of serving")
    g.add_argument("--snapshot", action="store_true", help="the graph payload")
    g.add_argument("--note", metavar="ID", help="one note's detail payload")
    g2 = p.add_argument_group("the reading window (both --since/--until inclusive)")
    g2.add_argument("--days", default=str(DEFAULT_WINDOW_DAYS),
                    help=f"window back from now, or 'all' (default {DEFAULT_WINDOW_DAYS})")
    g2.add_argument("--since", help="notes on/after this UTC day (YYYY-MM-DD)")
    g2.add_argument("--until", help="notes on/before this UTC day (YYYY-MM-DD)")
    g2.add_argument("--as-of", dest="as_of", metavar="TS",
                    help="the graph as it stood at this instant (note-id shape)")
    p.add_argument("--port", type=int, default=int(os.environ.get("INDEXIA_UI_PORT",
                                                                  DEFAULT_PORT)))
    p.add_argument("--host", default=BIND_HOST, help="bind address (loopback by default, and "
                                                     "there is no good reason to widen it)")
    p.add_argument("--tailscale", action="store_true",
                   help="bind this machine's Tailscale IP and serve HTTPS with a "
                        "tailscale-issued cert for its MagicDNS name, so other devices on the "
                        "tailnet can reach the graph UI at a trusted https:// URL. Overrides "
                        "--host outright — loopback is not reachable while running this way, "
                        "only the tailnet name is. The write guard's Host/Origin allowlist gains "
                        "that one name (never a wildcard — see Handler._host_allowed). Requires "
                        "`tailscale` to be installed and up.")
    p.add_argument("--read-only", dest="read_only", action="store_true",
                   default=bool(os.environ.get("INDEXIA_UI_READONLY")),
                   help="refuse every write with a 403 — the pre-v0.8.1 surface "
                        "(also INDEXIA_UI_READONLY=1)")
    p.add_argument("--json", action="store_true", help="print the raw payload as JSON")
    args = p.parse_args()

    db = notelib.Arcade()
    try:
        if args.note:
            note = note_detail(db, args.note)
            if not note:
                sys.exit(f"[ui] no note with id {args.note}")
            print(json.dumps(note, indent=2, ensure_ascii=False) if args.json
                  else render_note(note))
            return
        if args.snapshot:
            key = _graph_key({"days": [args.days], "since": [args.since],
                              "until": [args.until], "as_of": [args.as_of]})
            snap = snapshot(db, days=key[0], since=key[1], until=key[2], as_of=key[3])
            print(json.dumps(snap, indent=2, ensure_ascii=False) if args.json
                  else render_snapshot(snap))
            return
    except ValueError as e:
        sys.exit(f"[ui] {e}")
    except notelib.ArcadeError as e:
        sys.exit(f"[ui] query failed: {e}\n  SQL: {e.sql}")

    host, tls, shown_host, allowed_host = args.host, None, None, None
    if args.tailscale:
        try:
            host = tailscale.tailscale_ip()
            cert_path, key_path, fqdn = tailscale.provision_cert(Path(TAILSCALE_CERT_DIR))
        except tailscale.TailscaleError as e:
            sys.exit(f"[ui] {e}")
        tls, shown_host, allowed_host = (cert_path, key_path), fqdn, fqdn

    serve(db, host=host, port=args.port, writable=not args.read_only, tls=tls,
          allowed_host=allowed_host, shown_host=shown_host)


if __name__ == "__main__":
    main()
