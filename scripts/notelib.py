"""Shared core for Indexia note ingestion.

Everything that writes a note goes through one central path — `Ingestor.commit`
(scripts/add_note.py, scripts/ingest_staging.py, and the embed-backfill job all
call it), so embed-on-commit and the append-only `Op` log are automatic for every
workflow. Talks to the ArcadeDB HTTP API over TLS with the same loopback +
self-signed-cert posture as scripts/lib.sh.

Environment (exported by the bash wrappers after scripts/lib.sh `load_env`):
  BASE_URL                 e.g. https://localhost:12480
  DB                       e.g. indexia
  ARCADEDB_ROOT_PASSWORD   root password
Embedding config (all optional; sensible defaults):
  INDEXIA_EMBED_BACKEND    ollama | none            (default: ollama)
  INDEXIA_EMBED_MODEL      Ollama model name        (default: mxbai-embed-large)
  INDEXIA_EMBED_DIM        expected dim / index dim (default: 1024)
  OLLAMA_HOST              Ollama base URL           (default: http://localhost:11434)
  INDEXIA_EMBED_TIMEOUT    per-request seconds       (default: 120)

Note fields set by ingestion are the human-facing columns of ddl/schema.sql §20-25
(id, title, body, created_at, author, source_ref); `status` defaults to 'active'
(ADD_NOTE inserts an active note, §12.3); embedding/embedding_model/embedding_dim
are written at commit when an embedder is available (spec §7, §12.6 embed-on-commit).
"""
import base64
import contextlib
import json
import os
import random
import re
import ssl
import time
import urllib.error
import urllib.request
from collections import namedtuple
from datetime import datetime, timedelta, timezone

try:
    import fcntl                     # POSIX file locking (WSL/Linux); used to serialize
except ImportError:                  # vector queries. Non-Unix → the lock degrades to a no-op.
    fcntl = None

# ---- id / timestamp (spec §4) -----------------------------------------------
# Identity is a compact UTC timestamp to the millisecond, basic ISO-8601:
#     20260715T234214123Z
# Globally unique, chronologically sortable, literally the creation instant.
# Sub-millisecond collisions append a two-digit counter after the Z (§4).
ID_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(\d{3})Z(\d{2})?$")

ID_SHAPE_HINT = (
    "expected the spec §4 shape YYYYMMDDTHHMMSSmmmZ — a compact UTC timestamp to "
    "the millisecond, e.g. 20260715T234214123Z (optionally + a two-digit counter)"
)

# Human-facing Note columns that ingestion may set (ddl/schema.sql §20-25).
FIELDS = ("id", "title", "body", "created_at", "author", "source_ref")
DEFAULT_AUTHOR = "human"        # authorship is mainly human (spec §5)
DEFAULT_STATUS = "active"       # ADD_NOTE inserts Note(active) (spec §12.3)


def now_utc():
    return datetime.now(timezone.utc)


def format_id(dt):
    """Render a datetime as the compact id: 20260715T234214123Z."""
    return dt.strftime("%Y%m%dT%H%M%S") + f"{dt.microsecond // 1000:03d}Z"


def format_created_at(dt):
    """Render created_at as ISO-8601 with a 'T' separator, millis, and 'Z'.

    ArcadeDB only preserves milliseconds when the string is 'T'-separated; the
    space+millis form (`2026-07-20 13:45:12.123`) is silently stored as null
    (validated against the live server). Keep the 'T'.
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt.microsecond // 1000:03d}Z"


def safe_created_at(v):
    """Guard a user-supplied created_at against the space+millis trap: ArcadeDB
    silently stores `2026-07-20 13:45:12.123` as null, but the 'T'-separated form
    keeps the millis. Swap the date/time space for a 'T' when a fractional second
    is present; leave everything else untouched."""
    if " " in v and "." in v:
        v = v.replace(" ", "T", 1)
    return v


def validate_id(s):
    """Return `s` unchanged if it matches the spec §4 shape AND is a real
    calendar instant; otherwise raise ValueError."""
    m = ID_RE.match(s or "")
    if not m:
        raise ValueError(f"invalid note id {s!r}: {ID_SHAPE_HINT}")
    y, mo, d, h, mi, se, ms, _counter = m.groups()
    try:
        datetime(int(y), int(mo), int(d), int(h), int(mi), int(se),
                 int(ms) * 1000, tzinfo=timezone.utc)
    except ValueError as e:
        raise ValueError(f"note id {s!r} is not a valid instant: {e}")
    return s


def id_to_created_at(s):
    """Derive the created_at string from an id (the same instant, spec §4)."""
    m = ID_RE.match(s or "")
    if not m:
        raise ValueError(f"cannot derive created_at from malformed id {s!r}")
    y, mo, d, h, mi, se, ms, _counter = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{se}.{ms}Z"


def id_to_dt(s):
    """Parse a compact note id into a timezone-aware UTC datetime (spec §4)."""
    m = ID_RE.match(s or "")
    if not m:
        raise ValueError(f"cannot parse id {s!r}: {ID_SHAPE_HINT}")
    y, mo, d, h, mi, se, ms, _counter = m.groups()
    return datetime(int(y), int(mo), int(d), int(h), int(mi), int(se),
                    int(ms) * 1000, tzinfo=timezone.utc)


def _dt_digits(s):
    """Canonical YYYYMMDDHHMMSSmmm digit key of any timestamp-ish string, so a compact
    note id ('20260723T115735449Z') and a DB-round-tripped DATETIME ('2026-07-23 13:42:10.130'
    — space-separated, no Z) compare correctly. Slices to 17 digits, dropping any id
    collision-counter or format punctuation."""
    return "".join(ch for ch in str(s or "") if ch.isdigit())[:17]


def age_in_days(timestamp):
    """Whole days from a timestamp-ish value (a note id or a round-tripped DATETIME) to now, or
    None if it carries no parseable instant — which is how a null created_at reads."""
    digits = _dt_digits(timestamp)
    if len(digits) < 14:
        return None
    born = datetime.strptime(digits[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return max(0, (now_utc() - born).days)


# ---- ArcadeDB HTTP client ---------------------------------------------------
class ArcadeError(RuntimeError):
    """A non-2xx response from the ArcadeDB HTTP API."""

    def __init__(self, code, detail, sql):
        self.code, self.detail, self.sql = code, detail, sql
        super().__init__(f"HTTP {code}: {detail}")


# Optimistic-concurrency retry (spec §12.6 embed-on-commit runs the worker alongside ingest):
# ArcadeDB is MVCC, so an INSERT and a concurrent UPDATE landing on the same storage page race
# on the page version and one commit is rejected with ConcurrentModificationException — the
# server explicitly asks the client to retry. `Arcade.atomically` retries the whole transaction.
CONFLICT_RETRIES = 4       # attempts beyond the first before giving up (bounded, so a real jam surfaces)
CONFLICT_BACKOFF = 0.05    # base seconds; grows exponentially with jitter per attempt


def _is_conflict(e):
    """True for the one ArcadeError worth retrying — an MVCC page-version conflict. Matched on
    the exception name (not a bare HTTP code) so genuine failures still fail fast."""
    return isinstance(e, ArcadeError) and "ConcurrentModification" in (e.detail or "")


class Arcade:
    def __init__(self):
        missing = [k for k in ("BASE_URL", "DB", "ARCADEDB_ROOT_PASSWORD")
                   if not os.environ.get(k)]
        if missing:
            raise SystemExit(
                f"missing env {', '.join(missing)} — run via the bash wrapper "
                f"(e.g. scripts/add-note.sh) or export them yourself")
        self.base = os.environ["BASE_URL"].rstrip("/")
        self.db = os.environ["DB"]
        pw = os.environ["ARCADEDB_ROOT_PASSWORD"]
        self.auth = "Basic " + base64.b64encode(f"root:{pw}".encode()).decode()
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE
        self._session = None   # active transaction session id, if any

    def _request(self, path, body=None):
        headers = {"Authorization": self.auth}
        data = b""
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        if self._session:
            headers["arcadedb-session-id"] = self._session
        req = urllib.request.Request(f"{self.base}/api/v1/{path}", data=data,
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, context=self.ctx) as r:
                return r.status, r.headers.get("arcadedb-session-id"), r.read().decode()
        except urllib.error.HTTPError as e:
            sql = body.get("command") if isinstance(body, dict) else path
            raise ArcadeError(e.code, e.read().decode(), sql)
        except urllib.error.URLError as e:
            raise SystemExit(
                f"cannot reach ArcadeDB at {self.base} — is it up? (scripts/up.sh)  "
                f"[{e.reason}]")

    def command(self, sql, params=None):
        """Run a mutating statement (INSERT/UPDATE/CREATE …)."""
        body = {"language": "sql", "command": sql}
        if params is not None:
            body["params"] = params
        return json.loads(self._request(f"command/{self.db}", body)[2])

    def query(self, sql, params=None):
        """Run a read-only query."""
        body = {"language": "sql", "command": sql}
        if params is not None:
            body["params"] = params
        return json.loads(self._request(f"query/{self.db}", body)[2])

    @contextlib.contextmanager
    def transaction(self):
        """Wrap the body in one ArcadeDB transaction (HTTP begin/commit session).
        Commits on clean exit, rolls back on any exception."""
        _, sid, _ = self._request(f"begin/{self.db}")
        if not sid:
            raise SystemExit("ArcadeDB did not return arcadedb-session-id on begin")
        self._session = sid
        committed = False
        try:
            yield
            self._request(f"commit/{self.db}")
            committed = True
        finally:
            if not committed:
                try:
                    self._request(f"rollback/{self.db}")
                except Exception:
                    pass
            self._session = None

    def atomically(self, do, retries=CONFLICT_RETRIES):
        """Run `do()` inside one transaction, retrying the WHOLE transaction on an MVCC
        conflict (ConcurrentModificationException). The conflict is usually raised at commit,
        i.e. as the `with` block exits, so `return do()` stays inside it. On conflict the tx has
        already rolled back, so a retry re-runs from clean state — nothing partial persisted.
        Backoff grows exponentially with jitter to spread out contending writers; a persistent
        conflict past `retries` re-raises so it isn't hidden. Returns whatever `do()` returns."""
        for attempt in range(retries + 1):
            try:
                with self.transaction():
                    return do()
            except ArcadeError as e:
                if attempt < retries and _is_conflict(e):
                    time.sleep(CONFLICT_BACKOFF * (2 ** attempt) * (0.5 + random.random()))
                    continue
                raise


def first_row(resp):
    """First row of an ArcadeDB result envelope, or {} if empty."""
    rows_ = resp.get("result", []) if isinstance(resp, dict) else []
    return rows_[0] if rows_ else {}


def rows(resp):
    return resp.get("result", []) if isinstance(resp, dict) else []


# ---- embedding (spec §7, §10; embed-on-commit §12.6) ------------------------
Embedding = namedtuple("Embedding", "vector model dim")


class EmbedderError(RuntimeError):
    """Any failure to produce an embedding. Under fail-open ingestion this is
    caught and the note commits without an embedding (queued for backfill)."""


class EmbedderUnavailable(EmbedderError):
    """The embedder could not be reached / the model is not ready (transient)."""


