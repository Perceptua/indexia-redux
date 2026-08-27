#!/usr/bin/env python3
"""The graph UI's write surface: what it refuses, what it commits, and what it logs. (spec §7, §12.3)

Three groups, and the first is the one with the most future in it.

**The guard.** The server binds loopback and has no authentication, which cost nothing while it
only answered GET — a page you visit can send a cross-origin request but cannot read the reply.
A write needs no reply to do damage. So a write must carry `Content-Type: application/json` and
`X-Indexia-Write`, neither of which a cross-origin request can set without a CORS preflight, and
the server answers no `OPTIONS`. The check that `OPTIONS` is a 501 is therefore not a detail
about an unimplemented verb — it *is* the defence, and the day someone adds `do_OPTIONS` to be
helpful, this is the line that should stop them.

**The writes.** Every route goes through `Ingestor` or `LinkManager`, so every one of them lands
its `Op` in the same transaction as its change (§12.3). Rather than count Ops, this checks the
stronger thing: every write handed back an `op_id`, and every one of those is really in the log.

**What must not move.** `Note.visited` is untouched by all of it — writing a note is no more
walking it than reading one is (§13.2) — and the corpus counts come back exactly as found.

Runs against the live corpus (there is no fixture DB), so it commits real notes and deletes them
in a `finally`, the way tests/test_walk_ops.py does with walks. The server under test is built
with `embedder=None`: the routes need no Ollama, and more to the point a stray embedding would
invalidate the whole LSM_VECTOR index and hand the next vector query an ~11-minute rebuild. The
one check that needs a real embedder says so and skips when it cannot have one.
"""
import ast
import base64
import json
import os
import sys
import threading
import urllib.error
import urllib.request

import lib

sys.path.insert(0, os.path.join(lib.REPO, "scripts"))
import notelib  # noqa: E402
import ui  # noqa: E402

check = lib.check
db = notelib.Arcade()

MARK = "indexia-tests: synthetic note for the UI write surface, safe to delete"
_MISSING = object()


def call(url, method="POST", payload=None, headers=None, data=_MISSING):
    """(status, parsed body) for a request, without raising on an error status.

    The defaults carry exactly what the guard demands, so a check can remove one thing (pass
    None as a header value to drop it) and see the refusal that causes.
    """
    hdrs = {"Content-Type": "application/json", ui.WRITE_HEADER: "1"}
    hdrs.update(headers or {})
    hdrs = {k: v for k, v in hdrs.items() if v is not None}
    body = json.dumps(payload or {}).encode() if data is _MISSING else data
    req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"error": raw}


def get(url):
    return call(url, method="GET", data=None)


def op_exists(op_id):
    return notelib.first_row(db.query("SELECT id FROM Op WHERE id = :i",
                                      {"i": op_id})).get("id") == op_id


def op_count():
    return notelib.first_row(db.query("SELECT count(*) AS n FROM Op")).get("n") or 0


def b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def visited_total():
    return notelib.first_row(db.query("SELECT sum(visited) AS n FROM Note")).get("n") or 0


def scrub(note_id):
    """Delete a synthetic note and anything it accrued — the same three statements
    Ingestor.reject_variant uses, which is the repo's answer to 'unmake a note'."""
    db.command("DELETE FROM BINDS WHERE outV().id=:id OR inV().id=:id", {"id": note_id})
    db.command("DELETE FROM BEGETS WHERE outV().id=:id OR inV().id=:id", {"id": note_id})
    db.command("DELETE FROM Note WHERE id=:id", {"id": note_id})


def visited_of(note_id):
    return notelib.first_row(db.query("SELECT visited FROM Note WHERE id = :n",
                                      {"n": note_id})).get("visited") or 0


visits_before = visited_total()
made, op_ids, parked, walks_made = [], [], [], []

