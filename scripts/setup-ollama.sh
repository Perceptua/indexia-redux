#!/usr/bin/env bash
# One-time (idempotent) provisioning of the local Ollama embedding backend —
# no sudo, installs under ~/opt/ollama. Downloads the binary if absent, extracts
# it (zstd via the python `zstandard` module, so no zstd binary is needed), starts
# the daemon, pulls the embedding model, and smoke-tests one embedding.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

OLLAMA_PREFIX="${OLLAMA_PREFIX:-$HOME/opt/ollama}"
OLLAMA_BIN="$OLLAMA_PREFIX/bin/ollama"
TARBALL="${OLLAMA_TARBALL:-/tmp/ollama.tar.zst}"
URL="${OLLAMA_URL:-https://github.com/ollama/ollama/releases/download/v0.32.1/ollama-linux-amd64.tar.zst}"
MODEL="${INDEXIA_EMBED_MODEL:-mxbai-embed-large}"
export OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

# 1. Binary -------------------------------------------------------------------
if [[ -x "$OLLAMA_BIN" ]]; then
  echo "[setup] ollama already installed at $OLLAMA_BIN"
else
  if [[ ! -f "$TARBALL" ]]; then
    echo "[setup] downloading ollama -> $TARBALL"
    curl -fSL "$URL" -o "$TARBALL"
  fi
  echo "[setup] ensuring python 'zstandard' is available"
  python3 -c "import zstandard" 2>/dev/null \
    || python3 -m pip install --user --break-system-packages --quiet zstandard
  echo "[setup] extracting $(basename "$TARBALL") -> $OLLAMA_PREFIX"
  mkdir -p "$OLLAMA_PREFIX"
  python3 - "$TARBALL" "$OLLAMA_PREFIX" <<'PY'
import sys, tarfile, zstandard
tar_zst, dest = sys.argv[1], sys.argv[2]
with open(tar_zst, "rb") as fh, zstandard.ZstdDecompressor().stream_reader(fh) as reader:
    with tarfile.open(fileobj=reader, mode="r|") as tf:
        tf.extractall(dest, filter="fully_trusted")  # official release, trusted
print("[setup] extracted")
PY
  [[ -x "$OLLAMA_BIN" ]] || { echo "[setup] ERROR: $OLLAMA_BIN missing after extract" >&2; exit 1; }
fi
"$OLLAMA_BIN" --version 2>&1 | head -1 || true

# 2. Daemon + model -----------------------------------------------------------
bash "$ROOT/scripts/embed-server.sh" start
echo "[setup] pulling embedding model: $MODEL (first run downloads ~600-700MB)"
"$OLLAMA_BIN" pull "$MODEL"

# 3. Smoke-test one embedding -------------------------------------------------
echo "[setup] testing an embedding"
curl -sf "$OLLAMA_HOST/api/embeddings" -d "{\"model\":\"$MODEL\",\"prompt\":\"indexia embed smoke\"}" \
  | python3 -c 'import json,sys; v=json.load(sys.stdin).get("embedding",[]); print(f"[setup] OK: {len(v)}-dim vector"); sys.exit(0 if v else 1)'
echo "[setup] ollama ready — embed-on-commit is live."
