# Indexia — shortcuts for daily operations.
#
# Thin wrappers over scripts/*.sh (which do the real work via scripts/lib.sh); see
# README.md#scripts for the full reference. Docker + python3 live in WSL, so run this
# from WSL — e.g. `wsl -d ubuntu -- bash -lc 'cd ~/indexia && make up'`.
#
#   make              show this help
#   make up ui-up     bring the DB and the graph UI up
#   make reports      run the core read-only report set
#   make jobs         run the nightly/weekly maintenance chain by hand
#
# Targets that take arguments read them from ARGS (and a couple of named vars) rather than
# positional make args, e.g.:
#   make search ARGS="-q 'some phrase'"
#   make report R=autocatalysis ARGS="--members --json"
#   make restore ZIP=indexia-backup-123.zip TARGET=indexia_restore

SHELL := /bin/bash
.DEFAULT_GOAL := help

ARGS ?=
N ?=
R ?=
ZIP ?=
TARGET ?=

.PHONY: help \
	up down restart down-backup reset status logs \
	ui-up ui-down ui-restart ui-status ui-run \
	scheduler-up scheduler-down scheduler-status scheduler-run \
	embed-up embed-down embed-status embed-warm \
	worker-up worker-down worker-status worker-run \
	jobs knn-cache provocation-digest resurface link-expiry recent-notes embed-backfill \
	reports report fitness debt criticality communities autocatalysis visited walks \
	add-note ingest-staging search walk link provoke \
	backup restore drop-db console apply-ddl smoke-test \
	setup-ollama gen-env gen-cert new-id promote-type seed-binds \
	backfill-link-dates migrate-v0-8-0 transcribe-scans \
	test test-fast test-unit

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nIndexia — make targets\n"} \
		/^[a-zA-Z0-9_-]+:.*##/ { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 } \
		/^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)
	@echo ""

##@ Lifecycle (DB + daemons)

up: ## Start ArcadeDB, apply schema, start embedder/worker/scheduler (ARGS=--tailscale for Studio/REST over the tailnet)
	bash scripts/up.sh $(ARGS)

down: ## Stop ArcadeDB (./data persists)
	bash scripts/down.sh

down-backup: ## Hot backup, then stop
	bash scripts/down.sh --backup

restart: down up ## down then up

reset: ## DESTROYS ./data + ./backups — stops and wipes volumes (asks for confirmation)
	@read -p "This DESTROYS ./data and ./backups. Type 'yes' to continue: " ans; \
	[ "$$ans" = "yes" ] || { echo "aborted"; exit 1; }
	bash scripts/down.sh --reset

status: ## Container + server + UI + daemon status, all in one
	@bash scripts/status.sh
	@echo
	@bash scripts/ui.sh status
	@bash scripts/scheduler.sh status
	@bash scripts/embed-server.sh status
	@bash scripts/embed-worker.sh status

logs: ## Follow ArcadeDB container logs (N=lines, default 100)
	bash scripts/logs.sh $(N)

##@ Graph UI

ui-up: ## Start the graph UI (http://127.0.0.1:8420/; ARGS=--tailscale to serve the tailnet over HTTPS)
	bash scripts/ui.sh start $(ARGS)

ui-down: ## Stop the graph UI
	bash scripts/ui.sh stop

ui-restart: ui-down ui-up ## Restart the graph UI

ui-status: ## Graph UI status
	bash scripts/ui.sh status

ui-run: ## Run the graph UI in the foreground (debugging; e.g. ARGS=--read-only)
	bash scripts/ui.sh run $(ARGS)

##@ Daemons (scheduler, embedder, embed worker)

scheduler-up: ## Start the maintenance scheduler daemon
	bash scripts/scheduler.sh start

scheduler-down: ## Stop the maintenance scheduler daemon
	bash scripts/scheduler.sh stop

scheduler-status: ## Maintenance scheduler status
	bash scripts/scheduler.sh status

scheduler-run: ## Run the scheduler in the foreground (e.g. ARGS=--once)
	bash scripts/scheduler.sh run $(ARGS)

embed-up: ## Start the local Ollama embedding daemon
	bash scripts/embed-server.sh start

embed-down: ## Stop the local Ollama embedding daemon
	bash scripts/embed-server.sh stop

embed-status: ## Embedding daemon status
	bash scripts/embed-server.sh status

embed-warm: ## Warm the embedding model into memory
	bash scripts/embed-server.sh warm

worker-up: ## Start the async embed worker
	bash scripts/embed-worker.sh start

worker-down: ## Stop the async embed worker
	bash scripts/embed-worker.sh stop

worker-status: ## Embed worker status (+ pending count)
	bash scripts/embed-worker.sh status

worker-run: ## Run the embed worker in the foreground (debugging)
	bash scripts/embed-worker.sh run $(ARGS)

##@ Jobs (the scheduler's nightly/weekly chain, runnable by hand)

jobs: knn-cache provocation-digest resurface link-expiry ## Run the full maintenance chain now, in cadence order

knn-cache: ## Rebuild the k-NN adjacency cache (nightly)
	bash scripts/knn-cache.sh $(ARGS)

