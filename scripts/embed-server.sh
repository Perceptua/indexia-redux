#!/usr/bin/env bash
# Manage the local Ollama embedding daemon — start | stop | status | warm.
# Ollama serves the embedding model that notelib.py's OllamaEmbedder calls at
# commit time. No sudo / systemd unit: it runs as a detached user process, with
# the model kept resident (OLLAMA_KEEP_ALIVE) and warmed so commits embed fast
# instead of hitting a ~30-90s cold reload after the model idles out.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

OLLAMA_PREFIX="${OLLAMA_PREFIX:-$HOME/opt/ollama}"
OLLAMA_BIN="${OLLAMA_BIN:-$OLLAMA_PREFIX/bin/ollama}"
export OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
# Keep the embedding model in memory so notes don't pay a cold reload each time.
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-24h}"
MODEL="${INDEXIA_EMBED_MODEL:-mxbai-embed-large}"
LOG="${OLLAMA_LOG:-$HOME/.ollama/serve.log}"

bin() { if [[ -x "$OLLAMA_BIN" ]]; then echo "$OLLAMA_BIN"; else command -v ollama; fi; }
have_bin() { [[ -x "$OLLAMA_BIN" ]] || command -v ollama >/dev/null 2>&1; }
is_up() { curl -sf --max-time 3 "$OLLAMA_HOST/api/tags" >/dev/null 2>&1; }

case "${1:-status}" in
  start)
    if ! is_up; then
      have_bin || { echo "[embed] ollama not installed — run scripts/setup-ollama.sh" >&2; exit 1; }
      mkdir -p "$(dirname "$LOG")"
      # setsid + </dev/null fully detaches; systemd (PID 1) reaps it, so it
      # outlives this wsl invocation.
      setsid "$(bin)" serve >"$LOG" 2>&1 </dev/null &
      echo "[embed] starting ollama (pid $!), log: $LOG"
      for i in $(seq 1 30); do is_up && break; sleep 1; done
      is_up || { echo "[embed] did not become ready in 30s — see $LOG" >&2; exit 1; }
    fi
    echo "[embed] up at $OLLAMA_HOST (keep-alive $OLLAMA_KEEP_ALIVE); warming $MODEL in background"
    # Detached warm-up: load the model now so the first commit is hot, without
    # blocking this command (or up.sh) on the cold load.
    setsid bash "$ROOT/scripts/embed-server.sh" warm >/dev/null 2>&1 </dev/null &
    ;;
  warm)
    is_up || { echo "[embed] not running — start it first" >&2; exit 1; }
    echo "[embed] warming $MODEL (up to ~90s if cold)…"
    curl -sf --max-time 180 "$OLLAMA_HOST/api/embeddings" \
      -d "{\"model\":\"$MODEL\",\"prompt\":\"warm\"}" >/dev/null 2>&1 \
      && echo "[embed] warm" || echo "[embed] warm-up did not complete (non-fatal)"
    ;;
  stop)
    pkill -f "ollama serve" 2>/dev/null && echo "[embed] stopped" || echo "[embed] was not running"
    ;;
  status)
    if is_up; then
      echo "[embed] UP at $OLLAMA_HOST (keep-alive $OLLAMA_KEEP_ALIVE)"
      "$(bin)" list 2>/dev/null || true
    else
      echo "[embed] DOWN ($OLLAMA_HOST) — start with scripts/embed-server.sh start"
    fi
    ;;
  *) echo "usage: embed-server.sh [start|stop|status|warm]" >&2; exit 2 ;;
esac
