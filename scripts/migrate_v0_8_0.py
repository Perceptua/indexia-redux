#!/usr/bin/env python3
"""migrate-v0-8-0 — separate analytics from operations in the stored graph.

The one-time migration to the v0.8.0 schema (spec §13). It makes the graph purely relational:

  * adds `Note.visited` (0 for every existing note — walks before v0.8.0 were not counted);
  * adds `BEGETS.created_at` and dates every existing lineage edge from its CHILD note, which
    is exact: link_begets runs inside the child's commit transaction, so the edge and the note
    share an instant;
  * drops `Note.fitness` and `Note.activation` — stored analytics the graph had no business
    carrying (fitness was write-only; activation was read by nothing but its own decay tick);
  * drops the Trace and Cluster vertex types and the five edge types that hung off them
    (VISITED, PRODUCED, FORKED_FROM, CONTAINS, HUB_OF, DERIVES_FROM).

Nothing here is recoverable from the graph afterwards, so it refuses to run without a recent
backup unless forced. Notes, BEGETS, BINDS, Op and KnnCache are never touched.

Idempotent and safe to re-run — it is also the repair path after restoring a pre-v0.8.0 backup.
Every step is guarded by a schema/null check, because this ArcadeDB build (26.7.3) is fussier
than its syntax suggests:

  * `DROP PROPERTY x.y IF EXISTS` still ERRORS when the property is absent — the IF EXISTS guard
    is not honoured — so properties are looked up in `schema:types` before dropping. (`DROP TYPE
    ... IF EXISTS` *is* correctly a no-op.)
  * `DROP TYPE` refuses a type that still holds records, so each type is emptied first.

Run through the wrapper (scripts/migrate-v0-8-0.sh). Logs Op(MIGRATE_V0_8_0).
"""
import argparse
import json
import os
import sys
import time

import notelib

# Vertex/edge types removed in v0.8.0. Edges first: dropping a vertex type whose edges still
# exist would leave the edge type pointing at nothing.
DROP_EDGE_TYPES = ("VISITED", "PRODUCED", "FORKED_FROM", "CONTAINS", "HUB_OF", "DERIVES_FROM")
DROP_VERTEX_TYPES = ("Trace", "Cluster")
DROP_NOTE_PROPS = ("fitness", "activation")

BACKUP_MAX_AGE_HOURS = 24


def type_names(db):
    """Every type name currently in the schema."""
    return {r.get("name") for r in notelib.rows(db.query("SELECT name FROM schema:types"))
            if r.get("name")}


def property_names(db, type_name):
    """Property names declared on `type_name`, or an empty set if the type is gone."""
    row = notelib.first_row(db.query("SELECT properties FROM schema:types WHERE name = :t",
                                     {"t": type_name}))
    return {p.get("name") for p in (row.get("properties") or []) if isinstance(p, dict)}


def count_of(db, type_name):
    """Row count for a type (0 if it does not exist)."""
    try:
        return notelib.first_row(db.query(f"SELECT count(*) AS n FROM {type_name}")).get("n") or 0
    except notelib.ArcadeError:
        return 0


def recent_backup(root):
    """Newest backup zip younger than BACKUP_MAX_AGE_HOURS, or None.

    Walks the tree: ArcadeDB writes into a per-database subdir
    (backups/<db>/<db>-backup-<ts>.zip), not flat into backups/.
    """
    backups = os.path.join(root, "backups")
    if not os.path.isdir(backups):
        return None
    newest, newest_age = None, None
    for dirpath, _dirnames, filenames in os.walk(backups):
        for name in filenames:
            if not name.endswith(".zip"):
                continue
            path = os.path.join(dirpath, name)
            age = (time.time() - os.path.getmtime(path)) / 3600.0
            if newest_age is None or age < newest_age:
                newest, newest_age = path, age
    return newest if newest and newest_age <= BACKUP_MAX_AGE_HOURS else None


def undated_begets(db):
    """(parent_id, child_id) for every BEGETS edge with no created_at."""
    return [(r.get("p"), r.get("c")) for r in notelib.rows(db.query(
        "SELECT outV().id AS p, inV().id AS c FROM BEGETS WHERE created_at IS NULL"))
        if r.get("p") and r.get("c")]


def plan(db):
    """What this run would do, as a dict — the shared body of --dry-run and the real thing."""
    types = type_names(db)
    note_props = property_names(db, "Note")
    begets_props = property_names(db, "BEGETS")
    return {
        "visited_property": "visited" in note_props,
        "begets_created_at": "created_at" in begets_props,
        "notes_without_visited": (
            count_of(db, "Note") if "visited" not in note_props else
            notelib.first_row(db.query(
                "SELECT count(*) AS n FROM Note WHERE visited IS NULL")).get("n") or 0),
        "begets_to_date": len(undated_begets(db)) if "created_at" in begets_props else
                          count_of(db, "BEGETS"),
        "drop_properties": [f"Note.{p}" for p in DROP_NOTE_PROPS if p in note_props],
        "drop_types": {t: count_of(db, t) for t in DROP_EDGE_TYPES + DROP_VERTEX_TYPES
                       if t in types},
    }


