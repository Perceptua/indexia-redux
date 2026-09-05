#!/usr/bin/env python3
"""provocation-digest — run all six generativity moves and render them as a digest (spec §8.1,
§12.6; the v1.1 scheduled provocation surface).

Over a seed set (recent active notes + the hub of each detected community) it runs move 1
(semantically near, graph-far) per seed, plus the corpus-wide moves 2 (bridges), 3 (implicit
themes), 4 (contradiction pairs), 5 (re-encounter) and 6 (structural debt). It writes
`recent/provocations.md` and, with --stage (the default for the scheduled run), stages move 1
seed→candidate pairs as SUGGEST_LINK edges for you to ratify. Moves 3/4/6 are rendered as
write-a-note prompts — the machine proposes, never authors (§8.2). Logs Op(PROVOKE_DIGEST).

Move 6 is the one move that lives in `analytics/` rather than `notelib`, and the import direction
is the reason: analytics may import notelib and never the reverse, so a move built on `Corpus`
(which already reads lineage, ratified binds and `visited` in one pass, and already honours
--as-of) has to sit there. This script is operational, so importing it is legal and precedented —
scripts/ui.py does the same. What it may not do, and does not, is let move 6 write anything.

Two limits keep the run affordable and its output human-sized:
  * it waits for the embed queue to drain before touching the vector layer (fail-open), and
    reads the nightly k-NN cache, so the run pays one ANN rebuild at most instead of one per
    seed — this is what turned a 36-minute unfinished run into a fast one;
  * it *renders* every candidate it found but *stages* only the strongest few (--stage-cap,
    --min-score). The digest is a surface to read; the queue is a list to decide. Capping the
    second without capping the first is the point: a queue nobody can clear ratifies nothing.

**The per-run cap does not bound the queue; the ceiling does** (--max-queue, v0.8.5). A cap of 10
a night is a *rate*, and a rate against a reader who ratifies in occasional bursts is still
unbounded — it only stops growing when expiry starts removing as fast as this adds, which on the
shipped 30-day age means a standing queue around 300, and means nothing at all for the first
month. So staging now asks how much room is left under notelib.SUGGESTION_MAX_QUEUE and takes the
smaller of that and the cap. When the queue is full the run stages nothing and says so: a digest
that goes quiet has to be legible as a full queue, or it reads as a broken job.

Run through the wrapper (scripts/provocation-digest.sh); meant to run nightly.
"""
import argparse
import json
import os
import sys
import notelib
from analytics import common, debt

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recent")
OUT_FILE = os.path.join(OUT_DIR, "provocations.md")

# Staging policy (I-5): how much of what the digest finds is allowed into the queue per run.
STAGE_CAP = 10           # suggestions staged per run, across all seeds and both moves
# Move-1 similarity floor — below this, "near" isn't near enough to be worth a decision. Measured
# against the 101-note corpus before picking a number: move-1 scores ran max 0.729, median 0.644,
# min 0.592, and a 0.70 floor admitted only 4 of 55 candidates. That makes the *floor* the binding
# constraint rather than STAGE_CAP, which quietly switches move 1 off — the opposite of the point,
# since volume is the cap's job. 0.65 sits at the knee (26 of 55) and leaves the cap in charge.
# Raise it if your corpus scores higher; --min-score overrides per run.
STAGE_MIN_SCORE = 0.65


def stage_budget(queued, stage_cap=STAGE_CAP, max_queue=notelib.SUGGESTION_MAX_QUEUE):
    """How many suggestions this run may stage: the per-run cap, or the room left under the
    queue ceiling, whichever is smaller. Never negative.

    Kept as a pure function of three numbers so the policy can be pinned without a corpus — the
    interesting cases (a queue already over the ceiling, a ceiling exactly met, a cap larger than
    the room left) are all ones the live graph will not be in on any particular day.

    `queued` is read once, before staging, rather than per suggestion. The loop stages at most the
    budget, so the queue cannot pass the ceiling within a run either way, and re-counting would
    only let a concurrent ratification quietly raise the budget mid-run."""
    return max(0, min(int(stage_cap), int(max_queue) - int(queued)))



