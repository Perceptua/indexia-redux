#!/usr/bin/env bash
# Start the ArcadeDB container, wait until ready, and apply the schema.
# (Nightly backups run automatically via the built-in scheduler — see docker/backup.json.)
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[[ -f "$ENV_FILE" ]] || { log "no docker/.env — generating dev secrets"; bash "$ROOT/scripts/gen-env.sh"; }
load_env

# Host dirs for the bind mounts (owned by this user; UID 1000 matches the image's arcadedb user).
mkdir -p "$ROOT/data" "$ROOT/backups" "$ROOT/certs"
[[ -f "$ROOT/certs/keystore.p12" ]] || { log "no keystore found — generating"; bash "$ROOT/scripts/gen-cert.sh"; }

log "starting ArcadeDB (docker compose up -d)"
dc up -d

log "waiting for the server to become ready…"
wait_ready 60 || { dc logs --tail 50 arcadedb; die "server did not become ready — see logs above"; }
ok "server ready — Studio/REST at $BASE_URL (user: root)"

bash "$ROOT/scripts/apply-ddl.sh"

# Bring up the embedding backend (non-fatal). Embedding is async: commits never block
# on it — the embed worker drains pending notes in the background, so a missing/slow
# embedder just means notes stay 'pending-embed' until it's back. (Run
# scripts/setup-ollama.sh once if Ollama isn't installed yet.)
bash "$ROOT/scripts/embed-server.sh" start || log "embedder not started (non-fatal — notes stay pending-embed)"
bash "$ROOT/scripts/embed-worker.sh" start || log "embed worker not started (non-fatal — run embed-backfill.sh manually)"

# Start the maintenance scheduler (knn-cache then provocation-digest nightly; resurface and
# link-expiry weekly). Non-fatal — jobs fail open and retry if the DB blips, and the
# scripts/*.sh wrappers stay directly cron-usable if you prefer OS cron.
bash "$ROOT/scripts/scheduler.sh" start || log "scheduler not started (non-fatal — run jobs by hand or via cron)"

# Refresh the recent-notes digest for the most recent day (non-fatal — a digest
# hiccup or an empty corpus must never block bringing the database up).
bash "$ROOT/scripts/recent-notes.sh" || log "recent-notes digest skipped (non-fatal)"

ok "up complete"
