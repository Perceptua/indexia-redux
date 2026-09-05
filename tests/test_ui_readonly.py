#!/usr/bin/env python3
"""**Reading** the graph writes nothing — the payload contract, and the price of a look. (§13)

Same guarantee tests/test_analytics_readonly.py makes for the reports, made for a thing a human
clicks around in for an hour: after every GET route has been exercised, the corpus counts, the
Op count and the `Note.visited` total are all identical. `visited` is the one that would go
wrong quietly — incrementing it on a page view is the obvious "improvement", and it would
destroy the counter's meaning, since a number both a human and a daemon can move measures
neither (§13.2). That `/api/staging` is on the list matters: previewing the staging directory
runs the real ingest batch in dry-run, and a dry run that committed anything would show up here.

Since v0.8.1 the server also writes, on POST, and "no verb but GET exists" is no longer the
reason reading is safe. What replaced it — every write goes through Ingestor/LinkManager and so
cannot reach the graph without its Op, no raw SQL, no live vector query — lives in
tests/test_ui_write.py, along with the CSRF guard. This file is now about the read half only,
and about the one thing that must remain true of the write verb here: it is not a way to read.

The HTTP checks run against a real server on an ephemeral port, so the routes and the traversal
guard are the ones that actually ship.
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

import lib

sys.path.insert(0, os.path.join(lib.REPO, "scripts"))
import notelib  # noqa: E402
import ui  # noqa: E402
from analytics import common  # noqa: E402

check = lib.check
db = notelib.Arcade()


def op_count():
    return notelib.first_row(db.query("SELECT count(*) AS n FROM Op")).get("n") or 0


def visited_total():
    return notelib.first_row(db.query("SELECT sum(visited) AS n FROM Note")).get("n") or 0


def pidfiles():
    """{name: (mtime, contents)} for every pid file — the one part of the filesystem /api/status
    reads, and so the one part it could be caught changing."""
    out = {}
    if os.path.isdir(ui.RUN_DIR):
        for name in sorted(os.listdir(ui.RUN_DIR)):
            path = os.path.join(ui.RUN_DIR, name)
            if name.endswith(".pid") and os.path.isfile(path):
                out[name] = (os.path.getmtime(path), open(path, encoding="utf-8").read())
    return out


def get(url):
    """(status, body) for a GET, without raising on an error status."""
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def verb(url, method):
    """The status a non-GET verb comes back with."""
    req = urllib.request.Request(url, data=b"{}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


lib.require(db, "the read-only server", notes=1)

ops_before, visits_before = op_count(), visited_total()

with lib.corpus_guard(db):
    corpus = common.Corpus(db)
    snap = ui.snapshot(db)

    check("the snapshot carries every note", len(snap["nodes"]) == len(corpus.notes),
          f"{len(snap['nodes'])} node(s)")
    check("the snapshot carries both edge layers",
          snap["counts"]["begets"] == len(corpus.begets)
          and snap["counts"]["binds"] == len(corpus.binds),
          f"{snap['counts']['begets']} begets / {snap['counts']['binds']} binds")

    ids = {n["id"] for n in snap["nodes"]}
    check("every edge endpoint resolves to a node in the payload",
          all(e["source"] in ids and e["target"] in ids for e in snap["edges"]))
    edge_ids = [e["id"] for e in snap["edges"]]
    check("edge ids are unique — a selection has to survive a refilter",
          len(set(edge_ids)) == len(edge_ids))
    check("edge ids are stable across two reads of the same graph",
          [e["id"] for e in ui.snapshot(db)["edges"]] == edge_ids)

    # A body per node is ~5 MB at 10k notes to draw dots nobody can read, and it would put the
    # whole corpus on the wire for a view that only needs labels. Also a privacy guard.
    check("no note body or embedding rides along in the graph payload",
          all("body" not in n and "embedding" not in n for n in snap["nodes"]))

    check("the sign convention survives the wire",
          all(e["sign"] == notelib.bind_sign(e["mode"])
              for e in snap["edges"] if e["type"] == "binds")
          and all(e["sign"] == 0 for e in snap["edges"] if e["type"] == "begets"))

    # The window is a visibility flag, never a filter on the payload: fitness, community and
    # degree stay corpus-wide, or the view would disagree with `analytics.sh`.
    narrow = ui.snapshot(db, days=7)
    lo = ui.window_bounds(days=7)[2]
    marked = [n for n in narrow["nodes"] if n["in_window"]]
    check("a narrower window keeps every note in the payload",
          len(narrow["nodes"]) == len(snap["nodes"]), f"{len(marked)} marked in-window")
    check("every in-window note is genuinely inside the bound",
          all(n["t"] >= lo for n in marked))
    check("no out-of-window note is marked in-window",
          all(n["t"] < lo for n in narrow["nodes"] if not n["in_window"]))
    check("the window is a flag, not a filter — the metrics do not move",
          {n["id"]: n["fitness"] for n in narrow["nodes"]}
          == {n["id"]: n["fitness"] for n in snap["nodes"]})

    check("'all' drops the lower bound", ui.window_bounds(days=None)[2] is None)

    # An id and a round-tripped DATETIME must land on the same instant, or the timeline lies.
    sample = sorted(corpus.notes)[0]
    check("a note id and its created_at agree to the millisecond",
          ui.epoch_ms(sample) == ui.epoch_ms(corpus.notes[sample].get("created_at")),
          sample)
    check("a null created_at reads as no instant, not as the epoch",
          ui.epoch_ms(None) is None and ui.epoch_ms("") is None)

    detail = ui.note_detail(db, sample)
    check("the detail payload carries the body the graph left out", bool(detail.get("body")))
    check("the detail payload carries a Folgezettel address",
          isinstance(detail.get("folgezettel"), str), detail.get("folgezettel") or "(none)")
    check("semantic neighbours come back scored and labelled",
          all(set(h) >= {"id", "label", "score"} for h in detail["nearest"]),
          f"{len(detail['nearest'])} neighbour(s), cached={detail['knn_cached']}")
    check("a well-formed id that names no note is None, not an exception",
          ui.note_detail(db, "20200101T000000000Z") is None)

    # ---- the real server, on an ephemeral port ----
    httpd = ui.make_server(db, host="127.0.0.1", port=0)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        code, body = get(f"{base}/api/health")
        check("GET /api/health answers", code == 200 and json.loads(body)["ok"], body.strip())

        code, body = get(f"{base}/api/graph")
        check("GET /api/graph answers with the payload",
              code == 200 and len(json.loads(body)["nodes"]) == len(ids))

        code, body = get(f"{base}/api/note/{sample}")
        check("GET /api/note/<id> answers", code == 200 and json.loads(body)["id"] == sample)

        # The search panel. Lexical only, and the route has to keep it that way: a semantic
        # query would embed the query string and hit LSM_VECTOR, which rebuilds every vector in
        # the corpus — minutes behind a click. There is deliberately no `q=` that does that.
        code, body = get(f"{base}/api/search")
        browse = json.loads(body)
        check("GET /api/search with no filters browses the most recent notes",
              code == 200 and isinstance(browse["hits"], list) and browse["hits"],
              f"{browse.get('count')} row(s)")
        check("...newest first, and never more than the cap it reports",
              [h["id"] for h in browse["hits"]] == sorted(
                  (h["id"] for h in browse["hits"]), reverse=True)
              and browse["count"] <= browse["limit"], f"limit {browse.get('limit')}")
        # Same guard the graph payload gets, for the same two reasons: fifty bodies on the wire
        # to draw fifty snippets, and a body is the private half of a note.
        check("no note body or embedding rides along in a search result",
              all("body" not in h and "embedding" not in h for h in browse["hits"]))
        check("every hit carries what the panel shows and what it needs to jump to the graph",
              all({"id", "title", "snippet", "author", "created_at", "source_ref"}
                  == set(h) for h in browse["hits"]))

        # The panel and search.sh are the same function with the same rules, so a filter that
        # narrows on the command line has to narrow here — otherwise the view could promise a
        # result the CLI would not stand behind.
        sample_title = next((h["title"] for h in browse["hits"] if h.get("title")), None)
        if sample_title:
            word = sorted(sample_title.split(), key=len)[-1]
            code, body = get(f"{base}/api/search?q={urllib.parse.quote(word)}&field=title")
            found = json.loads(body)
            check("a title search finds the note whose title it came from",
                  code == 200 and any(word.lower() in (h["title"] or "").lower()
                                      for h in found["hits"]),
                  f"{found['count']} hit(s) for title~{word!r}")
            check("...and agrees exactly with notelib.lexical_search, which is search.sh's own",
                  [h["id"] for h in found["hits"]]
                  == [h["id"] for h in notelib.lexical_search(
                      db, title=word, limit=found["limit"])])

        check("every searchable field the panel offers is accepted",
              all(get(f"{base}/api/search?q=a&field={f}")[0] == 200
                  for f in ui.SEARCH_FIELDS))
        check("a field the panel does not offer is a 400, not a silent full-corpus browse",
              get(f"{base}/api/search?q=a&field=embedding")[0] == 400)
        check("a malformed date is a 400, not a stack trace",
              get(f"{base}/api/search?since=last-tuesday")[0] == 400)
        # A day is closed at both ends, the same reading search.sh gives it — since=D until=D is
        # all of day D, and a route that quietly dropped the last day would lose notes.
        oldest = sorted(corpus.notes)[0]
        one_day = f"{oldest[0:4]}-{oldest[4:6]}-{oldest[6:8]}"
        code, body = get(f"{base}/api/search?since={one_day}&until={one_day}&limit=200")
        same_day = json.loads(body)
        check("--since D --until D is all of day D, over HTTP too",
              code == 200 and any(h["id"] == oldest for h in same_day["hits"])
              and all(h["id"][:8] == oldest[:8] for h in same_day["hits"]),
              f"{same_day['count']} note(s) on {one_day}")

        code, body = get(f"{base}/api/links?status=suggested")
        check("GET /api/links answers with the ratification queue",
              code == 200 and isinstance(json.loads(body)["links"], list),
              f"{json.loads(body)['count']} suggested")

        # Moves 2-6, read live for the queue panel's non-link sections — see State.provocations.
        code, body = get(f"{base}/api/provocations")
        prov = json.loads(body)
        check("GET /api/provocations answers with all five non-link moves",
              code == 200 and {"move2", "move3", "move4", "move5", "move6"} <= set(prov),
              str(sorted(prov)))
        check("move 5's buckets are always present, even empty",
              {"orphans", "inhibited", "on_this_day"} <= set(prov["move5"]))

        # A dry run reads the directory, parses it, and asks the DB which ids exist. It must not
        # commit, embed, or move a file — the Op check at the bottom is what proves the first.
        code, body = get(f"{base}/api/staging")
        staged = json.loads(body)
        check("GET /api/staging previews without ingesting",
              code == 200 and isinstance(staged["files"], list),
              f"{len(staged['files'])} file(s) staged")
        # staging/scans/ arrives on the same route because one panel shows both inboxes, and it is a
        # listing rather than a preview: there is no dry run of "a person reads the handwriting".
        check("...and lists the scans waiting beside them, with a size and a timestamp each",
              isinstance(staged.get("scans"), list) and staged.get("scans_dir", "").endswith("scans")
              and all({"name", "bytes", "modified"} <= set(s) for s in staged["scans"]),
              f"{len(staged.get('scans') or [])} scan(s) waiting")

        # The status panel. Six independent reads behind one route, each wrapped so that one
        # failing cannot 502 the rest — a broken section arrives as data, not as a lost panel.
        code, body = get(f"{base}/api/status")
        st = json.loads(body)
        check("GET /api/status answers with the clock, the daemons and the measurements",
              code == 200 and {"jobs", "daemons", "log", "measured", "health"} <= set(st),
              str(sorted(st)))
        broken = {k: v.get("error") for k, v in st.items()
                  if isinstance(v, dict) and "error" in v}
        check("...and no section of it failed", not broken, str(broken))
        check("every job carries its cadence, its last run and a computed next one",
              st["jobs"] and all({"name", "kind", "last_run", "next_run", "due"} <= set(j)
                                 for j in st["jobs"]),
              str(st["jobs"][0]) if st.get("jobs") else "no jobs")
        check("every daemon is reported up, down or — honestly — unknown",
              {d["name"] for d in st["daemons"]} == {"scheduler", "embed-worker", "ui"}
              and all(d["state"] in ("up", "down", "unknown") for d in st["daemons"]),
              str([(d["name"], d["state"]) for d in st["daemons"]]))
        check("the server reports itself up, since it is the process answering",
              next(d for d in st["daemons"] if d["name"] == "ui")["state"] == "up")

        # lib.sh's daemon_pid deletes a pid file it finds stale and writes a fresh one from a
        # pgrep. Reading the same thing over HTTP may do neither: a GET leaves the filesystem
        # alone, and the pid files are the only part of it this route touches at all.
        before_pids = pidfiles()
        get(f"{base}/api/status?refresh=1")
        check("reading the status leaves the pid files exactly as they were",
              pidfiles() == before_pids,
              f"{sorted(before_pids)} -> {sorted(pidfiles())}")

        check("a malformed note id is a 400, not a stack trace",
              get(f"{base}/api/note/not-an-id")[0] == 400)
        check("an unknown route is a 404", get(f"{base}/nope")[0] == 404)
        check("GET / serves the page", get(f"{base}/")[0] == 200)

        # The static handler must not be a way out of ui/ and into the repo.
        check("a path traversal out of ui/ is refused",
              get(f"{base}/ui/../scripts/notelib.py")[0] == 404
              and get(f"{base}/ui/vendor/../../../scripts/ui.py")[0] == 404)

        # POST exists now, but only as a write verb: there is no route on which it reads, and
        # nothing beyond it was added. (Its own refusals are tests/test_ui_write.py's subject.)
        codes = {m: verb(f"{base}/api/graph", m) for m in ("PUT", "DELETE", "PATCH")}
        check("no verb beyond GET and POST reaches any code of ours",
              all(c == 501 for c in codes.values()), str(codes))
        posted = verb(f"{base}/api/graph", "POST")
        check("POST is not a second way to read a route that GETs — it is refused, never "
              "answered with the payload", posted >= 400, f"POST /api/graph -> {posted}")
    finally:
        httpd.shutdown()
        httpd.server_close()

ops_after, visits_after = op_count(), visited_total()
check("no Op was written — browsing leaves no trace in the log at all",
      ops_after == ops_before, f"{ops_before} -> {ops_after}")
check("Note.visited is untouched — reading a note in a panel is not walking it (§13.2)",
      visits_after == visits_before, f"{visits_before} -> {visits_after}")

lib.report_and_exit()
