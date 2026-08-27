#!/usr/bin/env bash
# Generate a self-signed PKCS12 keystore for the ArcadeDB HTTPS endpoint.
# Loopback-only dev cert: CN=localhost, SAN localhost + 127.0.0.1. Idempotent (use --force to regen).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
[[ -n "${ARCADEDB_KEYSTORE_PASSWORD:-}" ]] || die "ARCADEDB_KEYSTORE_PASSWORD not set in docker/.env"

KEYSTORE="$ROOT/certs/keystore.p12"
if [[ -f "$KEYSTORE" && "${1:-}" != "--force" ]]; then
  log "keystore already present at certs/keystore.p12 (use --force to regenerate)"; exit 0
fi

mkdir -p "$ROOT/certs"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout "$tmp/key.pem" -out "$tmp/cert.pem" \
  -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" 2>/dev/null
openssl pkcs12 -export -in "$tmp/cert.pem" -inkey "$tmp/key.pem" \
  -name indexia -out "$KEYSTORE" -passout pass:"$ARCADEDB_KEYSTORE_PASSWORD"
chmod 600 "$KEYSTORE"
ok "wrote certs/keystore.p12 (self-signed, CN=localhost, SAN localhost+127.0.0.1, 10y)"