class OllamaEmbedder:
    """Local, private embeddings via an Ollama server (default mxbai-embed-large,
    1024-dim). Construction is cheap and does not connect; `embed` connects."""

    def __init__(self, model=None, host=None, dim=None, timeout=None):
        self.model = model or os.environ.get("INDEXIA_EMBED_MODEL", "mxbai-embed-large")
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.expected_dim = int(dim or os.environ.get("INDEXIA_EMBED_DIM", "1024"))
        # Generous default: a cold model load (~669MB, --no-mmap) can take a while;
        # a warm model embeds in well under a second. embed-server.sh keeps it warm.
        self.timeout = float(timeout or os.environ.get("INDEXIA_EMBED_TIMEOUT", "120"))

    def _post(self, path, payload):
        """POST JSON to the Ollama server; returns the parsed response. Shared error
        translation for embed() and embed_batch() — both hit this same unreachable/HTTP-error
        shape, only the payload and response parsing differ."""
        req = urllib.request.Request(f"{self.host}{path}", data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:200]
            raise EmbedderUnavailable(
                f"Ollama {self.host} returned HTTP {e.code}: {detail} "
                f"(is the model pulled? `ollama pull {self.model}`)")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise EmbedderUnavailable(
                f"Ollama unreachable at {self.host}: {e} "
                f"(start it with scripts/embed-server.sh)")

    def _to_embedding(self, vec):
        if self.expected_dim and len(vec) != self.expected_dim:
            raise EmbedderError(
                f"embedding dim {len(vec)} != expected {self.expected_dim} for model "
                f"{self.model!r}; the LSM_VECTOR index is built for {self.expected_dim}-dim "
                f"(change INDEXIA_EMBED_DIM + the index, or pick a matching model)")
        return Embedding(vec, self.model, len(vec))

    def embed(self, text):
        data = self._post("/api/embeddings", {"model": self.model, "prompt": text})
        vec = data.get("embedding")
        if not vec:
            raise EmbedderUnavailable(
                f"Ollama returned no embedding for model {self.model!r} "
                f"(pull it: `ollama pull {self.model}`)")
        return self._to_embedding(vec)

    def embed_batch(self, texts):
        """Like embed(), but one HTTP round trip for many texts (Ollama's /api/embed, the
        batch sibling of /api/embeddings — plural 'embeddings' in the response) instead of
        one call per text. Under load the round trip + per-call model-scheduling overhead
        dominates wall-clock time as much as raw FLOPs, so batching notes a poll already has
        pending is a real win even single-threaded (-np 1) on the server side. Returns a list
        of Embedding, same order as `texts`. Raises EmbedderUnavailable/EmbedderError for the
        whole batch — one HTTP request either returns all N vectors or none, so a partial
        failure has no meaning at this layer."""
        if not texts:
            return []
        data = self._post("/api/embed", {"model": self.model, "input": list(texts)})
        vecs = data.get("embeddings")
        if not vecs or len(vecs) != len(texts):
            raise EmbedderUnavailable(
                f"Ollama returned {len(vecs or [])} embedding(s) for {len(texts)} input(s) "
                f"(model {self.model!r})")
        return [self._to_embedding(v) for v in vecs]


def make_embedder():
    """Construct the configured embedder, or None when embedding is turned off."""
    backend = os.environ.get("INDEXIA_EMBED_BACKEND", "ollama").strip().lower()
    if backend in ("", "none", "off"):
        return None
    if backend == "ollama":
        return OllamaEmbedder()
    raise SystemExit(f"unknown INDEXIA_EMBED_BACKEND={backend!r} (use 'ollama' or 'none')")


def embed_on_commit_sync():
    """True when a commit should embed inline (blocking). Default is 'async': the
    note commits immediately with no embedding, and the background embed-worker
    (scripts/embed-worker.sh) embeds it shortly after. Set INDEXIA_EMBED_ON_COMMIT=sync
    to embed inline instead."""
    return os.environ.get("INDEXIA_EMBED_ON_COMMIT", "async").strip().lower() == "sync"


# ---- note construction ------------------------------------------------------
class DuplicateNote(ValueError):
    def __init__(self, note_id):
        self.note_id = note_id
        super().__init__(
            f"a note with id {note_id} already exists (the corpus is append-only; "
            f"revise by adding a new note and binding it back with "
            f"`link.sh suggest <new> {note_id} --mode inhibits`, not by re-adding, spec §6)")


def build_note(fields):
    """Normalize a partial field dict into a complete, insertable note.

    Returns (note, generated_id) where generated_id is True when the id was
    auto-created (callers may then bump it on collision; a user-supplied id that
    collides is an error, not something to silently rename).

    - body is required (Note.body is MANDATORY/NOTNULL).
    - id is validated when supplied, else generated from the current instant.
    - created_at defaults to the id's instant (spec §4: the same instant).
    - author defaults to 'human' (§5); status defaults to 'active' (§12.3).
    """
    note = {k: v.strip() if isinstance(v, str) else v
            for k, v in fields.items() if v is not None and v != ""}

    if not note.get("body"):
        raise ValueError("body is required (Note.body is MANDATORY/NOTNULL, spec §6)")

    generated_id = False
    if note.get("id"):
        validate_id(note["id"])
    else:
        dt = now_utc()
        note["id"] = format_id(dt)
        note.setdefault("created_at", format_created_at(dt))
        generated_id = True

    if note.get("created_at"):
        note["created_at"] = safe_created_at(note["created_at"])
    else:
        note["created_at"] = id_to_created_at(note["id"])

    note.setdefault("author", DEFAULT_AUTHOR)
    note.setdefault("status", DEFAULT_STATUS)
    return note, generated_id


def _count(db, typ, node_id):
    r = db.query(f"SELECT count(*) AS n FROM {typ} WHERE id = :id", {"id": node_id})
    return first_row(r).get("n", 0)


def note_exists(db, note_id):
    return _count(db, "Note", note_id) > 0


def unique_id(db, node_id, typ="Note"):
    """Return node_id, or the next free id with a two-digit collision counter (§4)."""
    if _count(db, typ, node_id) == 0:
        return node_id
    m = ID_RE.match(node_id)
    base = node_id[:-2] if m and m.group(8) else node_id
    for i in range(100):
        candidate = f"{base}{i:02d}"
        if _count(db, typ, candidate) == 0:
            return candidate
    raise ValueError(f"could not find a free {typ} id near {node_id} after 100 collisions")


def insert_note(db, note, emb=None):
    """INSERT the note (bound params; only present columns are set). When `emb`
    is given, its vector + model + dim are written too. Returns the id."""
    # visited starts at 0 (spec §3.1, §13) so walk recording never has to distinguish
    # "never walked" from null. It is the only non-relational number on a Note.
    cols = ["id = :id", "body = :body", "created_at = :created_at",
            "author = :author", "status = :status", "visited = 0"]
    params = {k: note[k] for k in ("id", "body", "created_at", "author", "status")}
    for opt in ("title", "source_ref"):
        if note.get(opt):
            cols.append(f"{opt} = :{opt}")
            params[opt] = note[opt]
    if emb is not None:
        cols += ["embedding = :embedding", "embedding_model = :embedding_model",
                 "embedding_dim = :embedding_dim"]
        params["embedding"] = list(emb.vector)
        params["embedding_model"] = emb.model
        params["embedding_dim"] = emb.dim
    db.command("INSERT INTO Note SET " + ", ".join(cols), params)
    return note["id"]


def insert_op(db, op_id, rule, payload):
    """Append one Op to the rewrite-log (spec §11.2, §12.3). `payload` is a dict,
    stored as a JSON string."""
    db.command("INSERT INTO Op SET id = :id, rule = :rule, payload = :payload",
               {"id": op_id, "rule": rule, "payload": json.dumps(payload, ensure_ascii=False)})
    return op_id


# ---- structural edges (spec §3.2, §4) ---------------------------------------
# BEGETS carries lineage: parent -> child, mode ∈ {continues, branches} (§4). It is the ONLY
# edge the ingestion layer may create, and it is semantically neutral — it says where a note
# came from, never what it claims. All sign lives on BINDS (§3.2, §7).
MODES = {"continue": "continues", "continues": "continues",
         "branch": "branches", "branches": "branches"}

# BINDS.mode is the corpus's only semantic vocabulary (§7). None is legal and neutral: the
# machine proposes untyped, and the human may assign a mode at ratification (or never).
BIND_MODES = {"catalyzes": "catalyzes", "catalyze": "catalyzes",
              "inhibits": "inhibits", "inhibit": "inhibits"}

# The sign a mode carries (§7). Read by the analytics layer (fitness, catalysis, autocatalysis);
# since v0.8.0 it accumulates nowhere in the graph itself. Untyped ⇒ 0.
BIND_SIGN = {"catalyzes": 1, "inhibits": -1}


def normalize_mode(m):
    key = (m or "").strip().lower()
    if key not in MODES:
        raise ValueError(f"invalid lineage mode {m!r}: use 'continues' or 'branches' (spec §4)")
    return MODES[key]


def normalize_bind_mode(m, allow_none=True):
    """Canonicalize a BINDS mode. '' / None / 'none' -> None (untyped, neutral) when allowed."""
    key = (m or "").strip().lower()
    if key in ("", "none", "untyped"):
        if allow_none:
            return None
        raise ValueError("a mode is required here: use 'catalyzes' or 'inhibits' (spec §7)")
    if key not in BIND_MODES:
        raise ValueError(f"invalid bind mode {m!r}: use 'catalyzes', 'inhibits' "
                         f"or 'none' for an untyped link (spec §7)")
    return BIND_MODES[key]


def bind_sign(mode):
    """+1 for catalyzes, -1 for inhibits, 0 for untyped (§7)."""
    return BIND_SIGN.get(mode, 0)


def resolve_link(parent, mode):
    """Validate an optional BEGETS parent link. Returns (parent_id, mode) or
    (None, None); raises if only one of the pair is given or either is malformed."""
    if bool(parent) != bool(mode):
        raise ValueError("parent and mode go together — give both (a parent id + "
                         "continues/branches) or neither")
    if not parent:
        return None, None
    return validate_id(parent), normalize_mode(mode)


def link_begets(db, parent_id, child_id, mode):
    """BEGETS edge parent -> child (lineage). Run inside a transaction.

    `created_at` (v0.8.0) is the CHILD's instant, derived from its id (§4) rather than from the
    clock. For a live commit the two are the same — this runs inside the child's transaction — but
    they diverge whenever a note is ingested with a back-dated id, which is exactly what
    `ingest-staging` does when rebuilding a corpus from `staging/`. Reading the clock there would
    stamp every lineage edge with the rebuild date and silently break `--as-of` (§13): the notes
    would date from July, the edges joining them from whenever the rebuild happened."""
    db.command("CREATE EDGE BEGETS FROM (SELECT FROM Note WHERE id = :p) "
               "TO (SELECT FROM Note WHERE id = :c) SET mode = :m, created_at = :at",
               {"p": parent_id, "c": child_id, "m": mode,
                "at": id_to_created_at(child_id)})