def _hubs(db):
    """The structurally central note of each detected community — the highest-degree member,
    ties broken by id.

    Before v0.8.0 these were HUB_OF edges a human had drawn to a ratified Cluster. With clusters
    no longer stored, the hub is computed per run from the graph as it is (§13). It is a weaker
    claim — centrality is not the same as "this note names the theme" — but it is the right one
    for a *seed*: the digest wants well-connected places to look outward from, and a computed
    hub can never point at a theme that has since dissolved."""
    adj = notelib._undirected_adjacency(db)
    hubs = []
    for members in notelib.detect_communities(db):
        present = [m for m in members if m in adj]
        if present:
            hubs.append(max(sorted(present), key=lambda m: len(adj[m])))
    return hubs


def _seeds(db, limit):
    """Seed set: the most recent active notes + the hub of each detected community."""
    seeds = [r.get("id") for r in notelib.rows(db.query(
        f"SELECT id FROM Note WHERE status='active' AND embedding IS NOT NULL "
        f"ORDER BY id DESC LIMIT {int(limit)}")) if r.get("id")]
    return list(dict.fromkeys(seeds + _hubs(db)))     # dedupe, preserve order


def _addr(db, note_id):
    try:
        return notelib.folgezettel(db, note_id)
    except (notelib.ArcadeError, ValueError):
        return ""


def _stage_order(per_seed, min_score):
    """The order candidates are offered to the suggestion queue: strongest move-1 hits first.
    Returns [(seed_id, candidate), …]."""
    return sorted(((e["seed"], c) for e in per_seed for c in e["move1"]
                   if c.get("score", 0) >= min_score),
                  key=lambda pair: pair[1].get("score", 0), reverse=True)


def build(db, seeds_limit=10, k=5, stage=True, stage_cap=STAGE_CAP,
          min_score=STAGE_MIN_SCORE, max_queue=notelib.SUGGESTION_MAX_QUEUE,
          use_cache=True, wait=True, log=None):
    """Run the six moves and (optionally) stage the best move-1 suggestions, up to whichever is
    smaller of `stage_cap` and the room left under `max_queue`. Everything found is returned for
    rendering; only what fits the budget is staged. Returns a result dict."""
    lm = notelib.LinkManager(db)
    if wait:
        notelib.wait_for_embeddings(db=db, log=log)      # fail-open; see notelib
    seeds = _seeds(db, seeds_limit)
    per_seed = []
    for sid in seeds:
        entry = {"seed": sid, "addr": _addr(db, sid), "move1": []}
        try:
            entry["move1"] = notelib.move1_candidates(db, sid, k=k, use_cache=use_cache)
        except (ValueError, notelib.EmbedderError):
            pass
        if entry["move1"]:
            per_seed.append(entry)

    offered, staged = _stage_order(per_seed, min_score), 0
    for _sid, cand in offered:
        cand["staged"] = False               # found and rendered, but not (yet) in the queue
    queued = notelib.suggested_link_count(db)
    budget = stage_budget(queued, stage_cap, max_queue)
    if stage:
        for sid, cand in offered:
            if staged >= budget:
                break
            try:
                lm.suggest(sid, cand["id"], rationale="provocation-digest: near move")
            except notelib.LinkExists:
                continue                     # already proposed or ratified — costs no budget
            cand["staged"] = True
            staged += 1
    # Move 6 reads the whole graph once and touches neither the embeddings nor the clock, so it is
    # the cheapest move here. `move6_quiet` is filled only when the list is empty: "nothing is
    # owed" and "lineage is too shallow to measure" are different messages, and a digest read days
    # later has to be able to tell them apart (the v0.8.5 lesson about quiet runs).
    corpus = common.Corpus(db)
    move6 = debt.report(corpus, limit=k)
    return {"per_seed": per_seed, "staged": staged, "offered": len(offered),
            "stage_cap": stage_cap, "min_score": min_score,
            "queued_before": queued, "max_queue": max_queue, "budget": budget,
            "move2": notelib.move2_candidates(db, k=k),
            "move3": notelib.move3_candidates(db, k=k, use_cache=use_cache),
            "move4": notelib.move4_candidates(db, k=k),
            "move5": notelib.move5_candidates(db),
            "move6": move6,
            "move6_quiet": "" if move6 else debt.diagnosis(corpus)}


