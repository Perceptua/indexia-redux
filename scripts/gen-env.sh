#!/usr/bin/env bash
# Create docker/.env with strong random dev secrets, if it doesn't already exist.
# Root password is built to satisfy a strict policy (upper+lower+digit+special, 33 chars).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [[ -f "$ENV_FILE" && "${1:-}" != "--force" ]]; then
  log "docker/.env already exists (use --force to overwrite)"; exit 0
fi

raw="$(openssl rand -base64 64 | tr -dc 'A-Za-z0-9')"
rootpw="${raw:0:28}Aa9@z"     # guarantees the four character classes
kspw="${raw:28:24}"

umask 077
cat > "$ENV_FILE" <<EOF
# Generated dev secrets — gitignored, do not commit. Regenerate with: scripts/gen-env.sh --force
ARCADEDB_ROOT_PASSWORD=$rootpw
ARCADEDB_KEYSTORE_PASSWORD=$kspw
ARCADEDB_HEAP=1G
EOF
chmod 600 "$ENV_FILE"
ok "wrote docker/.env (root + keystore secrets generated, mode 0600)"