# ---- Folgezettel address derivation (spec §4) -------------------------------
# The Luhmann citation address (e.g. "1a2") is a DISPLAY-ONLY projection of the
# BEGETS tree, computed on demand and NEVER stored (§4). Depth alternates
# numeric/alphabetic (1 -> 1a -> 1a1 -> …); a `continues` hop increments the last
# segment, a `branches` hop appends a new one. Among same-mode siblings the render
# orders by id and increments deterministically — the address is display-only, so
# determinism (not Luhmann-exact numbering) is the only contract (§4). Endpoints are
# read via outV()/inV() (out.id projects null on this build, as in LinkManager).
def _alpha(n):
    """1-based index -> spreadsheet-style lowercase alpha: 1->a, 26->z, 27->aa."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(97 + r) + s
    return s


def _segment(depth, rank):
    """Render the `rank`-th (1-based) segment at `depth`: even depth numeric, odd alpha."""
    return str(rank) if depth % 2 == 0 else _alpha(rank)


def _load_lineage(db):
    """Snapshot the whole lineage in two queries: (set of all note ids, {child_id:
    (parent_id, mode)}). folgezettel is then pure-Python, so computing one address never
    fans out into a query per ancestor. mode is normalized to continues/branches."""
    ids = {r.get("id") for r in rows(db.query("SELECT id FROM Note")) if r.get("id")}
    parent = {}
    for e in rows(db.query("SELECT outV().id AS p, inV().id AS c, mode AS m FROM BEGETS")):
        child, par = e.get("c"), e.get("p")
        if child and par:
            parent[child] = (par, normalize_mode(e.get("m") or "continues"))
    return ids, parent


def _fz_depth_anchor(parent, note_id):
    """(depth, anchor) for a note in the in-memory lineage: `depth` = number of `branches`
    edges on the path from the root (a `continues` stays on the level), and `anchor` = the
    source note of the *last* branches edge on that path — the note this note's level hangs
    under — or None at the top level. Two notes share a level iff they share (depth, anchor)."""
    depth, anchor = 0, None
    node, seen = note_id, {note_id}
    while node in parent:
        pid, mode = parent[node]
        if mode == "branches":
            depth += 1
            if anchor is None:          # first branches met walking up = last on the way down
                anchor = pid
        if pid in seen:                 # defensive: lineage is a tree, but never loop forever
            break
        seen.add(pid)
        node = pid
    return depth, anchor


def folgezettel(db, note_id):
    """Derive a note's Luhmann citation address (e.g. '1a2') from its BEGETS ancestry
    (spec §4) — a display-only projection, never stored. Returns '' for an unknown note.

    Addresses are unique per note. (An earlier scheme incremented a shared segment, so a
    root's continues-chain walked 2→3→4 straight through the numbers other roots already
    held — 78/101 test notes collided.) Here every note is ranked within its *level* — the
    notes sharing its (depth, anchor) — by id, then prefixed with its anchor's address.
    Because each level is one shared id-ordered sequence and distinct anchors have distinct
    addresses, no two notes can collide. `continues` keeps a note on its parent's level
    (siblings 1,2,3 / a,b,c); `branches` descends a level (append a segment, alternating
    numeric/alpha by depth). Two queries + pure Python; cheap at personal scale."""
    validate_id(note_id)
    ids, parent = _load_lineage(db)
    if note_id not in ids:
        return ""
    da = {nid: _fz_depth_anchor(parent, nid) for nid in ids}
    levels = {}
    for nid, key in da.items():
        levels.setdefault(key, []).append(nid)
    for key in levels:
        levels[key].sort()               # id order is the deterministic tie-break (§4)
    memo = {}

    def addr(nid):
        if nid not in memo:
            depth, anchor = da[nid]
            rank = levels[(depth, anchor)].index(nid) + 1
            prefix = "" if anchor is None else addr(anchor)   # anchor is at depth-1: terminates
            memo[nid] = prefix + _segment(depth, rank)
        return memo[nid]

    return addr(note_id)


# ---- NO activation wave since v0.8.0 (spec §13) -----------------------------
# Ratifying a typed BINDS used to fire a signed wave backward along the target's BEGETS lineage,
# accumulating into Note.activation at 0.5/hop out to 3 hops. That was analytics written into the
# corpus: a number every note carried about itself, derived entirely from edges that were already
# there, and drifting from them the moment a bind was retyped. The sign still exists — it is
# BINDS.mode, and nothing else (§7) — but it is now READ when a question is asked rather than
# accumulated as notes are judged. Anything the wave measured is recoverable from the graph by
# scripts/analytics/, including as of a past instant.
#
# The one attention signal that survives on the note is `visited`, and it is not derived from the
# graph at all: it counts human-directed walks, which nothing else records (§13).


# ---- the central commit path ------------------------------------------------
# begets = (parent_id, mode) or None. Ingestion creates no other edge (§3.2).
CommitResult = namedtuple(
    "CommitResult",
    "note_id op_id rule embedded embedding_model warning begets")

# CORRECT_COSMETIC (§6, §12.3): the one in-place mutation — a typo/formatting fix whose meaning
# is unchanged, sanity-checked by embedding drift. Drift ≥ the threshold ⇒ meaning changed ⇒ the
# edit is refused (issue a correction instead). Threshold is a reversible tuning constant (§10).
CORRECT_COSMETIC_MAX_DRIFT = 0.15
CorrectResult = namedtuple("CorrectResult", "note_id op_id rule drift max_drift reembedded")


class CosmeticDriftError(ValueError):
    """A CORRECT_COSMETIC edit that changed meaning (embedding drift ≥ the threshold)."""

    def __init__(self, note_id, drift, max_drift):
        self.note_id, self.drift, self.max_drift = note_id, drift, max_drift
        super().__init__(
            f"refusing in-place edit of {note_id}: embedding drift {drift:.4f} ≥ {max_drift} — this "
            f"changes meaning, not just cosmetics. Issue a correction instead: add the corrected "
            f"note, then `link.sh suggest <new> {note_id} --mode inhibits` and ratify it (§6).")


_UNSET = object()


class Ingestor:
    """Central ingestion path shared by every workflow.

    `commit` performs, for each note: build/normalize → resolve id uniqueness →
    embed the body (best-effort, fail-open) → atomically insert the Note and an
    `Op(rule='ADD_NOTE')` in one transaction (spec §12.3: every state change is a
    rule that also appends an Op). If the embedder is unavailable the note still
    commits, without an embedding, and is picked up later by scripts/embed-backfill.sh.
    """

    def __init__(self, db=None, embedder=_UNSET):
        self.db = db or Arcade()
        self.embedder = make_embedder() if embedder is _UNSET else embedder

    def embed(self, text):
        """Return an Embedding or None, never raising: fail-open by contract."""
        if self.embedder is None:
            return None, "embedding disabled (INDEXIA_EMBED_BACKEND=none)"
        try:
            return self.embedder.embed(text), None
        except EmbedderError as e:
            return None, str(e)

    def commit(self, fields, embed=None, parent=None, mode=None):
        """Build, embed (optional), and atomically insert Note + Op(ADD_NOTE),
        optionally with a BEGETS parent link (spec §12.3). Returns a CommitResult.
        Raises DuplicateNote for a supplied id that exists, ValueError for bad input,
        ArcadeError for a DB failure (tx rolled back).

        embed=None (default) consults INDEXIA_EMBED_ON_COMMIT: 'async' (the default)
        commits without embedding (the background worker embeds it later); 'sync'
        embeds inline. embed=True/False forces it for this call.
        parent+mode add a BEGETS edge parent->new (mode continues|branches). This is the
        ONLY edge ingestion creates: a correction is a plain note plus a ratified
        BINDS{inhibits} added afterwards through the ratification flow (§6, §7)."""
        parent, mode = resolve_link(parent, mode)
        note = self._prepare(fields, parent)

        emb, warning = self._maybe_embed(note, embed)
        op_id = unique_id(self.db, format_id(now_utc()), "Op")
        payload = _add_note_payload(note, emb)
        if parent:
            payload["parent_id"], payload["mode"] = parent, mode
        def _do():                                     # one atomic unit; retried on MVCC conflict
            insert_note(self.db, note, emb)
            if parent:
                link_begets(self.db, parent, note["id"], mode)
            insert_op(self.db, op_id, "ADD_NOTE", payload)
        self.db.atomically(_do)
        return CommitResult(note_id=note["id"], op_id=op_id, rule="ADD_NOTE",
                            embedded=emb is not None, embedding_model=(emb.model if emb else None),
                            warning=warning, begets=((parent, mode) if parent else None))

    def _prepare(self, fields, parent):
        """Build + id-resolve a new note, and check the parent link target exists."""
        note, generated = build_note(fields)
        if generated:
            note["id"] = unique_id(self.db, note["id"], "Note")
        elif note_exists(self.db, note["id"]):
            raise DuplicateNote(note["id"])
        if parent and not note_exists(self.db, parent):
            raise ValueError(f"parent note {parent} does not exist (check the id, or add it first)")
        return note

    def _maybe_embed(self, note, embed):
        if embed is None:
            embed = embed_on_commit_sync()
        return self.embed(note["body"]) if embed else (None, None)

    def pending_note_ids(self, limit=0):
        """Notes still missing an embedding, oldest first."""
        sql = "SELECT id, body FROM Note WHERE embedding IS NULL AND body IS NOT NULL ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return rows(self.db.query(sql))

    def _write_embedding(self, note_id, emb):
        """Persist an already-computed embedding for note_id + log Op(EMBED), atomically.
        Shared by embed_existing and embed_existing_many — the DB write is per-note either
        way, so a batch is just N of these plus one shared embedder call."""
        op_id = unique_id(self.db, format_id(now_utc()), "Op")
        with self.db.transaction():
            self.db.command(
                "UPDATE Note SET embedding = :e, embedding_model = :m, embedding_dim = :d "
                "WHERE id = :id",
                {"e": list(emb.vector), "m": emb.model, "d": emb.dim, "id": note_id})
            insert_op(self.db, op_id, "EMBED",
                      {"note_id": note_id, "embedding_model": emb.model, "embedding_dim": emb.dim})

    def embed_existing(self, note_id, body):
        """Embed an already-committed note and log Op(EMBED), atomically. Raises
        EmbedderError/EmbedderUnavailable if no vector could be produced. Shared by
        embed-backfill (one-shot) and embed-worker (background loop)."""
        if self.embedder is None:
            raise EmbedderUnavailable("embedding disabled (INDEXIA_EMBED_BACKEND=none)")
        emb = self.embedder.embed(body)
        self._write_embedding(note_id, emb)
        return emb

    def embed_existing_many(self, notes):
        """Embed several already-committed notes in ONE Ollama call (embed_batch) instead of
        one call per note — that single call is where the wall-clock win is; each note still
        gets its own UPDATE + Op(EMBED) transaction, so a DB-level failure on one note (MVCC
        conflict, concurrent delete) skips only that note and leaves it pending for the next
        poll, same isolation embed_existing gives a single caller. `notes` is [{id, body}, …]
        (pending_note_ids' own shape). Raises EmbedderUnavailable/EmbedderError — unchanged
        from embed_batch — only when the embedder call itself fails; that failure is
        necessarily whole-batch (one HTTP request, all or nothing). Returns (embedded,
        skipped): embedded = [(note_id, Embedding), …], skipped = [(note_id, error_str), …],
        both in `notes` order."""
        if self.embedder is None:
            raise EmbedderUnavailable("embedding disabled (INDEXIA_EMBED_BACKEND=none)")
        if not notes:
            return [], []
        embs = self.embedder.embed_batch([n["body"] for n in notes])
        embedded, skipped = [], []
        for n, emb in zip(notes, embs):
            try:
                self._write_embedding(n["id"], emb)
                embedded.append((n["id"], emb))
            except ArcadeError as e:
                skipped.append((n["id"], str(e)))
        return embedded, skipped

    def correct_cosmetic(self, note_id, new_body, max_drift=CORRECT_COSMETIC_MAX_DRIFT):
        """CORRECT_COSMETIC ○ (spec §6, §12.3) — the ONLY in-place mutation: fix a typo/formatting
        in `body` without changing meaning, sanity-checked by embedding drift. If the note has a
        stored embedding and an embedder is available, re-embed the new body and require
        drift = 1 − cos(old, new) < max_drift, else raise CosmeticDriftError (meaning changed → issue
        a correction instead). The fresh embedding replaces the old one (the body changed). When drift can't be
        measured (embedder off, or note pending-embed) the human declaration is trusted. Logs
        Op(CORRECT_COSMETIC). Returns a CorrectResult; a no-op (body unchanged) has op_id=None."""
        validate_id(note_id)
        row = first_row(self.db.query(
            "SELECT body, embedding FROM Note WHERE id = :id", {"id": note_id}))
        if not row:
            raise ValueError(f"no note {note_id} to correct")
        new_body = (new_body or "").strip()
        if not new_body:
            raise ValueError("body is required (Note.body is MANDATORY/NOTNULL, spec §6)")
        if new_body == row.get("body"):
            return CorrectResult(note_id, None, "CORRECT_COSMETIC", 0.0, max_drift, False)
        old_emb, drift, new_emb = row.get("embedding"), None, None
        if self.embedder is not None:
            try:
                new_emb = self.embedder.embed(new_body)
            except EmbedderError:
                new_emb = None                          # can't sanity-check; trust the human (§6)
            if new_emb is not None and old_emb:
                drift = round(1.0 - _cosine(old_emb, new_emb.vector), 6)
                if drift >= max_drift:
                    raise CosmeticDriftError(note_id, drift, max_drift)
        op_id = unique_id(self.db, format_id(now_utc()), "Op")
        with self.db.transaction():
            cols, params = ["body = :b"], {"b": new_body, "id": note_id}
            if new_emb is not None:
                cols += ["embedding = :e", "embedding_model = :m", "embedding_dim = :d"]
                params.update({"e": list(new_emb.vector), "m": new_emb.model, "d": new_emb.dim})
            self.db.command("UPDATE Note SET " + ", ".join(cols) + " WHERE id = :id", params)
            insert_op(self.db, op_id, "CORRECT_COSMETIC",
                      {"note_id": note_id, "drift": drift, "max_drift": max_drift,
                       "reembedded": new_emb is not None})
        return CorrectResult(note_id, op_id, "CORRECT_COSMETIC", drift, max_drift,
                             new_emb is not None)


def _add_note_payload(note, emb):
    """Compact JSON args for the ADD_NOTE Op — references the note by id rather
    than duplicating its body (the body lives on the Note)."""
    payload = {"note_id": note["id"], "author": note["author"], "status": note["status"]}
    for opt in ("title", "source_ref"):
        if note.get(opt):
            payload[opt] = note[opt]
    payload["embedded"] = emb is not None
    if emb is not None:
        payload["embedding_model"] = emb.model
        payload["embedding_dim"] = emb.dim
    return payload


# ---- read / search (the difference-engine's read side, spec §8) -------------
# Everything above writes; the rest reads. Two access paths the search CLI
# (scripts/search.py) and the provocation moves (§8.1) share: the semantic layer
# (vector similarity) and the metadata/temporal layer (lexical filters).
SNIPPET_LEN = 160
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def snippet(body, n=SNIPPET_LEN):
    """A one-line preview of a note body: whitespace collapsed, clipped with an ellipsis."""
    text = " ".join((body or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def embed_query(text, embedder=None):
    """Embed a free-text query into the note embedding space for semantic search
    (spec §8, §8.1). Reuses the note embedder so a query and the notes are directly
    comparable. Raises EmbedderError when embedding is off (INDEXIA_EMBED_BACKEND=none)
    or the embedder is unreachable — semantic search can't fall back, the query vector
    must match the LSM_VECTOR index the notes were embedded into."""
    if embedder is None:
        embedder = make_embedder()
    if embedder is None:
        raise EmbedderError(
            "semantic search needs an embedder, but INDEXIA_EMBED_BACKEND=none "
            "(set it to 'ollama' and start scripts/embed-server.sh)")
    return embedder.embed(text).vector


def note_embedding(db, note_id):
    """The stored embedding vector for a note, or None if it has none yet
    (pending-embed). Backs 'find notes like this one' (search --like)."""
    return first_row(db.query("SELECT embedding FROM Note WHERE id = :id",
                              {"id": note_id})).get("embedding")


# Serialize semantic (vector.neighbors) queries across processes. Adding a vector to the
# corpus invalidates the LSM_VECTOR graph, so two searches running at once each trigger
# their own full ANN rebuild (observed in testing: two concurrent searches → two interleaved
# 100-vector rebuilds, doubling the work). A blocking exclusive file lock funnels vector
# queries through one at a time. Overridable/inspectable via env; defaults beside the other
# ~/.indexia runtime state (scheduler-state, logs).
VECTOR_QUERY_LOCK = os.path.expanduser(
    os.environ.get("INDEXIA_VECTOR_LOCK", os.path.join("~", ".indexia", "vector-query.lock")))


@contextlib.contextmanager
def vector_query_lock():
    """Hold an exclusive cross-process lock for the duration of a vector query. Degrades to
    a no-op if fcntl is unavailable (non-Unix) or the lock file can't be opened — serializing
    reads must never be what stops a search from running."""
    if fcntl is None:
        yield
        return
    fh = None
    try:
        os.makedirs(os.path.dirname(VECTOR_QUERY_LOCK), exist_ok=True)
        fh = open(VECTOR_QUERY_LOCK, "w")
        fcntl.flock(fh, fcntl.LOCK_EX)
    except OSError:
        if fh is not None:
            fh.close()
        yield                               # can't lock → proceed rather than block note-taking
        return
    try:
        yield
    finally:
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


# The other half of the same LSM_VECTOR problem the lock above solves. A job that interleaves
# embedding with vector queries pays a full ANN rebuild *per query*, because each new vector
# invalidates the graph; waiting for the embed queue to drain first means the whole run pays one
# rebuild and then fast (~3 s) queries. Measured at N=101: the five-move digest ran 36 min
# unfinished the interleaved way. Callers are the scheduled jobs (§12.6), never interactive ones.
EMBED_SETTLE_TIMEOUT = 600.0    # seconds to wait for the embed queue to drain before giving up
EMBED_SETTLE_POLL = 10.0        # seconds between pending-count checks


def wait_for_embeddings(db=None, timeout=EMBED_SETTLE_TIMEOUT, poll=EMBED_SETTLE_POLL, log=None):
    """Block until no note is missing an embedding, so the caller's vector queries run against
    a corpus that has stopped moving. Returns (settled, pending) — `settled` False means the
    timeout expired with `pending` notes still unembedded.

    **Fail-open by contract**: this is an optimization, not a gate on the work happening. A
    stopped embed worker, a disabled embedder, or one note whose body the embedder keeps
    rejecting must never stop the nightly jobs from running — the caller logs the pending count
    and proceeds. `log` is an optional callable taking one message string."""
    ing = Ingestor(db=db, embedder=None)          # counting only — no embedder handshake needed
    deadline = time.monotonic() + max(0.0, timeout)
    waited = False
    while True:
        pending = len(ing.pending_note_ids())
        if pending == 0:
            if waited and log:
                log("embed queue drained — vector queries will pay one ANN rebuild, not many")
            return True, 0
        if time.monotonic() >= deadline:
            if log:
                log(f"proceeding with {pending} note(s) still unembedded after {timeout:.0f}s "
                    f"— expect a slow first vector query (is the embed worker running?)")
            return False, pending
        if not waited and log:
            log(f"waiting for {pending} pending embedding(s) to settle before querying vectors")
        waited = True
        time.sleep(min(poll, max(0.0, deadline - time.monotonic())))


def semantic_search(db, vector, k=10, ef=100, exclude_ids=()):
    """Nearest notes to `vector` by cosine similarity — the semantic layer (§8, §12.2).

    Wraps ArcadeDB's `vector.neighbors('Note[embedding]', q, k, ef)` (HNSW ANN over the
    LSM_VECTOR index). That returns {record:<vertex>, distance:<cosine>} rows, so we
    expand() and project the vertex through record.* alongside the distance (the shape
    validated against the live 26.7.3 server; plain `expand()` alone drops the score).
    `score` = 1 - distance (1.0 == identical, higher == nearer). Notes without an
    embedding cannot match and simply do not appear; an empty corpus yields [].

    `k`/`ef` are coerced to int and inlined because the ANN function wants literal args;
    the query vector is always a bound param. We over-fetch by len(exclude_ids) (the ANN
    call has no server-side filter) and drop the excluded ids after. The query runs under
    `vector_query_lock()` so concurrent searches don't each rebuild the ANN graph."""
    exclude = set(exclude_ids)
    want = int(k)
    over = want + len(exclude)              # over-fetch, then drop excluded ids client-side
    ef_i = max(int(ef), over)               # efSearch must be >= the candidate count requested
    sql = ("SELECT record.id AS id, record.title AS title, record.status AS status, "
           "record.body AS body, distance "
           f"FROM (SELECT expand(vector.neighbors('Note[embedding]', :q, {over}, {ef_i})))")
    with vector_query_lock():
        resp = db.query(sql, {"q": list(vector)})
    hits = []
    for r in rows(resp):
        nid = r.get("id")
        if nid is None or nid in exclude:
            continue
        dist = float(r.get("distance") or 0.0)
        hits.append({"id": nid, "title": r.get("title"), "status": r.get("status"),
                     "body": r.get("body"), "snippet": snippet(r.get("body")),
                     "score": round(1.0 - dist, 6)})
        if len(hits) >= want:
            break
    return hits


