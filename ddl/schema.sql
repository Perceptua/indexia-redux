-- =============================================================================
-- Indexia — ArcadeDB schema (DDL)
-- Target: ArcadeDB SQL. Applied statement-by-statement by scripts/apply-ddl.sh, which tolerates
-- "already exists" errors so re-running is idempotent (this build accepts IF NOT EXISTS on
-- TYPE/INDEX but NOT on CREATE PROPERTY, so idempotency is handled by the applier, not by
-- per-statement guards). Comments are stripped before send.
--
-- Source of truth: docs/spec.md §12.1.
--
-- ---- THE SHAPE, AND WHY IT IS THIS SMALL (spec §13) -------------------------
-- Five types, and that is the whole graph:
--
--     Note  --BEGETS-->  Note      lineage: where a note came from
--     Note  --BINDS--->  Note      association: what it relates to, and how
--     Op                           the append-only rewrite log (infrastructure, not corpus)
--     KnnCache                     a rebuildable index over the corpus (document, not a vertex)
--
-- The graph is **purely relational**, and a Note is **value-neutral**: it carries identity,
-- content, provenance, an embedding, and one record of human attention — but it does not know
-- its own fitness, which community it belongs to, or whether it is critical. Those are
-- observations *of* the graph, computed on demand by scripts/analytics/ and stored nowhere.
--
-- This is a rule, not an accident, and it has teeth:
--
--   **Before adding a type or a property, ask whether the thing can be COMPUTED from Note +
--   BEGETS + BINDS + the Op log. If it can, it belongs in a report, not in the schema.**
--
-- Earlier versions did not follow it. Note.fitness and Note.activation were stored numbers
-- derived entirely from the edges; Trace and Cluster were vertices summarizing runs and
-- groupings; six further edge types existed only to hang those two off the corpus. All of it
-- had to be MAINTAINED — ratifying one bind could invalidate any of it, so a nightly job
-- rewrote the lot, and between runs it was wrong. Note.fitness was never even read.
--
-- What makes the rule affordable is that everything here is DATED: Note.created_at,
-- BEGETS.created_at and BINDS.created_at together let any report be recomputed *as of* a past
-- instant. A cluster from last month is a query, not a record somebody had to keep.
--
-- When a report gets too slow to recompute (not at ~100 notes; eventually), the answer is a
-- rebuildable cache in the shape of KnnCache below — a DOCUMENT type nothing traverses into —
-- and NOT a property back on Note. Computed-vs-stored is not the distinction that matters;
-- whether the corpus *claims* the value as part of itself is.
--
-- ---- THE ONE TUNING KNOB ----------------------------------------------------
-- VECTOR DIMENSION must match the embedding model (spec §10 leaves the model open; default is a
-- ~1024-dim local model). Changing it later means DROP + recreate the LSM_VECTOR index and
-- re-embed every note.
-- =============================================================================


-- ---- Vertex: Note (the atomic note, §3.1) -----------------------------------
CREATE VERTEX TYPE Note IF NOT EXISTS;
CREATE PROPERTY Note.id STRING;               -- identity, immutable: 20260715T234214123Z (§4)
CREATE PROPERTY Note.title STRING;            -- optional human label
CREATE PROPERTY Note.body STRING;             -- the atomic note (full sentences, one idea)
CREATE PROPERTY Note.created_at DATETIME;     -- same instant as id, for range queries
CREATE PROPERTY Note.author STRING;           -- human | llm_assisted (§5)
CREATE PROPERTY Note.source_ref STRING;       -- optional provenance pointer
CREATE PROPERTY Note.embedding ARRAY_OF_FLOATS; -- computed at creation (§7); binds cleanly from JSON-array params and is compact for large vectors (spec §12.1 said LIST OF FLOAT; ARRAY_OF_FLOATS validated as the API-friendly choice)
CREATE PROPERTY Note.embedding_model STRING;  -- which model produced `embedding` (swappable, §10)
CREATE PROPERTY Note.embedding_dim INTEGER;   -- dimension of `embedding` (must match the index)
CREATE PROPERTY Note.status STRING;           -- proposed | active (§3.1). NOT 'superseded':
                                              -- inhibition is derived from inbound ratified
                                              -- BINDS{inhibits}, never stamped on the note (§6).