with lib.corpus_guard(db):
    httpd = ui.make_server(db, host="127.0.0.1", port=0, embedder=None)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        # ---- the guard ------------------------------------------------------
        code, body = call(f"{base}/api/note", payload={"body": MARK},
                          headers={ui.WRITE_HEADER: None})
        check("a write without the X-Indexia-Write header is refused", code == 403, str(body))

        code, _ = call(f"{base}/api/note", payload={"body": MARK},
                       headers={"Content-Type": "text/plain"})
        check("a write that is not application/json is refused", code == 415, str(code))

        code, _ = call(f"{base}/api/note", payload={"body": MARK},
                       headers={"Origin": "http://evil.example"})
        check("a cross-origin write is refused on its Origin", code == 403, str(code))

        code, _ = call(f"{base}/api/note", payload={"body": MARK},
                       headers={"Host": "evil.example"})
        check("a write whose Host is not this server is refused — that is the DNS-rebinding "
              "case, and what makes 'loopback only' mean anything", code == 403, str(code))

        # THE check. Both header requirements above are only enforceable because a cross-origin
        # request has to ask permission first, and never gets an answer.
        code, _ = call(f"{base}/api/note", method="OPTIONS", data=None)
        check("OPTIONS is refused by the transport, so no CORS preflight can ever succeed",
              code == 501, str(code))

        # Declared, not sent: the point of the cap is that the body is refused on its header and
        # never buffered. Actually streaming a megabyte would test the same rule the slow way and
        # get a broken pipe for it, since the server has answered and hung up by then.
        code, _ = call(f"{base}/api/note", payload={"body": MARK},
                       headers={"Content-Length": str(ui.MAX_BODY + 1)})
        check("a body over the cap is refused on its Content-Length, before a byte is read",
              code == 413, str(code))

        code, _ = call(f"{base}/api/note", payload={"body": MARK},
                       headers={"Content-Length": "-1"})
        check("a negative Content-Length is refused — it clears a `> cap` test and then means "
              "'read until EOF', which parks a thread until the client goes away",
              code == 400, str(code))

        code, _ = call(f"{base}/api/note", payload={"body": MARK},
                       headers={"Transfer-Encoding": "chunked"})
        check("a chunked body is refused rather than read as an empty one, leaving its bytes on "
              "the socket to be parsed as the next request", code == 400, str(code))

        # ---- ADD_NOTE -------------------------------------------------------
        code, note = call(f"{base}/api/note",
                          payload={"title": "indexia-tests: write surface", "body": MARK})
        check("POST /api/note commits a note",
              code == 200 and note.get("ok") and note.get("rule") == "ADD_NOTE", str(note)[:140])
        nid = note.get("note_id")
        if not nid:
            lib.report_and_exit()
        made.append(nid)
        op_ids.append(note.get("op_id"))

        row = notelib.first_row(db.query(
            "SELECT id, title, author, status, embedding FROM Note WHERE id = :n", {"n": nid}))
        check("the note is really in the graph, with ingestion's defaults",
              row.get("id") == nid and row.get("author") == "human"
              and row.get("status") == "active", str(row.get("author")))
        check("the UI never embeds inline — the note lands pending for embed-worker.sh, which "
              "is what makes committing from a click enqueueing rather than blocking",
              note.get("embedded") is False and row.get("embedding") is None)

        code, child = call(f"{base}/api/note",
                           payload={"body": MARK + " (child)", "parent": nid, "mode": "branch"})
        check("parent+mode draws the one edge ingestion may draw",
              code == 200 and child.get("begets") == [nid, "branches"], str(child.get("begets")))
        cid = child.get("note_id")
        made.append(cid)
        op_ids.append(child.get("op_id"))

        edge = notelib.first_row(db.query(
            "SELECT mode, created_at FROM BEGETS WHERE outV().id = :a AND inV().id = :b",
            {"a": nid, "b": cid}))
        check("BEGETS.created_at is the child's own instant, not the wall clock — which is what "
              "keeps a back-dated import honest under --as-of",
              notelib._dt_digits(edge.get("created_at"))
              == notelib._dt_digits(notelib.id_to_created_at(cid)), str(edge.get("created_at")))

        check("a supplied id that already exists is a 409, not a 400",
              call(f"{base}/api/note", payload={"id": nid, "body": "x"})[0] == 409)
        check("a malformed id is a 400",
              call(f"{base}/api/note", payload={"id": "nope", "body": "x"})[0] == 400)
        check("a note with no body is a 400",
              call(f"{base}/api/note", payload={"title": "no body"})[0] == 400)
        check("a parent that does not exist is a 400",
              call(f"{base}/api/note",
                   payload={"body": "x", "parent": "20200101T000000000Z",
                            "mode": "continue"})[0] == 400)
        check("an unknown POST route is a 404", call(f"{base}/api/nope")[0] == 404)
        check("a POST to a read route is a 404, never a silent accept",
              call(f"{base}/api/graph")[0] == 404)

        # ---- the BINDS ratification flow (spec §7) --------------------------
        code, r = call(f"{base}/api/link/suggest",
                       payload={"a": nid, "b": cid, "rationale": MARK})
        check("suggest stages an untyped, suggested bind — the machine proposes (§8.2)",
              code == 200 and r.get("status") == "suggested" and r.get("mode") is None, str(r))
        op_ids.append(r.get("op_id"))

        code, queue = get(f"{base}/api/links?status=suggested")
        check("the suggestion is in the queue the ratification panel reads",
              code == 200 and any(link.get("a") == nid and link.get("b") == cid
                                  for link in queue["links"]), f"{queue.get('count')} in queue")

        check("a second bind on the same pair is a 409 — one relation per pair, either way round",
              call(f"{base}/api/link/suggest", payload={"a": cid, "b": nid})[0] == 409)

        code, r = call(f"{base}/api/link/ratify", payload={"a": cid, "b": nid})
        check("ratify resolves the edge either way round and reports the stored direction, "
              "because with inhibits the direction IS the claim (§6)",
              code == 200 and r.get("a") == nid and r.get("b") == cid
              and r.get("status") == "ratified", str(r))
        check("ratify with no mode key leaves the mode alone rather than untyping it",
              r.get("mode") is None, str(r.get("mode")))
        op_ids.append(r.get("op_id"))

        code, r = call(f"{base}/api/link/ratify",
                       payload={"a": nid, "b": cid, "mode": "inhibits"})
        check("ratify can assign the mode in the same act",
              code == 200 and r.get("mode") == "inhibits", str(r.get("mode")))
        op_ids.append(r.get("op_id"))

        check("retype with no mode is a 400, not a silent untyping",
              call(f"{base}/api/link/retype", payload={"a": nid, "b": cid})[0] == 400)
        code, r = call(f"{base}/api/link/retype",
                       payload={"a": nid, "b": cid, "mode": "catalyzes"})
        check("retype relabels an existing bind", code == 200 and r.get("mode") == "catalyzes")
        op_ids.append(r.get("op_id"))

        check("an unknown link action is a 400",
              call(f"{base}/api/link/frobnicate", payload={"a": nid, "b": cid})[0] == 400)
        check("a link with only one endpoint is a 400",
              call(f"{base}/api/link/suggest", payload={"a": nid})[0] == 400)
        check("a note cannot be bound to itself",
              call(f"{base}/api/link/suggest", payload={"a": nid, "b": nid})[0] == 400)

        code, r = call(f"{base}/api/link/reject", payload={"a": nid, "b": cid})
        check("reject deletes the edge — a proposal is not corpus (§2.4)",
              code == 200 and r.get("status") == "deleted", str(r))
        op_ids.append(r.get("op_id"))
        check("and it is gone from the graph", not notelib.first_row(db.query(
            "SELECT outV().id AS a FROM BINDS WHERE outV().id=:a AND inV().id=:b",
            {"a": nid, "b": cid})).get("a"))

        # ---- CORRECT_COSMETIC (spec §6) -------------------------------------
        code, r = call(f"{base}/api/note/{nid}/correct", payload={"body": MARK})
        check("correcting a note to the body it already has writes nothing at all — no Op, "
              "which is exactly how §12.3 says 'no state changed' is spelled",
              code == 200 and r.get("op_id") is None, str(r))

        code, r = call(f"{base}/api/note/{nid}/correct", payload={"body": MARK + " (fixed)"})
        check("a cosmetic correction goes through", code == 200 and r.get("op_id"), str(r))
        op_ids.append(r.get("op_id"))
        check("and the body really changed", notelib.first_row(db.query(
            "SELECT body FROM Note WHERE id = :n", {"n": nid})).get("body", "").endswith("(fixed)"))
        check("an empty correction is a 400",
              call(f"{base}/api/note/{nid}/correct", payload={"body": "  "})[0] == 400)
        check("correcting a note that does not exist is a 400",
              call(f"{base}/api/note/20200101T000000000Z/correct",
                   payload={"body": "x"})[0] == 400)

        # ---- staging --------------------------------------------------------
        code, staged = get(f"{base}/api/staging")
        check("GET /api/staging previews the directory without committing anything",
              code == 200 and isinstance(staged.get("files"), list),
              f"{len(staged.get('files') or [])} file(s) waiting")

        # ---- dropped files --------------------------------------------------
        # Only the refusals and the one thing the route promises that inbox.py cannot: that
        # parking a file logs nothing. What lands where, and in what shape, is tested against a
        # tmpdir in tests/test_inbox.py — this route writes into the REAL staging/, so it drops
        # exactly one file and takes it away again.
        for payload, expect, why in [
            ({"name": "n.md", "data": "!!!not base64!!!"}, 400, "a payload that is not base64"),
            ({"name": "n.md", "data": ["a", "list"]}, 400, "a data field that is not a string"),
            ({"name": "n.md"}, 400, "a drop with no data at all"),
            ({"name": 42, "data": b64("x")}, 400, "a name that is not a string"),
            ({"name": "archive.zip", "data": b64("x")}, 400, "an extension nothing here reads"),
            ({"name": "../escape.md", "data": b64("x")}, 400, "a name that climbs out of the inbox"),
        ]:
            code, r = call(f"{base}/api/upload", payload=payload)
            check(f"POST /api/upload refuses {why}", code == expect, f"{code} {r.get('error', '')}")

        code, _ = call(f"{base}/api/upload", payload={"name": "n.md", "data": "x"},
                       headers={"Content-Length": str(ui.UPLOAD_MAX_WIRE + 1)})
        check("a drop over the upload cap is refused on its Content-Length", code == 413, str(code))
        code, _ = call(f"{base}/api/note", payload={"body": MARK},
                       headers={"Content-Length": str(ui.MAX_BODY + 1)})
        check("...and the raised cap is the upload route's alone — every other route still says "
              "1 MiB, because every other route still takes prose", code == 413, str(code))

        ops_before = op_count()
        code, dropped = call(f"{base}/api/upload",
                             payload={"name": "indexia-tests-drop.md", "data": b64(MARK + "\n")})
        check("POST /api/upload parks a typed file under a minted id",
              code == 200 and dropped.get("kind") == "typed" and dropped.get("minted") is True,
              str(dropped))
        if dropped.get("saved_as"):
            parked.append(os.path.join(dropped["dir"], dropped["saved_as"]))
        check("...and the file is really there, carrying the name it arrived as",
              bool(parked) and os.path.isfile(parked[0])
              and "indexia-tests-drop.md" in open(parked[0], encoding="utf-8").read())
        check("**an upload is not an operation** — a parked file logs no Op, because nothing has "
              "reached the graph until ingest commits it (§12.3)",
              op_count() == ops_before, f"{ops_before} -> {op_count()}")

        # ---- walks (spec §11.1, §13.2) --------------------------------------
        # The one route that may move `Note.visited`, and the reason the blanket "the view never
        # touches it" below had to become a narrower claim. What makes the narrower one hold is
        # that a visit cannot happen without a walk id, and a walk id cannot exist without
        # somebody having opened one: the checks below are as much about what does NOT count.
        seed_visits = visited_of(nid)
        code, started = call(f"{base}/api/walk/start", payload={"seed": nid})
        walk_id = started.get("walk_id")
        walks_made.append(walk_id)
        op_ids.append(started.get("op_id"))
        check("POST /api/walk/start opens a walk whose id is its own Op id",
              code == 200 and walk_id and walk_id == started.get("op_id"), str(started))
        check("...and the seed is visited by the act of starting there",
              visited_of(nid) == seed_visits + 1, f"{seed_visits} -> {visited_of(nid)}")

        child_visits = visited_of(cid)
        code, v = call(f"{base}/api/walk/visit", payload={"walk": walk_id, "note": cid})
        op_ids.append(v.get("op_id"))
        check("POST /api/walk/visit counts a first visit",
              code == 200 and visited_of(cid) == child_visits + 1)
        code, v2 = call(f"{base}/api/walk/visit", payload={"walk": walk_id, "note": cid})
        op_ids.append(v2.get("op_id"))
        check("...and a re-visit inside the same walk is logged but does NOT count again — the "
              "counter measures sittings, not clicks, which is what lets the page send every "
              "selection without keeping the trail itself",
              code == 200 and visited_of(cid) == child_visits + 1,
              f"visited={visited_of(cid)}")

        produced_visits = visited_of(cid)
        code, p = call(f"{base}/api/walk/produce", payload={"walk": walk_id, "note": cid})
        op_ids.append(p.get("op_id"))
        check("POST /api/walk/produce records authorship and counts no visit — writing a note is "
              "not reading one (§13), which is why composing during a walk cannot inflate it",
              code == 200 and visited_of(cid) == produced_visits)

        code, open_walk = get(f"{base}/api/walk")
        check("GET /api/walk hands back the open walk, so a reload cannot orphan one",
              code == 200 and (open_walk.get("walk") or {}).get("id") == walk_id,
              str((open_walk.get("walk") or {}).get("id")))
        check("...with the trail and the produce the page needs to resume its tally",
              [t["note_id"] for t in open_walk["walk"]["visited"]] == [nid, cid]
              and open_walk["walk"]["produced"] == [cid],
              str(open_walk["walk"]["produced"]))

        check("a walk action the button cannot reach is a 400 — fork and delete stay on walk.sh",
              call(f"{base}/api/walk/fork", payload={"walk": walk_id})[0] == 400)
        check("every walk the page opens is named, so it can be found again — there is no rule "
              "that amends a START_WALK payload, so an anonymous walk stays anonymous",
              bool(notelib.read_walk(db, walk_id).get("intent")),
              repr(notelib.read_walk(db, walk_id).get("intent")))
        check("a visit with no walk id is a 400, never a visit against nothing",
              call(f"{base}/api/walk/visit", payload={"note": nid})[0] == 400)
        check("a start with no seed is a 400 — a walk begins somewhere (§11.1)",
              call(f"{base}/api/walk/start", payload={})[0] == 400)
        check("a walk id that names no walk is a 400",
              call(f"{base}/api/walk/visit",
                   payload={"walk": "20200101T000000000Z", "note": nid})[0] == 400)

        code, saved = call(f"{base}/api/walk/save", payload={"walk": walk_id})
        op_ids.append(saved.get("op_id"))
        check("POST /api/walk/save closes it, and it reads back exactly as a walk.sh one does",
              code == 200 and notelib.read_walk(db, walk_id).get("status") == "saved")
        # About THIS walk, not about the corpus. "no walk is open anywhere" is not a property this
        # test owns: the tests run against the live corpus, and a walk left open in a browser tab
        # or a terminal is a perfectly ordinary state for the machine to be in. Asserting it made
        # the run fail for a reason that had nothing to do with the code under test.
        check("...and it is no longer the open walk, so the button goes back to grey",
              (get(f"{base}/api/walk")[1].get("walk") or {}).get("id") != walk_id)

        # ---- the Op log -----------------------------------------------------
        # Not "the Op count went up" — that would pass if a write logged somebody else's Op.
        # Every write handed back an id, and every one of those ids is in the log (§12.3).
        check("every write returned an Op id, and every one of them is really in the log",
              all(op_ids) and all(op_exists(o) for o in op_ids), f"{len(op_ids)} write(s)")

    finally:
        # Walks first: DELETE_WALK hands back exactly the visits the walk counted, and it can only
        # do that while the notes it visited still exist. This is also what keeps a test run from
        # leaving an `active` walk behind — the UI resumes the newest one on load, so a stranded
        # one would show up as a live recording session in somebody's browser.
        for walk_id in walks_made:
            if walk_id:
                notelib.WalkManager(db).delete(walk_id)
        for note_id in made:
            if note_id:
                scrub(note_id)
        for path in parked:      # this route writes into the real staging/, so put it back
            if os.path.isfile(path):
                os.remove(path)
        httpd.shutdown()
        httpd.server_close()