# ---- k-NN adjacency cache (the vector layer, materialized) -------------------
# `vector.neighbors` is correct but expensive to *reach*: one new embedding invalidates the whole
# LSM_VECTOR graph, so the next call rebuilds all N vectors. The five-move digest asks for
# neighbours once per seed, so interleaved with ingest it paid that rebuild over and over. The
# nightly knn-cache job (scripts/knn_cache.py) pays it once and writes every embedded note's top-k
# neighbours to the KnnCache document type; the provocation moves then read the cache through
# `neighbors_of`, which falls back to a live query whenever the cache cannot answer. The cache is
# derived data — dropping it costs nothing but time, and it never becomes a source of truth.
KNN_CACHE_K = 50                # neighbours materialized per note: generous, because callers
                                # filter (move 1 subtracts the graph-near set) before taking k
KNN_CACHE_CHUNK = 100           # rows per multi-row INSERT when writing the cache back


def _hydrate_notes(db, ids):
    """{id: row} of title/status/body for a batch of note ids — one query (a list param binds
    fine on 26.7.3). The cache stores only ids + scores, so the text a caller renders is always
    read live and can never go stale against the corpus."""
    if not ids:
        return {}
    return {r["id"]: r for r in rows(db.query(
        "SELECT id, title, status, body FROM Note WHERE id IN :ids", {"ids": list(ids)}))
        if r.get("id")}


def knn_cached_neighbors(db, note_id):
    """The cached [(neighbour_id, score), …] for a note, nearest first — or None when the note
    has no cache row (never built, or embedded since the last rebuild). A row whose JSON is
    unreadable is treated as absent rather than fatal: a corrupt cache must degrade to a live
    query, not break provocation."""
    raw = first_row(db.query("SELECT neighbors FROM KnnCache WHERE note_id = :n",
                             {"n": note_id})).get("neighbors")
    if raw is None:
        return None
    try:
        pairs = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return [(p[0], float(p[1])) for p in pairs
            if isinstance(p, (list, tuple)) and len(p) == 2 and p[0]]


def neighbors_of(db, note_id, k=5, ef=100, exclude_ids=(), use_cache=True):
    """Nearest notes to an already-embedded note — the read path the provocation moves share.

    Serves from KnnCache when it can, hydrating titles/bodies from Note so the rendered text is
    current, and falls back to a live `semantic_search` only when the cache genuinely cannot
    answer: no cache row, caching disabled, or a truncated cached list from which the caller's
    exclusions removed *everything*. Row shape matches semantic_search exactly, so callers cannot
    tell which path served them.

    **A short cached answer is preferred to a complete live one**, and that is a deliberate
    trade. A cached list truncated at KNN_CACHE_K can hide further neighbours past its tail, so
    after a big exclusion set (move 1 subtracts the whole graph-near neighbourhood) the cache may
    yield fewer than `k`. Falling back for those would undo the point of caching: one
    heavily-linked seed drags the entire nightly run through a full ANN rebuild — measured, when
    an earlier version fell back on `len(kept) < k` and turned an 8-second digest into 654
    seconds. Three provocations now beat five in eleven minutes. When the cached list is *shorter*
    than KNN_CACHE_K it is the complete neighbour set anyway, so even an empty result is exact."""
    exclude = set(exclude_ids)
    if use_cache:
        pairs = knn_cached_neighbors(db, note_id)
        if pairs is not None:
            kept = [(nid, sc) for nid, sc in pairs if nid != note_id and nid not in exclude][:k]
            if kept or len(pairs) < KNN_CACHE_K:
                notes = _hydrate_notes(db, [nid for nid, _ in kept])
                hits = []
                for nid, sc in kept:
                    n = notes.get(nid)
                    if n is None:
                        continue                  # note deleted since the rebuild — skip it
                    hits.append({"id": nid, "title": n.get("title"), "status": n.get("status"),
                                 "body": n.get("body"), "snippet": snippet(n.get("body")),
                                 "score": round(sc, 6)})
                return hits
    vec = note_embedding(db, note_id)
    if vec is None:
        if not note_exists(db, note_id):
            raise ValueError(f"no note with id {note_id}")
        raise ValueError(f"note {note_id} has no embedding yet (pending-embed) — it must be "
                         f"embedded before it can provoke")
    return semantic_search(db, vec, k=k, ef=ef, exclude_ids=exclude)


KNN_STATUS_SAMPLE = 3           # ids named in a staleness reason before it says "and N more"


def _id_sample(ids):
    """A few ids from a set, oldest first, for a human-readable reason string. Bounded, because
    the first rebuild of an unbuilt cache is short of every note in the corpus."""
    ordered = sorted(ids)
    shown = ", ".join(ordered[:KNN_STATUS_SAMPLE])
    rest = len(ordered) - KNN_STATUS_SAMPLE
    return f"{shown}, and {rest} more" if rest > 0 else shown