-- The one record of human attention a note carries: how many human-directed walks went THROUGH
-- it (§13). Counted once per walk, not once per visit — re-reading a note inside a single
-- sitting is one encounter. Written only by walk recording; no machine job may touch it, or it
-- would measure the daemon rather than the reader. It is stored precisely BECAUSE it is the one
-- thing no amount of graph reading recovers: nothing else records that a person read something.
CREATE PROPERTY Note.visited INTEGER;

CREATE INDEX IF NOT EXISTS ON Note (id) UNIQUE;
CREATE INDEX IF NOT EXISTS ON Note (created_at) NOTUNIQUE;   -- temporal layer (§8, §12.2)
CREATE INDEX IF NOT EXISTS ON Note (status) NOTUNIQUE;
-- Semantic layer: HNSW/Vamana ANN over embeddings (JVector-backed). `dimensions` must equal the
-- Note.embedding_dim written on each note.
CREATE INDEX IF NOT EXISTS ON Note (embedding) LSM_VECTOR
  METADATA { dimensions: 1024, similarity: 'COSINE', quantization: 'INT8', maxConnections: 16 };


-- ---- Vertex: Op (append-only rewrite log, §11.2, §12.3) ---------------------
-- Every state change appends one. The converse is the useful half: anything that appends no Op
-- changed no state — which is how the analytics layer's "reads only" guarantee is checked rather
-- than asserted (tests/test_analytics_readonly.py).
--
-- It is also the ONLY record of a walk. A walk has no vertex: START_WALK / VISIT / SET_WORKING /
-- PRODUCE / SAVE_WALK / FORK_WALK *are* the walk, folded back on read (notelib.read_walk), and
-- the walk's id is its own opening Op's id. DELETE_WALK is therefore a tombstone, not an
-- erasure — the log does not rewrite.
CREATE VERTEX TYPE Op IF NOT EXISTS;
CREATE PROPERTY Op.id STRING;                 -- timestamp of the rule application
CREATE PROPERTY Op.rule STRING;               -- ADD_NOTE | SUGGEST_LINK | VISIT | ... (§12.3)
CREATE PROPERTY Op.payload STRING;            -- JSON args of the rule
CREATE INDEX IF NOT EXISTS ON Op (id) UNIQUE;


-- ---- Document: KnnCache (derived k-NN adjacency cache — NOT corpus) ---------
-- Adding a vector invalidates the whole LSM_VECTOR graph, so the next vector.neighbors call
-- rebuilds all N vectors (~11 min at N=101). The provocation moves would each pay that, so the
-- nightly knn-cache job materializes every embedded note's top-k neighbours once and the moves
-- read this instead (scripts/knn_cache.py, notelib.neighbors_of).
-- Deliberately a DOCUMENT type, not a vertex: it is a rebuildable index over the corpus, never
-- part of the graph — nothing traverses into it, and dropping it loses nothing.
CREATE DOCUMENT TYPE KnnCache IF NOT EXISTS;
CREATE PROPERTY KnnCache.note_id STRING;      -- the note whose neighbours these are
CREATE PROPERTY KnnCache.neighbors STRING;    -- JSON [[neighbour_id, score], …], nearest first
CREATE PROPERTY KnnCache.built_at DATETIME;   -- when this row was materialized (staleness check)
CREATE INDEX IF NOT EXISTS ON KnnCache (note_id) UNIQUE;


-- ---- Edge types (§3.2) ------------------------------------------------------
-- Two note->note edges, drawn along the one boundary the system pivots on: generation vs
-- recognition. Ingestion may create BEGETS and nothing else; every judgement the corpus makes
-- about itself is a BINDS, and is human-ratified.

-- Structural/generative graph. mode in {continues, branches}; drives the Folgezettel (§4).
-- Semantically neutral: BEGETS says only "this note came from that one" (§3.2).
CREATE EDGE TYPE BEGETS IF NOT EXISTS;
CREATE PROPERTY BEGETS.mode STRING;           -- continues | branches
-- The child's instant, derived from its id (§4) rather than read off the clock. For a live
-- commit the two coincide — the edge is written inside the child's transaction — but they
-- diverge when a note is ingested with a back-dated id, which is what ingest-staging does when
-- rebuilding a corpus from staging/. Reading the clock there would date every lineage edge to
-- the rebuild and silently break --as-of. Not READONLY: the dating must stay repairable.
CREATE PROPERTY BEGETS.created_at DATETIME;