def migrate(db, dry_run=False):
    """Apply the migration. Returns a summary dict."""
    steps = plan(db)

    if not steps["visited_property"] or not steps["begets_created_at"]:
        sys.exit("[migrate-v0-8-0] Note.visited and/or BEGETS.created_at are missing — run\n"
                 "                  `bash scripts/apply-ddl.sh` first (it adds them), then re-run.")

    summary = {"visited_initialized": 0, "begets_dated": 0,
               "properties_dropped": [], "types_dropped": {}, "dry_run": dry_run}

    # 1. visited = 0 wherever it is null. A clean break: pre-v0.8.0 walk history is not counted
    #    (there were no Trace vertices left to count it from).
    if not dry_run and steps["notes_without_visited"]:
        summary["visited_initialized"] = notelib.first_row(db.command(
            "UPDATE Note SET visited = 0 WHERE visited IS NULL")).get("count", 0)
    else:
        summary["visited_initialized"] = steps["notes_without_visited"]

    # 2. Date the lineage edges from their child note. Done per-edge in Python rather than as a
    #    correlated UPDATE: ~100 edges, and it keeps the id -> datetime conversion in one place
    #    (notelib.id_to_created_at, the same helper backfill_link_dates.py uses).
    pairs = undated_begets(db)
    if not dry_run and pairs:
        with db.transaction():
            for parent_id, child_id in pairs:
                db.command(
                    "UPDATE BEGETS SET created_at = :at "
                    "WHERE outV().id = :p AND inV().id = :c AND created_at IS NULL",
                    {"at": notelib.id_to_created_at(child_id), "p": parent_id, "c": child_id})
    summary["begets_dated"] = len(pairs)

    # 3. Drop the stored analytics properties. Checked against the schema first — IF EXISTS is
    #    not honoured for DROP PROPERTY on this build.
    for prop in steps["drop_properties"]:
        if not dry_run:
            db.command(f"DROP PROPERTY {prop}")
        summary["properties_dropped"].append(prop)

    # 4. Empty, then drop, the removed types — DROP TYPE refuses a type holding records.
    for type_name, n in steps["drop_types"].items():
        if not dry_run:
            if n:
                db.command(f"DELETE FROM {type_name}")
            db.command(f"DROP TYPE {type_name} IF EXISTS")
        summary["types_dropped"][type_name] = n

    # 5. Log it. A migration is an operation, so it appends to the trail like everything else.
    summary["op_id"] = None
    touched = (summary["visited_initialized"] or summary["begets_dated"]
               or summary["properties_dropped"] or summary["types_dropped"])
    if not dry_run and touched:
        op_id = notelib.unique_id(db, notelib.format_id(notelib.now_utc()), "Op")
        with db.transaction():
            notelib.insert_op(db, op_id, "MIGRATE_V0_8_0",
                              {k: summary[k] for k in
                               ("visited_initialized", "begets_dated",
                                "properties_dropped", "types_dropped")})
        summary["op_id"] = op_id
    return summary


def main():
    p = argparse.ArgumentParser(
        prog="migrate-v0-8-0",
        description="Migrate the graph to the v0.8.0 purely-relational schema (one-time).")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="report what would change, write nothing")
    p.add_argument("--force", action="store_true",
                   help="proceed without a recent backup (destructive — Trace/Cluster are lost)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not args.dry_run and not args.force and not recent_backup(root):
        sys.exit(f"[migrate-v0-8-0] no backup newer than {BACKUP_MAX_AGE_HOURS}h in backups/ — "
                 f"this migration is irreversible.\n"
                 f"                  Run `bash scripts/backup.sh` first, or pass --force.")

    db = notelib.Arcade()
    try:
        summary = migrate(db, dry_run=args.dry_run)
    except notelib.ArcadeError as e:
        sys.exit(f"[migrate-v0-8-0] failed: {e}\n  SQL: {e.sql}")

    if args.json:
        print(json.dumps(summary, indent=2))
        return

    verb = "would set" if args.dry_run else "set"
    tag = "[migrate-v0-8-0]"
    print(f"{tag} {verb} visited = 0 on {summary['visited_initialized']} note(s)")
    print(f"{tag} {'would date' if args.dry_run else 'dated'} {summary['begets_dated']} "
          f"BEGETS edge(s) from their child note")
    if summary["properties_dropped"]:
        print(f"{tag} {'would drop' if args.dry_run else 'dropped'} "
              f"{', '.join(summary['properties_dropped'])}")
    if summary["types_dropped"]:
        for type_name, n in summary["types_dropped"].items():
            print(f"{tag} {'would drop' if args.dry_run else 'dropped'} type {type_name} "
                  f"({n} record(s))")
    if not summary["properties_dropped"] and not summary["types_dropped"]:
        print(f"{tag} no v0.7.0 types or properties left — already migrated")
    if summary["op_id"]:
        print(f"{tag} Op {summary['op_id']}")


if __name__ == "__main__":
    main()