def knn_cache_status(db):
    """Coverage and staleness of the k-NN cache, as a dict with a `stale` flag and human
    `reasons`. Stale when the set of embedded notes no longer matches the set of cached ones
    (a note added, or one that lost its embedding), or when a
    CORRECT_COSMETIC landed after the build on a note that is still here — the one rewrite that
    changes a note's vector without changing the id set (§6), so the id comparison alone would
    miss it. An empty corpus is not stale: there is nothing to cache."""
    embedded = {r.get("id") for r in rows(db.query(
        "SELECT id FROM Note WHERE embedding IS NOT NULL")) if r.get("id")}
    cached = {r.get("note_id") for r in rows(db.query("SELECT note_id FROM KnnCache"))
              if r.get("note_id")}
    built_at = first_row(db.query("SELECT max(built_at) AS m FROM KnnCache")).get("m")
    reasons = []
    # Named, not just counted. "1 embedded note(s) not in the cache" tells you the size of the
    # gap and nothing about where it came from; the id says at a glance whether a note was just
    # written or whether something removed a row that should still be there.
    if embedded - cached:
        reasons.append(f"{len(embedded - cached)} embedded note(s) not in the cache "
                       f"({_id_sample(embedded - cached)})")
    if cached - embedded:
        reasons.append(f"{len(cached - embedded)} cached note(s) no longer embedded "
                       f"({_id_sample(cached - embedded)})")
    if not reasons and built_at:
        # DATETIME round-trips space-separated without a 'Z', so compare digit keys, never
        # the raw strings ('T' > ' ' would make every id look newer than every built_at).
        recut = first_row(db.query(
            "SELECT max(id) AS m FROM Op WHERE rule = 'CORRECT_COSMETIC'")).get("m")
        if recut and _dt_digits(recut) > _dt_digits(built_at):
            if _corrected_since(db, _dt_digits(built_at)):
                reasons.append("a note was re-embedded in place (CORRECT_COSMETIC) after the "
                               "build")
    return {"embedded": len(embedded), "cached": len(cached), "built_at": built_at,
            "stale": bool(reasons), "reasons": reasons}


def _corrected_since(db, cutoff):
    """True if any note that STILL EXISTS was corrected in place after `cutoff` (a digit key).

    The id-set comparison above cannot see a CORRECT_COSMETIC — it rewrites a vector without
    changing which notes are embedded (§6) — so the Op log is the only witness. But a note that
    was corrected and then deleted took its vector with it, and the cache it would have
    invalidated is whole again; reading `max(id)` alone would call the cache stale forever on the
    strength of a note that is gone. Only ops past the cut are re-read, and only when the cheap
    max() has already said there is one.
    """
    for r in rows(db.query("SELECT id, payload FROM Op WHERE rule = 'CORRECT_COSMETIC'")):
        if _dt_digits(r.get("id")) <= cutoff:
            continue
        try:
            note_id = json.loads(r.get("payload") or "{}").get("note_id")
        except (TypeError, ValueError):
            continue                       # an unreadable payload cannot name a live note
        if note_id and note_exists(db, note_id):
            return True
    return False


def build_knn_cache(db, k=KNN_CACHE_K, ef=None, progress=None):
    """Rebuild the whole k-NN adjacency cache — one row per embedded note, its top-`k`
    neighbours as JSON [[id, score], …].

    The first `semantic_search` here absorbs the LSM_VECTOR rebuild for the whole run; every
    later one is fast, which is the entire point of the cache. Written back as truncate +
    multi-row INSERT inside one transaction, so a concurrent reader sees either the old cache
    or the new one, never a half-built one. `progress` is an optional callable (done, total,
    note_id). Returns a summary dict."""
    ids = [r.get("id") for r in rows(db.query(
        "SELECT id FROM Note WHERE embedding IS NOT NULL ORDER BY id")) if r.get("id")]
    built_at = format_created_at(now_utc())
    entries = []
    for done, nid in enumerate(ids, 1):
        vec = note_embedding(db, nid)
        if vec is not None:
            hits = semantic_search(db, vec, k=k, ef=max(int(ef or 0), 2 * k, 100),
                                   exclude_ids=(nid,))
            entries.append((nid, json.dumps([[h["id"], h["score"]] for h in hits])))
        if progress:
            progress(done, len(ids), nid)
    with db.transaction():
        db.command("DELETE FROM KnnCache")
        for start in range(0, len(entries), KNN_CACHE_CHUNK):
            chunk = entries[start:start + KNN_CACHE_CHUNK]
            vals, params = [], {"t": built_at}
            for j, (nid, nbrs) in enumerate(chunk):
                vals.append(f"(:n{j}, :b{j}, :t)")
                params[f"n{j}"], params[f"b{j}"] = nid, nbrs
            db.command("INSERT INTO KnnCache (note_id, neighbors, built_at) VALUES "
                       + ", ".join(vals), params)
    return {"embedded": len(ids), "cached": len(entries), "k": k, "built_at": built_at}


def _day_bound(v, what, plus_days=0):
    """Validate a YYYY-MM-DD day and return the 'YYYY-MM-DD 00:00:00' string that
    date() parses. Bound as a param, never interpolated (unlike recent_notes.py, this
    value is user-supplied) — date(:p) with a bound param is validated on 26.7.3.

    `plus_days` shifts the returned midnight forward, which is how an *inclusive* upper
    bound is expressed against a `created_at < bound` comparison: --until D becomes
    midnight of D+1, so the whole of day D is in range."""
    if not DAY_RE.match(v or ""):
        raise ValueError(f"--{what} must be a date as YYYY-MM-DD (got {v!r})")
    if plus_days:
        day = datetime.strptime(v, "%Y-%m-%d") + timedelta(days=plus_days)
        v = day.strftime("%Y-%m-%d")
    return f"{v} 00:00:00"


def lexical_search(db, *, id_prefix=None, text=None, title=None, body=None, source=None,
                   author=None, since=None, until=None, limit=25):
    """Non-semantic locator over the metadata + temporal layers (spec §12.2).

    Filters AND together. `text` = case-insensitive substring in body OR title; `title`,
    `body` and `source` are the same substring match narrowed to one field each, so a
    caller that knows *where* it is looking can say so — the graph UI's search panel is
    the one that does (scripts/ui.py). `id_prefix` matches the compact-timestamp id, so a
    day like '20260722' selects that whole day. `since`/`until` are YYYY-MM-DD (UTC) and
    **both inclusive of the named day**, so `since=D, until=D` is all of day D — the
    natural reading of a closed range. Internally the upper bound stays a `<` against
    midnight of until+1, because that is the created_at range that uses the index (a raw
    string-vs-DATETIME comparison does not; see recent_notes.py). Newest-first by id.

    A null field simply does not match: `source_ref` is nullable and `null LIKE '%x%'` is
    false on this build, so `source=` narrows to the notes that carry one rather than
    needing an IS NOT NULL guard of its own."""
    where, params = [], {}
    if id_prefix:
        where.append("id LIKE :idp"); params["idp"] = id_prefix + "%"
    if text:
        where.append("(body.toLowerCase() LIKE :txt OR title.toLowerCase() LIKE :txt)")
        params["txt"] = "%" + text.lower() + "%"
    if title:
        where.append("title.toLowerCase() LIKE :ttl"); params["ttl"] = "%" + title.lower() + "%"
    if body:
        where.append("body.toLowerCase() LIKE :bdy"); params["bdy"] = "%" + body.lower() + "%"
    if source:
        where.append("source_ref.toLowerCase() LIKE :src")
        params["src"] = "%" + source.lower() + "%"
    if author:
        where.append("author = :author"); params["author"] = author
    if since:
        where.append("created_at >= date(:since)"); params["since"] = _day_bound(since, "since")
    if until:
        # inclusive of the named day: compare < midnight of the *next* day
        where.append("created_at < date(:until)")
        params["until"] = _day_bound(until, "until", plus_days=1)
    sql = "SELECT id, title, status, body, created_at, author, source_ref FROM Note"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    if limit is not None:              # None = unlimited, for callers that post-filter (§6)
        sql += f" LIMIT {int(limit)}"
    return [{"id": r.get("id"), "title": r.get("title"), "status": r.get("status"),
             "body": r.get("body"), "snippet": snippet(r.get("body")),
             "created_at": r.get("created_at"), "author": r.get("author"),
             "source_ref": r.get("source_ref")}
            for r in rows(db.query(sql, params or None))]


# ---- associative links: BINDS + the ratification flow (spec §7, §12.3) -------
# The first provocation loop: the machine SUGGESTs a BINDS edge; the human RATIFIEs
# it, or REJECTs it — a proposal is not corpus, so deletion is allowed (§2.4). Each
# is a rewrite rule logged as an Op (§11.2).
#
# BINDS carries the corpus's ONLY semantics, in `mode`: `catalyzes` (excitatory —
# this note feeds that one across a generational gap) and `inhibits` (the correction
# signal that replaced the SUPERSEDES edge in v0.7.0, §6). `mode` is nullable and a
# null one is inert: the machine can spot affinity but never judge, so it proposes
# untyped and the human assigns a mode at ratification, or never (§7, §10).
#
# Edge mechanics validated on this 26.7.3 build: out.id/in.id project null, so
# endpoints are addressed via outV()/inV(); there is no DELETE EDGE statement, so
# edges are removed with `DELETE FROM <edge>` (no dangling adjacency — checked).
LinkResult = namedtuple("LinkResult", "rule a b status mode op_id")


class LinkExists(ValueError):
    def __init__(self, a, b, status):
        self.a, self.b, self.status = a, b, status
        super().__init__(
            f"a BINDS edge between {a} and {b} already exists (status={status})")