# ---- --read-only restores the old surface exactly ---------------------------
# Built second and only after the first is down: Handler.state is a class attribute.
httpd = ui.make_server(db, host="127.0.0.1", port=0, writable=False, embedder=None)
base = f"http://127.0.0.1:{httpd.server_address[1]}"
threading.Thread(target=httpd.serve_forever, daemon=True).start()
try:
    code, health = get(f"{base}/api/health")
    check("a --read-only server says so, so the page can hide every write affordance",
          code == 200 and health.get("writes") is False, str(health))
    codes = {route: call(f"{base}{route}", payload={"body": MARK, "a": "x", "b": "y",
                                                    "seed": "x", "walk": "w",
                                                    "name": "n.md", "data": b64("x")})[0]
             for route in ("/api/note", "/api/link/suggest", "/api/walk/start", "/api/walk/visit",
                           "/api/staging/ingest", "/api/upload")}
    check("and refuses every write route — parking a file is not a graph write and a walk is not "
          "a note, but both are writes, and a server told to touch nothing touches nothing",
          all(c == 403 for c in codes.values()), str(codes))
    check("while still reading", get(f"{base}/api/graph")[0] == 200)
    # Reading whether a walk is open is a read. It has to keep working here, or the page could not
    # tell a read-only server from one where the button is simply grey.
    code, ro_walk = get(f"{base}/api/walk")
    check("...including GET /api/walk, which says the server takes no writes",
          code == 200 and ro_walk.get("writes") is False, str(ro_walk))