def render(db, res):
    L = [f"# Provocations — {notelib.format_id(notelib.now_utc())}", "",
         "_Generated by `scripts/provocation-digest.sh`; overwritten on each run. The machine "
         "proposes; you dispose (spec §8.2). Staged move-1 pairs await `link.sh ratify`._", ""]
    L += [f"_Staged **{res['staged']}** of {res['offered']} eligible pair(s) this run — cap "
          f"{res['stage_cap']}/run, move-1 floor {res['min_score']:.2f}, queue ceiling "
          f"{res['max_queue']} ({res['queued_before']} already awaiting a decision). Everything "
          f"found is listed below either way; only what is marked `staged` is in your "
          f"ratification queue._", ""]
    # A run that stages nothing has to say why in the artefact itself. The digest is read on its
    # own, days later, by someone who will otherwise conclude the job broke.
    if not res["budget"]:
        L += [f"> **The queue is full** — {res['queued_before']} suggestions are standing at a "
              f"ceiling of {res['max_queue']}, so nothing was staged this run. Nothing is lost: "
              f"every candidate is still listed below, and the digest starts proposing again as "
              f"soon as you ratify or reject enough to make room "
              f"(`bash scripts/link.sh`).", ""]
    elif res["budget"] < res["stage_cap"]:
        L += [f"> Staging was limited to **{res['budget']}** by the queue ceiling rather than by "
              f"the per-run cap of {res['stage_cap']}.", ""]

    L += ["## Move 1 — near-but-graph-far", ""]
    if not res["per_seed"]:
        L += ["_No per-seed candidates._", ""]
    for e in res["per_seed"]:
        head = f"### from `{e['seed']}`" + (f" · {e['addr']}" if e["addr"] else "")
        L += [head, ""]
        for c in e["move1"]:
            mark = " · _staged_" if c.get("staged") else ""
            L.append(f"- **near, graph-far** · `{c['id']}` · {c.get('score', 0):.3f} · "
                     f"{c.get('title') or '(untitled)'}{mark}  \n  {c.get('snippet', '')}")
        L.append("")

    L += ["## Move 2 — bridge candidates (join two clusters)", ""]
    L += ([f"- `{c['id']}` · spans {c['spans']} communities · degree {c['degree']} · "
           f"{c.get('title') or '(untitled)'}" for c in res["move2"]]
          or ["_None — needs ≥ 2 detected communities._"])
    L += [""]

    L += ["## Move 3 — implicit themes (write a hub note?)", ""]
    if not res["move3"]:
        L += ["_None — no unnamed semantic cluster large enough._"]
    for t in res["move3"]:
        L += [f"- theme around `{t['seed']}` ({t['size']} notes):"]
        L += [f"    - `{m['id']}` · {m.get('title') or '(untitled)'}" for m in t["members"]]
    L += [""]

    L += ["## Move 4 — contradiction / tension pairs (reconcile?)", ""]
    if not res["move4"]:
        L += ["_None — no ratified BINDS{inhibits} pairs._"]
    for p in res["move4"]:
        reason = f" · _{p['reason']}_" if p.get("reason") else ""
        L += [f"- **now** `{p['new']}` {p.get('new_title') or ''}{reason}  \n"
              f"  **was** `{p['old']}` {p.get('old_title') or ''}"]
    L += [""]

    m5 = res["move5"]
    L += ["## Move 5 — serendipitous re-encounter", ""]
    for label, bucket in (("Orphans (no ratified link)", "orphans"),
                          ("Inhibited (corrected, still re-encounterable)", "inhibited"),
                          ("On this day", "on_this_day")):
        L += [f"### {label}"]
        L += ([f"- `{c['id']}` · {c.get('title') or '(untitled)'}" for c in m5[bucket]]
              or ["_none_"])
        L += [""]

    # Last on purpose. Every other move offers something — a link to make, a tension to reconcile,
    # a note you had forgotten. This one says you owe work, so it goes at the bottom, stays capped,
    # and keeps its sentence flat rather than admonishing.
    L += ["## Move 6 — structural debt (the corpus grew out of these; you have not been back)", ""]
    if not res["move6"]:
        L += [f"_None — {res['move6_quiet']}._", ""]
    for r in res["move6"]:
        addr = _addr(db, r["id"])
        L += [f"- **{debt.prompt(r)}**  \n"
              f"  `{r['id']}`" + (f" · {addr}" if addr else "")
              + f" · debt {r['debt']:.2f} · {r['descendants']} descendant(s), "
                f"{r['binds']} bind(s), {r['visited']} walk(s)"]
    L += [""]
    return "\n".join(L).rstrip() + "\n"