-- Emergent associative link; human-ratified, may be machine-proposed. Carries the corpus's
-- ONLY sign: catalyzes is excitatory, inhibits is the correction/inhibitory signal (§6, §7).
-- The sign lives here and nowhere else — it is READ where a question is asked, never accumulated
-- onto the notes, which is why ratify/retype/reject each write one edge and stop.
CREATE EDGE TYPE BINDS IF NOT EXISTS;
CREATE PROPERTY BINDS.mode STRING;            -- catalyzes | inhibits | null = untyped, neutral
CREATE PROPERTY BINDS.status STRING;          -- suggested | ratified (§7)
-- When the edge was staged; ages the suggestion queue for the expiry sweep (§7). Written once at
-- CREATE by LinkManager.suggest and never updated — but deliberately NOT hardened READONLY like
-- Note.created_at, because scripts/backfill_link_dates.py must be able to date edges that predate
-- this property (and re-date them after restoring an older backup). Undated edges are never
-- swept, so leaving that repair path open is what keeps them mortal.
CREATE PROPERTY BINDS.created_at DATETIME;
-- Free text: why these two relate, or why this correction. Move 5 reads "what did I used to
-- think, and why did I change it?" off the edge (§6, §8.1).
CREATE PROPERTY BINDS.rationale STRING;


-- ---- Secondary indexes for the difference-engine (§8) -----------------------
-- Most associative-layer queries filter BINDS by status='ratified'. Equality on an edge property
-- indexes correctly (verified: 195 rows, no duplicates).
CREATE INDEX IF NOT EXISTS ON BINDS (status) NOTUNIQUE;
--
-- DELIBERATELY NO INDEX ON BINDS (created_at) — NOR ON BINDS (mode), which is nullable for the
-- same reason and is nulled by `link.sh retype ... --mode none`, NOR ON BEGETS (created_at).
-- All three are nullable, and that is the exact shape of the fault below. Root-caused 2026-07-25
-- by replaying the original sequence against a restore of the pre-backfill backup:
--
--   CONFIRMED, deterministic, reproducible on demand: with this index present, an UPDATE that
--   sets created_at to NULL does NOT remove the row's existing index entry. A range predicate
--   (`created_at < date(...)`) then still matches that row even though its stored value is null.
--   Drop the index and nulls are correctly excluded. Value-to-value updates are maintained fine
--   (aged->older, aged->fresh, fresh->aged all exact), so the fault is specific to nulling.
--   This is why link_expiry's `IS NOT NULL` guard is load-bearing, not decoration — the first
--   version of that sweep lacked it and deleted the undated edges it promised to spare.
--
--   NOT reproduced: the other symptom (17 rows returned where 12 matched — 2 duplicated edges, 3
--   failing the predicate). A faithful replay of the whole original procedure on the original
--   data with this index present was exact at every step, as were targeted tests of
--   delete-then-recreate with RID reuse (15/15), 50-edge single transactions, and a full-scale
--   196-edge queue. Best remaining hypothesis is the same class of fault — a stale index entry
--   surviving a deletion and attaching to a reused RID — but it could not be made to fire.
--
-- Both symptoms needed the index; without it everything measured exact. So dropping it addresses
-- the actual cause rather than the symptom, and scripts/link_expiry.py ages the queue in Python
-- as defence in depth. tests/test_db_invariants.py demonstrates the confirmed fault live (it
-- creates and drops the index itself) — if that test starts failing, ArcadeDB has fixed the bug
-- and all three indexes could come back.
--
-- Note.visited is likewise unindexed, but for the ordinary reason: nothing filters or ranges on
-- it in the operational path, and the analytics that read it scan the whole corpus anyway.


-- =============================================================================
-- HARDENING (append-only / immutability guarantees, §4 & §6) — validated on 26.7.3.
-- ALTER PROPERTY sets constraints after the property exists. READONLY allows a value to be set
-- once at INSERT but never updated afterward.
--   id / created_at READONLY  -> enforce §4 immutability (set once at insert).
--   body stays writable        -> allows CORRECT_COSMETIC in-place fixes (§6).
--   status stays writable      -> allows the proposed -> active transition (§12.7).
--   visited stays writable     -> incremented by walk recording (§13); never by a machine job.
-- =============================================================================
ALTER PROPERTY Note.id MANDATORY true;
ALTER PROPERTY Note.id NOTNULL true;
ALTER PROPERTY Note.id READONLY true;
ALTER PROPERTY Note.body MANDATORY true;
ALTER PROPERTY Note.body NOTNULL true;
ALTER PROPERTY Note.created_at READONLY true;