finally:
    httpd.shutdown()
    httpd.server_close()

check("`Note.visited` is back exactly where it started — a walk is the one thing here that may "
      "move it, and DELETE_WALK gives back precisely what it took (§13.2). Every other write "
      "above left it alone: committing a note is not walking it, any more than reading one is",
      visited_total() == visits_before, f"{visits_before} -> {visited_total()}")

# ---- the source-level guarantees --------------------------------------------
# Two of the three that guarded the read-only server survive the crossing intact, and they are
# the two that were load-bearing. The third — "no do_POST exists" — is what this file replaces.
src = open(os.path.join(lib.REPO, "scripts", "ui.py"), encoding="utf-8").read()
check("scripts/ui.py composes writes but never SQL: no raw db.command, so nothing can reach the "
      "graph from here without the Op that records it", ".command(" not in src)
check("scripts/ui.py never issues a live vector query — a click must not rebuild the ANN index",
      not any(s in src for s in ("semantic_search", "vector.neighbors", "neighbors_of")))
check("the server still answers no OPTIONS — the CSRF guard is only a guard while that holds",
      not hasattr(ui.Handler, "do_OPTIONS"))
check("and still no verb beyond GET and POST",
      not any(hasattr(ui.Handler, f"do_{m}") for m in ("PUT", "DELETE", "PATCH")))
check("the status panel reports on the scheduler and never drives it: knn-cache absorbs the "
      "~11-minute LSM_VECTOR rebuild, and a 'run now' button would put it behind a click",
      "run_job" not in src)

# Structural rather than a grep. `"replay" not in src` would fail the moment somebody writes the
# comment explaining why replay is excluded — which is exactly the comment that should be there,
# and the two greps above already force this module's prose to dance around `semantic_search`
# and `neighbors_of`. What matters is which attribute is reached, so ask the parser.
tree = ast.parse(src)
reached = sorted({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                  and isinstance(n.value, ast.Name) and n.value.id == "walks"})
check("ui.py reaches analytics.walks for `visits` and nothing else — walks.replay falls through "
      "to a live vector query whenever the cache misses, which behind a click is a hang",
      reached == ["visits"], str(reached))

lib.report_and_exit()