def main():
    p = argparse.ArgumentParser(prog="provocation-digest",
                                description="Render all six provocation moves (spec §8.1, §12.6).")
    p.add_argument("-k", "--limit", type=int, default=5, help="candidates per move (default 5)")
    p.add_argument("--seeds", type=int, default=10, help="recent notes to seed from (default 10)")
    p.add_argument("--no-stage", action="store_true",
                   help="preview only — do not stage move-1 suggestions")
    p.add_argument("--stage-cap", type=int, default=STAGE_CAP, dest="stage_cap",
                   help=f"most suggestions to stage per run (default {STAGE_CAP})")
    p.add_argument("--min-score", type=float, default=STAGE_MIN_SCORE, dest="min_score",
                   help=f"move-1 similarity floor for staging (default {STAGE_MIN_SCORE}; "
                        f"move 2 is floored by its own max distance)")
    p.add_argument("--max-queue", type=int, default=notelib.SUGGESTION_MAX_QUEUE,
                   dest="max_queue",
                   help=f"stage nothing once this many suggestions are already awaiting a "
                        f"decision (default {notelib.SUGGESTION_MAX_QUEUE})")
    p.add_argument("--no-cache", action="store_true", dest="no_cache",
                   help="query the vector index live instead of the k-NN cache (slow: one ANN "
                        "rebuild per query if anything was embedded since)")
    p.add_argument("--no-wait", action="store_true", dest="no_wait",
                   help="do not wait for pending embeddings to settle first")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    def log(msg):
        print(f"[provocation-digest] {msg}", flush=True)

    db = notelib.Arcade()
    try:
        res = build(db, seeds_limit=args.seeds, k=args.limit, stage=not args.no_stage,
                    stage_cap=args.stage_cap, min_score=args.min_score,
                    max_queue=args.max_queue,
                    use_cache=not args.no_cache, wait=not args.no_wait, log=log)
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(OUT_FILE, "w", encoding="utf-8") as fh:
            fh.write(render(db, res))
        op_id = notelib.unique_id(db, notelib.format_id(notelib.now_utc()), "Op")
        notelib.insert_op(db, op_id, "PROVOKE_DIGEST",
                          {"seeds": len(res["per_seed"]), "staged": res["staged"],
                           "offered": res["offered"], "stage_cap": res["stage_cap"],
                           "min_score": res["min_score"],
                           "queued_before": res["queued_before"],
                           "max_queue": res["max_queue"], "budget": res["budget"],
                           "move2": len(res["move2"]), "move3": len(res["move3"]),
                           "move4": len(res["move4"]), "move6": len(res["move6"])})
    except notelib.ArcadeError as e:
        sys.exit(f"[provocation-digest] failed: {e}\n  SQL: {e.sql}")

    if args.json:
        print(json.dumps({"staged": res["staged"], "offered": res["offered"],
                          "stage_cap": res["stage_cap"], "budget": res["budget"],
                          "queued_before": res["queued_before"], "max_queue": res["max_queue"],
                          "seeds": len(res["per_seed"]), "out": OUT_FILE}, indent=2))
    else:
        # The limit that actually bound this run is named, because "staged 0" and "staged 10" are
        # both ordinary and only the reason tells them apart in a log read a week later.
        why = (f"queue full at {res['max_queue']}" if not res["budget"] else
               f"room for {res['budget']} under the ceiling of {res['max_queue']}"
               if res["budget"] < res["stage_cap"] else f"cap {res['stage_cap']}")
        print(f"[provocation-digest] wrote {OUT_FILE} · staged {res['staged']} of "
              f"{res['offered']} eligible suggestion(s) ({why}; {res['queued_before']} queued) "
              f"across {len(res['per_seed'])} seed(s)")


if __name__ == "__main__":
    main()