class LinkManager:
    """The BINDS ratification flow (spec §7): SUGGEST_LINK ● (machine proposes), then
    RATIFY_LINK / RETYPE_LINK / REJECT_LINK ○ (human disposes). Mirrors Ingestor — every
    mutation runs in one transaction that also appends its Op (§12.3).

    Every method here writes edge properties and nothing else. Ratifying a typed edge is
    still the corpus's one signed event, but since v0.8.0 the sign lands on the edge and
    stops there: no wave, no accumulated per-note number. What the judgement means for a
    note is a question for scripts/analytics/, asked when someone asks it (§13)."""

    def __init__(self, db=None):
        self.db = db or Arcade()

    def suggest(self, a, b, rationale=None, mode=None):
        """SUGGEST_LINK ● — stage a BINDS{status:suggested} a→b and log Op(SUGGEST_LINK).
        Raises LinkExists if a bind already exists in either direction (one relation per
        pair), ValueError for a missing/self/malformed id or an unknown mode.

        `mode` defaults to None (untyped, neutral) — that is what the provocation digest
        stages. Stamps `created_at` (the one and only write of it — the edge's birth
        instant), which is what ages the suggestion queue for the expiry sweep. Same
        'T'-separated format as Note.created_at: space+millis is silently stored as null."""
        self._check(a, b)
        mode = normalize_bind_mode(mode)
        who_a, status = self._between(a, b)
        if who_a is not None:
            raise LinkExists(a, b, status)
        op_id = unique_id(self.db, format_id(now_utc()), "Op")
        with self.db.transaction():
            self.db.command(
                "CREATE EDGE BINDS FROM (SELECT FROM Note WHERE id=:a) "
                "TO (SELECT FROM Note WHERE id=:b) "
                "SET status='suggested', created_at=:at, mode=:m, rationale=:r",
                {"a": a, "b": b, "at": format_created_at(now_utc()),
                 "m": mode, "r": rationale})
            insert_op(self.db, op_id, "SUGGEST_LINK",
                      {"a": a, "b": b, "mode": mode, "rationale": rationale})
        return LinkResult("SUGGEST_LINK", a, b, "suggested", mode, op_id)

    def ratify(self, a, b, mode=_UNSET, rationale=None):
        """RATIFY_LINK ○ — flip the a↔b BINDS to status:ratified, optionally setting its
        `mode` (and `rationale`) in the same act. Logs Op(RATIFY_LINK).

        Resolves the edge in EITHER direction — `suggest` refuses a duplicate either way,
        so requiring the caller to remember which way round it was stored is a trap. The
        stored direction is what is returned and what the claim is read off, because with
        `inhibits` the direction IS the claim (§6)."""
        edge = self._edge(a, b)
        if edge is None:
            raise ValueError(f"no BINDS edge between {a} and {b} to ratify "
                             f"(list candidates: link.sh list --status suggested)")
        src, dst, status, cur_mode = edge
        new_mode = cur_mode if mode is _UNSET else normalize_bind_mode(mode)
        if status == "ratified" and new_mode == cur_mode:
            return LinkResult("RATIFY_LINK", src, dst, "ratified", cur_mode, None)
        op_id = unique_id(self.db, format_id(now_utc()), "Op")
        sets = ["status='ratified'", "mode=:m"]
        params = {"a": src, "b": dst, "m": new_mode}
        if rationale is not None:
            sets.append("rationale=:r")
            params["r"] = rationale
        def _do():
            self.db.command(f"UPDATE BINDS SET {', '.join(sets)} "
                            "WHERE outV().id=:a AND inV().id=:b", params)
            insert_op(self.db, op_id, "RATIFY_LINK",
                      {"a": src, "b": dst, "was": status, "was_mode": cur_mode,
                       "mode": new_mode})
        self.db.atomically(_do)
        return LinkResult("RATIFY_LINK", src, dst, "ratified", new_mode, op_id)

    def retype(self, a, b, mode):
        """RETYPE_LINK ○ — change an existing bind's `mode` (including to None, untyped).
        A plain relabel whatever the edge's status: since v0.8.0 the mode is read where it
        matters rather than accumulated, so changing it needs no compensating write.
        Logs Op(RETYPE_LINK)."""
        edge = self._edge(a, b)
        if edge is None:
            raise ValueError(f"no BINDS edge between {a} and {b} to retype")
        src, dst, status, cur_mode = edge
        new_mode = normalize_bind_mode(mode)
        if new_mode == cur_mode:
            return LinkResult("RETYPE_LINK", src, dst, status, cur_mode, None)
        op_id = unique_id(self.db, format_id(now_utc()), "Op")
        def _do():
            self.db.command("UPDATE BINDS SET mode=:m WHERE outV().id=:a AND inV().id=:b",
                            {"a": src, "b": dst, "m": new_mode})
            insert_op(self.db, op_id, "RETYPE_LINK",
                      {"a": src, "b": dst, "status": status, "was_mode": cur_mode,
                       "mode": new_mode})
        self.db.atomically(_do)
        return LinkResult("RETYPE_LINK", src, dst, status, new_mode, op_id)

    def reject(self, a, b):
        """REJECT_LINK — delete the a↔b BINDS (a proposal is not corpus, §2.4) and log
        Op(REJECT_LINK). Primarily for `suggested` edges; also un-links a ratified one, in
        which case the judgement simply stops being part of the graph — nothing to unwind."""
        edge = self._edge(a, b)
        if edge is None:
            raise ValueError(f"no BINDS edge between {a} and {b} to reject")
        src, dst, status, cur_mode = edge
        op_id = unique_id(self.db, format_id(now_utc()), "Op")
        def _do():
            self.db.command("DELETE FROM BINDS WHERE outV().id=:a AND inV().id=:b",
                            {"a": src, "b": dst})
            insert_op(self.db, op_id, "REJECT_LINK",
                      {"a": src, "b": dst, "was": status, "was_mode": cur_mode})
        self.db.atomically(_do)
        return LinkResult("REJECT_LINK", src, dst, "deleted", cur_mode, op_id)

    def list(self, status=None, mode=_UNSET, limit=100):
        """BINDS edges with their endpoint ids/titles, optionally filtered by status and/or
        mode (pass mode=None to select only untyped edges). `created_at` is null on edges
        staged before the property existed (see scripts/link_expiry.py — undated edges are
        never swept)."""
        sql = ("SELECT status, mode, rationale, created_at, outV().id AS a, inV().id AS b, "
               "outV().title AS a_title, inV().title AS b_title FROM BINDS")
        where, params = [], {}
        if status:
            where.append("status = :s")
            params["s"] = status
        if mode is not _UNSET:
            m = normalize_bind_mode(mode)
            where.append("mode IS NULL" if m is None else "mode = :m")
            if m is not None:
                params["m"] = m
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" LIMIT {int(limit)}"
        return rows(self.db.query(sql, params or None))

    def _edge(self, a, b):
        """(src, dst, status, mode) of the BINDS edge between a and b in *either*
        direction, else None. Direction is reported as stored."""
        row = first_row(self.db.query(
            "SELECT outV().id AS src, inV().id AS dst, status, mode FROM BINDS "
            "WHERE (outV().id=:a AND inV().id=:b) OR (outV().id=:b AND inV().id=:a)",
            {"a": a, "b": b}))
        if not row.get("src"):
            return None
        return row["src"], row["dst"], row.get("status"), row.get("mode")

    def _between(self, a, b):
        """(out-id, status) of a BINDS edge in *either* direction, else (None, None)."""
        edge = self._edge(a, b)
        return (None, None) if edge is None else (edge[0], edge[2])

    def _check(self, a, b):
        validate_id(a)
        validate_id(b)
        if a == b:
            raise ValueError("a note cannot link to itself")
        for nid in (a, b):
            if not note_exists(self.db, nid):
                raise ValueError(f"note {nid} does not exist")


# ---- the ratification queue: keeping it human-sized (spec §7, §8.2) ----------
# The digest proposes faster than any human ratifies — one measured run left 90 suggested edges
# against 18 ratified. An unbounded queue quietly erodes "the human ratifies everything": what
# nobody can read, nobody decides. So suggestions are mortal. Ratified edges never are — those
# are corpus, and only a *proposal* may be deleted (§2.4).
#
# **Age was not enough, and the measurement says why** *(v0.8.5)*. Expiry alone bounds the queue at
# production rate × max age, which on a corpus staging 10 a night settles near 300 standing
# suggestions — and it takes a full max-age period to bind at all, so for the first month it does
# nothing whatsoever. Measured on this corpus: 5 nightly runs staged 50, the sweep expired 0, and
# the earliest date it *could* have expired anything was four weeks out. A ceiling binds from the
# first run instead, and binds against the thing that actually matters — how much is standing
# undecided now, which is a fact about the reader, not about the calendar.
SUGGESTION_MAX_AGE_DAYS = 30    # unratified for a month = implicitly declined
SUGGESTION_KEEP_MIN = 10        # never prune a queue this short — a to-do list is not a backlog
SUGGESTION_MAX_QUEUE = 50       # ...and never let it grow past this: the digest stops proposing
                                # until you decide some. KEEP_MIN < MAX_QUEUE is load-bearing —
                                # inverted, a queue could neither be added to nor pruned.
EXPIRE_SAMPLE = 25              # pairs recorded verbatim on the Op (the count is always exact)


def suggested_link_count(db):
    """How many suggestions are standing undecided — the queue's one number.

    Both ends of the queue read it: the digest to know whether it may propose more, the sweep to
    know whether the backlog is worth pruning. One definition, so a change of mind about what
    counts as queued cannot leave the producer and the consumer disagreeing."""
    return first_row(db.query(
        "SELECT count(*) AS n FROM BINDS WHERE status = 'suggested'")).get("n") or 0


