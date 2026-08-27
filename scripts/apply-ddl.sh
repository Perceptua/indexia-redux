#!/usr/bin/env bash
# Create the database if absent, then apply ddl/schema.sql. Idempotent: statements are
# applied individually and "already exists" errors are tolerated (this ArcadeDB build
# rejects IF NOT EXISTS on CREATE PROPERTY, so idempotency lives here, not in the DDL).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env

wait_ready 30 || die "server not reachable at $BASE_URL — run scripts/up.sh first"

# 1. Ensure the database exists.
if db_get "$BASE_URL/api/v1/databases" | grep -q "\"$DB\""; then
  log "database '$DB' already exists"
else
  log "creating database '$DB'"
  db_post "$BASE_URL/api/v1/server" "{\"command\":\"create database $DB\"}" >/dev/null \
    || die "failed to create database '$DB'"
  ok "database '$DB' created"
fi

# 2. Apply the schema statement-by-statement (idempotent).
log "applying ddl/schema.sql"
BASE_URL="$BASE_URL" DB="$DB" ARCADEDB_ROOT_PASSWORD="$ARCADEDB_ROOT_PASSWORD" \
  SCHEMA="$ROOT/ddl/schema.sql" python3 - <<'PY'
import base64, json, os, re, ssl, sys, urllib.request, urllib.error
BASE, DB, PW = os.environ["BASE_URL"], os.environ["DB"], os.environ["ARCADEDB_ROOT_PASSWORD"]
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
AUTH = "Basic " + base64.b64encode(f"root:{PW}".encode()).decode()

sql = re.sub(r'--.*', '', open(os.environ["SCHEMA"]).read())   # strip line comments (parser rejects them)
stmts = [s.strip() for s in sql.split(';') if s.strip()]

applied = skipped = 0
for st in stmts:
    req = urllib.request.Request(f"{BASE}/api/v1/command/{DB}",
        data=json.dumps({"language": "sql", "command": st}).encode(),
        headers={"Content-Type": "application/json", "Authorization": AUTH}, method="POST")
    try:
        urllib.request.urlopen(req, context=ctx).read(); applied += 1
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if re.search(r'already exist', body, re.I):
            skipped += 1
        else:
            sys.exit(f"FAILED on statement:\n  {st}\n  {body}")
print(f"  applied={applied}  skipped(existing)={skipped}  total={len(stmts)}")
PY
ok "schema applied"

# 3. Report the resulting types.
log "types now present:"
db_post "$BASE_URL/api/v1/query/$DB" '{"language":"sql","command":"SELECT name FROM schema:types ORDER BY name"}' \
  | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); names=[t.get("name") for t in d.get("result",[])]
    print("  " + (", ".join(n for n in names if n) or "(none)"))
except Exception as e: print("  (could not parse types:", e, ")")' || true
