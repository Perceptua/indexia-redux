#!/usr/bin/env bash
# End-to-end smoke test: exercises the schema, the LSM_VECTOR index, and graph traversal.
# Creates SMOKE-* rows in the live DB and deletes them afterward.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
wait_ready 10 || die "server not reachable at $BASE_URL — run scripts/up.sh first"

log "running smoke test against '$DB' (temporary SMOKE-* rows)"
BASE_URL="$BASE_URL" DB="$DB" ARCADEDB_ROOT_PASSWORD="$ARCADEDB_ROOT_PASSWORD" python3 - <<'PY'
import base64, json, os, random, ssl, sys, time, urllib.request, urllib.error

BASE = os.environ["BASE_URL"]; DB = os.environ["DB"]; PW = os.environ["ARCADEDB_ROOT_PASSWORD"]
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
AUTH = "Basic " + base64.b64encode(f"root:{PW}".encode()).decode()

def call(endpoint, body):
    req = urllib.request.Request(
        f"{BASE}/api/v1/{endpoint}/{DB}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": AUTH}, method="POST")
    try:
        with urllib.request.urlopen(req, context=ctx) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} on /{endpoint}: {e.read().decode()}\n  SQL: {body.get('command')}")

def cmd(sql, params=None):
    b = {"language": "sql", "command": sql}
    if params is not None:
        b["params"] = params
    return call("command", b)

fails = 0
def check(name, ok):
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    fails += 0 if ok else 1

ts = str(int(time.time() * 1000))
a, b = f"SMOKE-A-{ts}", f"SMOKE-B-{ts}"
vecA = [round(random.random(), 6) for _ in range(1024)]
vecB = [round(random.random(), 6) for _ in range(1024)]
INS = ("INSERT INTO Note SET id = :id, body = :body, status = 'active', "
       "created_at = sysdate(), embedding = :emb, embedding_dim = 1024, embedding_model = 'smoke'")

try:
    cmd(INS, {"id": a, "body": "smoke note A", "emb": vecA})
    cmd(INS, {"id": b, "body": "smoke note B", "emb": vecB})
    r = cmd("SELECT count(*) AS n FROM Note WHERE id IN [:a, :b]", {"a": a, "b": b})
    check("inserted 2 Note vertices", r["result"][0]["n"] == 2)

    r = cmd("SELECT expand(vector.neighbors('Note[embedding]', :q, 5, 100))", {"q": vecA})
    ids = [row.get("id") for row in r.get("result", [])]
    check("vector.neighbors returns A for A's own vector", a in ids)

    cmd("CREATE EDGE BEGETS FROM (SELECT FROM Note WHERE id = :a) "
        "TO (SELECT FROM Note WHERE id = :b) SET mode = 'continues'", {"a": a, "b": b})
    r = cmd("SELECT expand(out('BEGETS')) FROM Note WHERE id = :a", {"a": a})
    check("BEGETS(A->B) is traversable", b in [row.get("id") for row in r.get("result", [])])
finally:
    try:
        cmd("DELETE VERTEX FROM Note WHERE id IN [:a, :b]", {"a": a, "b": b})
        print("  (cleanup: SMOKE-* rows deleted)")
    except SystemExit as e:
        print(f"  (cleanup warning: {e})")

print()
if fails:
    print(f"SMOKE TEST FAILED — {fails} check(s) failed"); sys.exit(1)
print("SMOKE TEST PASSED")
PY