def expire_suggested_links(db, max_age_days=SUGGESTION_MAX_AGE_DAYS,
                           keep_min=SUGGESTION_KEEP_MIN, dry_run=False):
    """EXPIRE_LINKS — delete `suggested` BINDS edges older than `max_age_days`, so the
    ratification queue cannot outgrow the human it exists to serve. Returns a summary dict;
    logs one Op for the sweep (never one per edge — the sweep is the rewrite, §11.2).

    Two guards stop it from eating a working queue:
      * while the total suggested count is <= `keep_min`, nothing is deleted at all — the whole
        point is to prune a backlog, and a short list isn't one;
      * an edge with no `created_at` is never swept, only counted. Undatable edges predate the
        property, and refusing to guess their age means the sweep can only ever delete something
        it knows is stale.

    **Ages the queue in Python, not in SQL.** A `WHERE created_at < date(:cutoff)` over an *edge*
    type is not trustworthy on this build: measured against a 213-edge corpus it returned 17 rows
    where 12 matched — two duplicated edges plus three that did not satisfy the predicate at all,
    unchanged by clause order or an added status filter. (The same range form over the vertex type
    Note is correct — see `lexical_search`. It is the edge index that misbehaves, which is why
    `ddl/schema.sql` deliberately does not index `BINDS.created_at`.) A null DATETIME also
    *matches* `< date(...)` here rather than dropping out as a SQL null should, so a SQL filter
    would delete the undated edges this promises to spare. Equality on an edge property is fine,
    so the scan filters by `status` in SQL and compares dates client-side via `_dt_digits`, then
    deletes by endpoint pair — the same verified idiom as `LinkManager.reject`."""
    total = suggested_link_count(db)
    result = {"total_suggested": total, "max_age_days": max_age_days, "cutoff": None,
              "expired": 0, "undated": 0, "pairs": [], "skipped_small_queue": False,
              "dry_run": dry_run, "op_id": None}
    if total <= keep_min:
        result["skipped_small_queue"] = True
        return result

    cutoff_day = (now_utc() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    result["cutoff"] = cutoff_day
    cutoff_key = _dt_digits(cutoff_day.replace("-", "") + "0" * 9)   # midnight of the cutoff day
    stale, undated = [], 0
    for r in rows(db.query("SELECT outV().id AS a, inV().id AS b, created_at FROM BINDS "
                           "WHERE status = 'suggested'")):
        a, b, born = r.get("a"), r.get("b"), r.get("created_at")
        if not (a and b):
            continue
        if not born:
            undated += 1                        # cannot prove it is stale -> leave it alone
        elif _dt_digits(born) < cutoff_key:
            stale.append((a, b))
    result["undated"] = undated
    result["pairs"], result["expired"] = stale, len(stale)
    if dry_run or not stale:
        return result

    op_id = unique_id(db, format_id(now_utc()), "Op")
    with db.transaction():
        for a, b in stale:
            db.command("DELETE FROM BINDS WHERE outV().id = :a AND inV().id = :b",
                       {"a": a, "b": b})
        insert_op(db, op_id, "EXPIRE_LINKS",
                  {"cutoff": cutoff_day, "max_age_days": max_age_days,
                   "expired": len(stale), "total_suggested": total,
                   "undated": undated, "pairs": stale[:EXPIRE_SAMPLE]})
    result["op_id"] = op_id
    return result


# ---- provocation moves (the difference-engine, spec §8.1) -------------------
# Move 1 is the flagship: "semantically near, graph-far". The others (bridges, implicit
# themes, contradictions, resurfacing) join here in the later build steps; they all read
# the four layers and stage SUGGEST_LINK candidates.
def graph_neighborhood(db, seed_id, depth=2):
    """Ids within `depth` hops of the seed over BINDS or BEGETS (either
    direction) — the 'graph-near' set move 1 excludes. Includes the seed itself.
    (TRAVERSE both(...) MAXDEPTH n, validated on 26.7.3; MAXDEPTH wants a literal.)"""
    d = max(1, int(depth))
    sql = ("SELECT id FROM (TRAVERSE both('BINDS'), both('BEGETS') "
           f"FROM (SELECT FROM Note WHERE id=:s) MAXDEPTH {d})")
    return {r.get("id") for r in rows(db.query(sql, {"s": seed_id})) if r.get("id")}


def move1_candidates(db, seed_id, k=5, ef=100, depth=2, use_cache=True):
    """Provocation move 1 (spec §8.1, §8.3): notes semantically near the seed but
    graph-far — high embedding similarity AND no ≤`depth`-hop BINDS/BEGETS path,
    so it never proposes a link where lineage or an existing link already connects.
    Returns up to `k` semantic hits (with score) minus the graph-near set. The seed
    must already be embedded (else ValueError).

    Reads the nightly k-NN cache by default (`neighbors_of`), which is what makes the
    five-move digest affordable; `use_cache=False` forces a live vector query."""
    near = graph_neighborhood(db, seed_id, depth)         # includes the seed itself
    return neighbors_of(db, seed_id, k=k, ef=ef, exclude_ids=near, use_cache=use_cache)


# ---- provocation moves 2–5 (the rest of the difference-engine, spec §8.1) ---
# Move 1 (above) is the flagship. Moves 2–5 mine the other layer-mismatches; each returns
# candidate rows the machine may propose but never author (§8.2). They power the provocation
# digest (scripts/provocation_digest.py) and the weekly resurfacing pass.
def _cosine(a, b):
    """Cosine similarity of two equal-length vectors; 0.0 if either is empty/degenerate."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def move2_candidates(db, k=5, min_size=None):
    """Move 2 (§8.1): bridge candidates — notes whose neighbours span >= 2 detected communities
    (structural holes are where original ideas live). Ranks boundary notes by the number of
    distinct communities they touch, then degree. Needs >= 2 communities, else []."""
    if min_size is None:
        min_size = COMMUNITY_MIN_SIZE                 # resolved at call time (defined below)
    adj = _undirected_adjacency(db)
    comms = detect_communities(db, min_size=min_size)
    if len(comms) < 2:
        return []
    comm_of = {nid: i for i, c in enumerate(comms) for nid in c}
    scored = []
    for nid, nbrs in adj.items():
        touched = {comm_of[m] for m in nbrs if m in comm_of}
        if nid in comm_of:
            touched.add(comm_of[nid])
        if len(touched) >= 2:
            scored.append((len(touched), len(nbrs), nid))
    scored.sort(reverse=True)
    out = []
    for spans, deg, nid in scored[:k]:
        meta = first_row(db.query("SELECT title, status, body FROM Note WHERE id = :id", {"id": nid}))
        out.append({"id": nid, "title": meta.get("title"), "status": meta.get("status"),
                    "snippet": snippet(meta.get("body")), "spans": spans, "degree": deg})
    return out


def move3_candidates(db, k=5, theme_score=0.55, min_theme=None, sample=50, use_cache=True):
    """Move 3 (§8.1): implicit themes — a tight semantic neighbourhood with no hub note holding
    it together: an unnamed theme running through the notes → a prompt to write one. The machine
    surfaces the theme; the human names it (never auto-authored).

    Before v0.8.0 this subtracted the members of ratified Clusters, on the theory that a named
    theme needs no naming. With Clusters gone there is nothing ratified to subtract, and the
    subtraction is not worth reconstructing: a detected community is a machine guess, and
    suppressing a prompt because the machine already grouped those notes would let the machine
    decide what the human never has to look at (§8.2).

    Asks for neighbours up to `sample` times, so it is the digest's heaviest vector consumer —
    it reads the k-NN cache by default (`use_cache=False` forces live queries)."""
    if min_theme is None:
        min_theme = COMMUNITY_MIN_SIZE                # resolved at call time (defined below)
    seeds = [r.get("id") for r in rows(db.query(
        f"SELECT id FROM Note WHERE embedding IS NOT NULL AND status = 'active' "
        f"ORDER BY id DESC LIMIT {int(sample)}")) if r.get("id")]
    themes, used = [], set()
    for sid in seeds:
        if sid in used:
            continue
        try:
            near = neighbors_of(db, sid, k=k + 1, exclude_ids={sid}, use_cache=use_cache)
        except ValueError:
            continue                          # seed lost its embedding mid-scan — skip it
        neigh = [c for c in near if c["score"] >= theme_score]
        member_ids = [sid] + [c["id"] for c in neigh]
        if len(member_ids) >= min_theme and not (set(member_ids) & used):
            sm = first_row(db.query("SELECT title, body FROM Note WHERE id = :id", {"id": sid}))
            members = [{"id": sid, "title": sm.get("title"), "snippet": snippet(sm.get("body"))}]
            members += [{"id": c["id"], "title": c.get("title"), "snippet": c.get("snippet")}
                        for c in neigh]
            themes.append({"seed": sid, "members": members, "size": len(member_ids)})
            used.update(member_ids)
        if len(themes) >= k:
            break
    return themes


def inhibited_note_ids(db):
    """Ids of notes a ratified BINDS{inhibits} points AT — the derived replacement for the
    `status='superseded'` flag dropped in v0.7.0 (§6). Being inhibited is now a fact about
    the graph, not a stamp on the note: withdraw the bind and the note is simply not
    inhibited any more, with no second write to keep in step."""
    return {r["id"] for r in rows(db.query(
        "SELECT inV().id AS id FROM BINDS WHERE mode = 'inhibits' AND status = 'ratified'"))
        if r.get("id")}


def move4_candidates(db, k=5):
    """Move 4 (§8.1): contradiction / tension pairs. The append-only corpus keeps opposing
    claims side by side — most sharply across a ratified BINDS{inhibits} (what you used to
    think vs the correction). Surface these to reconcile by hand; the machine never
    auto-resolves (§8.2)."""
    pairs = []
    for r in rows(db.query(
            "SELECT outV().id AS new_id, inV().id AS old_id, rationale FROM BINDS "
            "WHERE mode = 'inhibits' AND status = 'ratified'")):
        new_id, old_id = r.get("new_id"), r.get("old_id")
        if not (new_id and old_id):
            continue
        nt = first_row(db.query("SELECT title, body FROM Note WHERE id = :id", {"id": new_id}))
        ot = first_row(db.query("SELECT title, body FROM Note WHERE id = :id", {"id": old_id}))
        pairs.append({"new": new_id, "new_title": nt.get("title"),
                      "new_snippet": snippet(nt.get("body")), "old": old_id,
                      "old_title": ot.get("title"), "old_snippet": snippet(ot.get("body")),
                      "reason": r.get("rationale")})
    return pairs[:k]


def move5_candidates(db, k=8):
    """Move 5 (§8.1): serendipitous re-encounter — resurface orphan (no ratified BINDS) and
    inhibited notes plus an 'on this day' anniversary, so nothing is forgotten (nothing truly
    dies, §6). Returns buckets {orphans, inhibited, on_this_day}."""
    ratified_linked = set()
    for col in ("outV().id", "inV().id"):
        ratified_linked |= {r.get("id") for r in rows(db.query(
            f"SELECT {col} AS id FROM BINDS WHERE status = 'ratified'")) if r.get("id")}
    inhibited_ids = inhibited_note_ids(db)
    alln = rows(db.query("SELECT id, title, body, status FROM Note ORDER BY id"))

    def card(r):
        return {"id": r.get("id"), "title": r.get("title"), "snippet": snippet(r.get("body"))}

    orphans = [card(r) for r in alln
               if r.get("id") not in inhibited_ids and r.get("id") not in ratified_linked][:k]
    inhibited = [card(r) for r in alln if r.get("id") in inhibited_ids][:k]
    today = now_utc()
    md, ymd = f"{today.month:02d}{today.day:02d}", today.strftime("%Y%m%d")
    on_this_day = [card(r) for r in alln
                   if (r.get("id") or "")[4:8] == md and (r.get("id") or "")[:8] != ymd][:k]
    return {"orphans": orphans, "inhibited": inhibited, "on_this_day": on_this_day}


# ---- Walks: the read/think session as a recorded run (spec §11.1, §11.6, §13) ----
# A walk is an execution run into the adjacent possible: a human-directed traversal, replayable
# against a corpus that has since changed — the stored-program loop (§11.1). The stored graph is
# first entelechy (dormant knowledge); a replay is second entelechy (the being-at-work that
# actualizes it, §11.6).
#
# Recording a walk is an OPERATION and it writes exactly two things (§13):
#
#   1. an Op per event — the walk IS its Op sequence, and needs no vertex to exist. The walk's
#      id is the id of its own START_WALK Op, so it is unique by construction.
#   2. +1 to Note.visited, once per walk per note. Re-reading a note inside one sitting is one
#      encounter, so a repeat VISIT logs its Op but does not count again.
#
# Everything else a Trace vertex used to hold — the ordered trail, what the run produced, whether
# it was saved — is reconstructed from those Ops by read_walk()/list_walks() below, and replayed
# by scripts/analytics/walks.py. Mirrors LinkManager: every mutation is one transaction that also
# appends its Op (§12.3).
#
# **The working set is gone as of v0.8.3, and `SET_WORKING` is in this list for one reason: the
# log is append-only.** A walk recorded before then has part of its trail spread across
# SET_WORKING Ops, and dropping the rule from what we READ would silently shorten that walk's
# history — the same reasoning that makes DELETE_WALK a tombstone rather than a delete. Nothing
# writes it any more (there is no set_working method, and walk.sh has no --working), and
# _fold_walk treats an old one as the plain visit it always also was.
#
# What it was: a second role on a trail entry, nominated by hand, that `replay` re-seeded move 1
# from. It was declared rather than derived, which put it on the wrong side of v0.8.0's own line
# (nothing else in the graph is a stored human judgement about a measurement), it had no verb to
# undo it, and across the whole history of this corpus it was never once used outside the tests.
# Replay now re-seeds from the trail: the notes a run touched ARE the notes it held.
WALK_RULES = ("START_WALK", "VISIT", "SET_WORKING", "PRODUCE", "SAVE_WALK",
              "FORK_WALK", "DELETE_WALK")
WalkResult = namedtuple("WalkResult", "rule walk_id note_id op_id")


class WalkManager:
    """Start, record, save, fork and discard human-directed walks (spec §11.1, §13).

    The one writer of Note.visited. Nothing else in the system — not the digest, not the
    resurfacing pass, not the k-NN rebuild — ever touches it, which is what makes it a measure
    of human attention rather than of machine activity."""

    def __init__(self, db=None):
        self.db = db or Arcade()

    def start(self, intent, seed_id):
        """START_WALK — open a walk with an intent + seed and visit the seed at seq 0. The
        walk's id is its own Op's id. The seed must be an existing note. Returns WalkResult.

        **Every walk gets an intent** (v0.8.3). Given none, one is derived from the seed, because
        the seed already says what the run is about — you began there for a reason. Before this,
        a walk started without `--intent` was anonymous forever: there is no rule in the grammar
        that amends a START_WALK payload, so `analytics.sh walks` listed it as `(no intent)` and
        nothing could ever fix that. A derived default is not the stated goal §11.3 means by
        top-down control, and it does not pretend to be — it is a name, so the run can be found
        again. `walk.sh start --intent "…"` still states the real thing.
        """
        validate_id(seed_id)
        if not note_exists(self.db, seed_id):
            raise ValueError(f"seed note {seed_id} does not exist")
        intent = (intent or "").strip() or self._intent_from(seed_id)
        started = format_created_at(now_utc())
        walk_id = unique_id(self.db, format_id(now_utc()), "Op")
        with self.db.transaction():
            insert_op(self.db, walk_id, "START_WALK",
                      {"walk_id": walk_id, "seed": seed_id, "intent": intent,
                       "started_at": started, "note_id": seed_id, "seq": 0,
                       "role": "visited"})
            self._count_visit(seed_id)
        return WalkResult("START_WALK", walk_id, seed_id, walk_id)

    def visit(self, walk_id, note_id):
        """VISIT — append note_id to the trail at the next seq. A re-visit is logged but keeps
        its original seq and does not count towards Note.visited again.

        The only way to put a note on a trail since v0.8.3; `set_working` is gone (see WALK_RULES
        above for what it was and why it went)."""
        info = self._require_walk(walk_id)
        validate_id(note_id)
        if not note_exists(self.db, note_id):
            raise ValueError(f"note {note_id} does not exist")
        # The dedup read happens before the transaction opens: it is a question about Ops already
        # committed, and a walk is one person reading.
        seen = {v["note_id"]: v["seq"] for v in info.get("visited", [])}
        first_time = note_id not in seen
        seq = len(seen) if first_time else seen[note_id]
        op_id = unique_id(self.db, format_id(now_utc()), "Op")
        with self.db.transaction():
            insert_op(self.db, op_id, "VISIT",
                      {"walk_id": walk_id, "note_id": note_id, "seq": seq})
            if first_time:
                self._count_visit(note_id)
        return WalkResult("VISIT", walk_id, note_id, op_id)

    def produce(self, walk_id, note_id):
        """PRODUCE — record that the walk authored note_id. Does not count as a visit: writing
        a note is not reading one."""
        self._require_walk(walk_id)
        validate_id(note_id)
        if not note_exists(self.db, note_id):
            raise ValueError(f"note {note_id} does not exist")
        op_id = unique_id(self.db, format_id(now_utc()), "Op")
        with self.db.transaction():
            insert_op(self.db, op_id, "PRODUCE", {"walk_id": walk_id, "note_id": note_id})
        return WalkResult("PRODUCE", walk_id, note_id, op_id)

    def save(self, walk_id):
        """SAVE_WALK — close the run; saved walks replay and fork."""
        self._require_walk(walk_id)
        ended = format_created_at(now_utc())
        op_id = unique_id(self.db, format_id(now_utc()), "Op")
        with self.db.transaction():
            insert_op(self.db, op_id, "SAVE_WALK", {"walk_id": walk_id, "ended_at": ended})
        return WalkResult("SAVE_WALK", walk_id, None, op_id)

    def fork(self, walk_id):
        """FORK_WALK — open a new walk copying intent/seed, recording its parent. One Op, which
        opens the new walk exactly as START_WALK does and additionally names what it came from;
        the seed is visited by the new walk, so it counts once more — a fork is a fresh sitting."""
        src = read_walk(self.db, walk_id)
        if not src:
            raise ValueError(f"no walk {walk_id} to fork")
        seed_id = src.get("seed")
        started = format_created_at(now_utc())
        new_id = unique_id(self.db, format_id(now_utc()), "Op")
        with self.db.transaction():
            insert_op(self.db, new_id, "FORK_WALK",
                      {"walk_id": new_id, "seed": seed_id, "intent": src.get("intent"),
                       "started_at": started, "note_id": seed_id, "seq": 0,
                       "role": "visited", "forked_from": walk_id})
            if seed_id:
                self._count_visit(seed_id)
        return WalkResult("FORK_WALK", new_id, seed_id, new_id)

    def delete(self, walk_id):
        """DELETE_WALK — retire a walk. A walk is a record of the corpus, not corpus itself
        (§2.4), so it may be discarded; but the Op log is append-only, so this writes a
        TOMBSTONE rather than erasing history. read_walk/list_walks honour it and skip the
        walk thereafter, and the attention it contributed is given back: every note it visited
        is decremented by one (floored at 0 — a note is never made to owe visits).

        Its forks survive; only the parent link goes stale. Raises if the walk is unknown."""
        info = self._require_walk(walk_id)
        visited_ids = [v["note_id"] for v in info.get("visited", []) if v.get("note_id")]
        op_id = unique_id(self.db, format_id(now_utc()), "Op")
        with self.db.transaction():
            if visited_ids:
                self.db.command("UPDATE Note SET visited = visited - 1 "
                                "WHERE id IN :ids AND visited > 0", {"ids": visited_ids})
            insert_op(self.db, op_id, "DELETE_WALK",
                      {"walk_id": walk_id, "status": info.get("status"),
                       "visited": len(visited_ids),
                       "produced": len(info.get("produced", []))})
        return WalkResult("DELETE_WALK", walk_id, None, op_id)

    # -- internals --
    def _intent_from(self, seed_id):
        """The default intent for a walk started without one: the seed, named.

        The same short label every report uses — a title, else a clipped snippet — so the walk
        listing reads as prose rather than as a column of ids. start() has already established
        the note exists; the bare id is the last resort for one with neither field.
        """
        row = first_row(self.db.query("SELECT title, body FROM Note WHERE id = :n",
                                      {"n": seed_id}))
        label = (row.get("title") or "").strip() or snippet(row.get("body"), 60)
        return f"reading out from {label or seed_id}"

    def _count_visit(self, note_id):
        """+1 to Note.visited. Run inside a transaction. ifnull() covers notes that predate
        the property (the migration zeroes them, but a restore might not have run it yet)."""
        self.db.command("UPDATE Note SET visited = ifnull(visited, 0) + 1 WHERE id = :n",
                        {"n": note_id})

    def _require_walk(self, walk_id):
        """Reconstruct the walk, raising if it is unknown or tombstoned. Returns it, since
        every caller needs the trail anyway."""
        validate_id(walk_id)
        info = read_walk(self.db, walk_id)
        if not info:
            raise ValueError(f"no walk with id {walk_id}")
        return info


# ---- reading walks back out of the Op log (spec §11.2, §13) -----------------
# The reconstruction the WalkManager writes against, and the one analytics replays. Pure reads.
def _walk_ops(db, walk_id=None):
    """Walk Ops oldest-first as (op_id, rule, payload dict). Ops with unparseable payloads are
    skipped rather than raising — the log is an audit trail, and one bad row must not make
    every walk unreadable."""
    rule_list = ", ".join(f"'{r}'" for r in WALK_RULES)
    out = []
    for r in rows(db.query(f"SELECT id, rule, payload FROM Op "
                           f"WHERE rule IN [{rule_list}] ORDER BY id")):
        try:
            payload = json.loads(r.get("payload") or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        if walk_id is None or payload.get("walk_id") == walk_id:
            out.append((r.get("id"), r.get("rule"), payload))
    return out


def _fold_walk(events):
    """Fold one walk's Ops into its state, or {} if it never started or was tombstoned.

    The trail keeps each note once, at the seq of its FIRST appearance; a re-visit neither
    duplicates nor reorders it.

    SET_WORKING is folded as a plain visit. Nothing writes one since v0.8.3 (WALK_RULES says what
    it was), but a walk recorded before then has trail entries that arrived only as SET_WORKING,
    and an append-only log means reading them is not optional — ignoring the rule here would not
    remove a feature from history, it would shorten the walks that used it. The `role` those Ops
    carry is simply not read: there is one role now, so the trail no longer states it."""
    head, trail, produced, deleted = {}, {}, [], False
    for op_id, rule, payload in events:
        if rule == "DELETE_WALK":
            deleted = True
        elif rule in ("START_WALK", "FORK_WALK"):
            # FORK_WALK opens a walk exactly as START_WALK does; it only adds forked_from.
            head = {"id": payload.get("walk_id") or op_id,
                    "intent": payload.get("intent"),
                    "seed": payload.get("seed"),
                    "started_at": payload.get("started_at"),
                    "ended_at": None,
                    "status": "active",
                    "forked_from": payload.get("forked_from")}
        elif rule == "SAVE_WALK":
            if not head:
                continue                # a close with no open — ignore rather than invent a walk
            head["ended_at"] = payload.get("ended_at")
            head["status"] = "saved"
        elif rule == "PRODUCE":
            if payload.get("note_id"):
                produced.append(payload["note_id"])
        if rule in ("START_WALK", "FORK_WALK", "VISIT", "SET_WORKING"):
            note_id = payload.get("note_id")
            if note_id and note_id not in trail:
                trail[note_id] = {"note_id": note_id, "seq": payload.get("seq", len(trail))}
    if deleted or not head:
        return {}
    head["visited"] = sorted(trail.values(), key=lambda v: v["seq"])
    head["produced"] = produced
    return head


def read_walk(db, walk_id):
    """One walk reconstructed from the Op log: {id, intent, seed, started_at, ended_at, status,
    forked_from, visited: [{note_id, seq}], produced: [id]}. {} if unknown or deleted."""
    return _fold_walk(_walk_ops(db, walk_id))


def list_walks(db, status=None, limit=50):
    """Walks newest-first, optionally filtered by status ('active'|'saved'). One pass over the
    walk Ops, grouped by walk_id — cheaper than a query per walk, and the log is small."""
    grouped = {}
    for event in _walk_ops(db):
        grouped.setdefault(event[2].get("walk_id"), []).append(event)
    walks = [w for w in (_fold_walk(events) for events in grouped.values()) if w]
    if status:
        walks = [w for w in walks if w.get("status") == status]
    walks.sort(key=lambda w: w.get("id") or "", reverse=True)
    return walks[:int(limit)]


# ---- community detection: a cluster is a query, not an object (spec §3.3, §13) ----
# Communities are DETECTED on demand over the ratified BINDS + BEGETS graph, by stdlib-only
# label propagation (no networkx — the repo has zero third-party deps). Before v0.8.0 the
# detector crystallized its findings into Cluster vertices for a human to ratify; now nothing
# is written. A cluster is what the graph currently looks like, so asking twice on a graph that
# moved is *supposed* to give two answers, and there is no stored copy to fall out of date.
#
# This lives in notelib rather than in analytics because it is on the OPERATIONAL path: the
# difference-engine's move 3 mines the boundaries between communities, and the provocation
# digest seeds itself from their hubs (§8.1). It writes nothing either way.
COMMUNITY_MIN_SIZE = 3           # smaller groupings are just links, not communities
LABELPROP_MAX_ITERS = 20         # label-propagation iteration cap (guarantees termination)


def _undirected_adjacency(db):
    """Undirected adjacency {note_id: set(neighbours)} over the associative (ratified
    BINDS) + structural (BEGETS) graph — the two graphs community detection runs on
    (§12.6). Endpoints via outV()/inV() (out.id projects null on this build)."""
    adj = {}

    def join(a, b):
        if a and b and a != b:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)

    for r in rows(db.query("SELECT outV().id AS a, inV().id AS b FROM BINDS "
                           "WHERE status='ratified'")):
        join(r.get("a"), r.get("b"))
    for r in rows(db.query("SELECT outV().id AS a, inV().id AS b FROM BEGETS")):
        join(r.get("a"), r.get("b"))
    return adj


def detect_communities(db, min_size=COMMUNITY_MIN_SIZE, max_iters=LABELPROP_MAX_ITERS):
    """Detect communities via synchronous label propagation (Raghavan et al.) over the
    ratified-BINDS + BEGETS graph — O(edges)/iteration, stdlib only. Deterministic:
    nodes are processed in id order and label ties are broken by lowest label id. Returns a
    sorted list of id-sorted communities with >= min_size members (a 2-node group is just a
    link). Empty graph -> []."""
    adj = _undirected_adjacency(db)
    if not adj:
        return []
    label = {nid: nid for nid in adj}
    order = sorted(adj)
    for _ in range(max_iters):
        changed = False
        for nid in order:
            if not adj[nid]:
                continue
            counts = {}
            for m in adj[nid]:
                counts[label[m]] = counts.get(label[m], 0) + 1
            top = max(counts.values())
            winner = min(lab for lab, c in counts.items() if c == top)   # tie-break: lowest id
            if winner != label[nid]:
                label[nid] = winner
                changed = True
        if not changed:
            break
    groups = {}
    for nid, lab in label.items():
        groups.setdefault(lab, []).append(nid)
    return sorted((sorted(g) for g in groups.values() if len(g) >= min_size))


# ---- NO stored analytics since v0.8.0 (spec §13) ----------------------------
# What used to live here — the catalysis relation, autocatalysis (iterative Tarjan), per-cluster
# reproduction rate and criticality, ClusterManager, the activation-decay tick, the fitness
# recompute, and the corpus criticality report — has moved to scripts/analytics/, which reads
# the graph and writes nothing to it.
#
# The move is not just tidying. Every one of those was derivable from BEGETS + BINDS + the Op
# log at the moment it was asked, and storing the answer meant maintaining it: a nightly job
# rewrote fitness for every note and the diagnostics for every cluster, because ratifying one
# bind could change any of them. Computing them on demand costs a graph scan at ~100 notes and
# removes an entire class of staleness. It also makes the numbers cheap to change — a fitness
# weight is now a constant in a report, not a migration.
#
# notelib keeps only what the OPERATIONAL path needs: community detection (above, for move 3
# and the digest's hub seeds) and the vocabulary the graph is written in (bind_sign, MODES).


# ---- schema growth: PROMOTE_TYPE (spec §3.3, §12.3) -------------------------
# Kauffman's unprestatability made operational: promoting a recurring pattern into a new
# first-class vertex/edge type is itself a legitimate (meta) rewrite rule, so the type system
# stays extensible (§3.3). Identifiers can't be bound params, so they are validated against a
# strict pattern (and types against a whitelist) before interpolation — no injection surface.
_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
ARCADE_TYPES = {"STRING", "INTEGER", "LONG", "SHORT", "BYTE", "FLOAT", "DOUBLE", "DECIMAL",
                "BOOLEAN", "DATE", "DATETIME", "BINARY", "LIST", "MAP", "EMBEDDED", "LINK",
                "ARRAY_OF_FLOATS"}


def promote_type(db, kind, name, properties=None):
    """PROMOTE_TYPE ○ (spec §3.3, §12.3) — register a new vertex/edge type (schema growth),
    optionally with properties. Idempotent: `CREATE … TYPE … IF NOT EXISTS` for the type;
    per-property creation tolerates 'already exists' (this build rejects IF NOT EXISTS on
    CREATE PROPERTY, as apply-ddl.sh handles). `properties` is an iterable of (name, arcade_type).
    Logs Op(PROMOTE_TYPE). Returns a summary dict."""
    kind = (kind or "").strip().lower()
    if kind not in ("vertex", "edge"):
        raise ValueError(f"kind must be 'vertex' or 'edge', got {kind!r}")
    if not _IDENT_RE.match(name or ""):
        raise ValueError(f"invalid type name {name!r} (letters/digits/underscore, letter first)")
    props = []
    for pname, ptype in (properties or []):
        if not _IDENT_RE.match(pname or ""):
            raise ValueError(f"invalid property name {pname!r}")
        ptype = (ptype or "STRING").strip().upper()
        if ptype not in ARCADE_TYPES:
            raise ValueError(f"unknown property type {ptype!r} (one of: {', '.join(sorted(ARCADE_TYPES))})")
        props.append((pname, ptype))
    db.command(f"CREATE {kind.upper()} TYPE {name} IF NOT EXISTS")
    created = []
    for pname, ptype in props:
        try:
            db.command(f"CREATE PROPERTY {name}.{pname} {ptype}")
            created.append({"name": pname, "type": ptype})
        except ArcadeError as e:
            if "already exist" not in str(e.detail).lower():
                raise
    op_id = unique_id(db, format_id(now_utc()), "Op")
    insert_op(db, op_id, "PROMOTE_TYPE", {"kind": kind, "name": name, "properties": created})
    return {"kind": kind, "name": name, "properties": created, "op_id": op_id}