provocation-digest: ## Render all 7 moves -> recent/provocations.md, stage top suggestions (nightly)
	bash scripts/provocation-digest.sh $(ARGS)

resurface: ## Re-encounter orphan/inhibited/anniversary notes -> recent/resurface.md (weekly)
	bash scripts/resurface.sh $(ARGS)

link-expiry: ## Sweep stale suggested BINDS edges (weekly)
	bash scripts/link-expiry.sh $(ARGS)

recent-notes: ## Render the most recent day's notes -> recent/recent-notes.md
	bash scripts/recent-notes.sh

embed-backfill: ## One-shot: embed any notes still lacking an embedding
	bash scripts/embed-backfill.sh $(ARGS)

##@ Reports (read-only analytics, spec §13 — writes nothing)

reports: fitness debt criticality communities visited ## Run the core report set back-to-back

report: ## Any analytics report by name: make report R=autocatalysis ARGS="--json"
	bash scripts/analytics.sh $(R) $(ARGS)

fitness: ## Note standing report
	bash scripts/analytics.sh fitness $(ARGS)

debt: ## Structural debt / move-7 candidates
	bash scripts/analytics.sh debt $(ARGS)

criticality: ## Is the corpus at the edge of chaos?
	bash scripts/analytics.sh criticality $(ARGS)

communities: ## Detected communities + diagnostics
	bash scripts/analytics.sh communities $(ARGS)

autocatalysis: ## Communities that cycle under catalysis
	bash scripts/analytics.sh autocatalysis $(ARGS)

visited: ## Human attention per note
	bash scripts/analytics.sh visited $(ARGS)

walks: ## Recorded walks, reconstructed from the Op log
	bash scripts/analytics.sh walks $(ARGS)

##@ Content workflows

add-note: ## Capture a note: make add-note ARGS="--title '...' --body '...'"
	bash scripts/add-note.sh $(ARGS)

ingest-staging: ## Ingest id-named files dropped in staging/ (e.g. ARGS=--dry-run)
	bash scripts/ingest-staging.sh $(ARGS)

search: ## Find notes: make search ARGS="-q 'some phrase'"
	bash scripts/search.sh $(ARGS)

walk: ## Record a reading session: make walk ARGS="start <id>"
	bash scripts/walk.sh $(ARGS)

link: ## BINDS ratification flow: make link ARGS=list
	bash scripts/link.sh $(ARGS)

provoke: ## Provocation move 1 (semantically near, graph-far): e.g. ARGS=--stage
	bash scripts/provoke.sh $(ARGS)

##@ Data & ops

backup: ## On-demand hot backup -> ./backups
	bash scripts/backup.sh

restore: ## Restore a backup into a NEW db: make restore ZIP=file.zip [TARGET=name]
	bash scripts/restore.sh $(ZIP) $(TARGET)

drop-db: ## Drop a scratch database (refuses the live one): make drop-db TARGET=indexia_restore
	bash scripts/drop-db.sh $(TARGET)

console: ## Interactive ArcadeDB SQL console
	bash scripts/console.sh

apply-ddl: ## Create DB if absent + apply ddl/schema.sql (idempotent)
	bash scripts/apply-ddl.sh

smoke-test: ## End-to-end smoke test (self-cleaning SMOKE-* rows)
	bash scripts/smoke-test.sh

##@ Setup & one-time migrations

setup-ollama: ## One-time (no-sudo) install of the local Ollama embedder + model
	bash scripts/setup-ollama.sh

gen-env: ## (Re)generate docker/.env dev secrets
	bash scripts/gen-env.sh

gen-cert: ## (Re)generate the self-signed TLS keystore
	bash scripts/gen-cert.sh

new-id: ## Mint fresh note ids: make new-id N=5
	bash scripts/new-id.sh $(N)

promote-type: ## Register a new vertex/edge type: make promote-type ARGS="..."
	bash scripts/promote-type.sh $(ARGS)

seed-binds: ## Replay an associative layer from a TSV manifest: make seed-binds ARGS=file.tsv
	bash scripts/seed-binds.sh $(ARGS)

backfill-link-dates: ## One-time: date BINDS edges predating BINDS.created_at (e.g. ARGS=--dry-run)
	bash scripts/backfill-link-dates.sh $(ARGS)

migrate-v0-8-0: ## One-time migration to the v0.8.0 schema (irreversible; wants a recent backup)
	bash scripts/migrate-v0-8-0.sh $(ARGS)

transcribe-scans: ## Interactive: transcribe staging/scans/ via the transcribe-notes skill
	bash scripts/transcribe-scans.sh $(ARGS)

review-transcripts: ## Interactive: review staging/transcripts/ via the review-transcripts skill
	bash scripts/review-transcripts.sh $(ARGS)

##@ Tests

test: ## Run the full test suite (against the live corpus; self-cleaning)
	bash tests/run.sh $(ARGS)

test-fast: ## Full suite, skipping the slow cold-ANN checks
	INDEXIA_TESTS_FAST=1 bash tests/run.sh $(ARGS)

test-unit: ## Only the tests that need no database
	bash tests/run.sh --unit
