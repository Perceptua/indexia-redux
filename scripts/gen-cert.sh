#!/usr/bin/env bash
# Generate a PKCS12 keystore for the ArcadeDB HTTPS endpoint.
#   gen-cert.sh              self-signed loopback-only dev cert: CN=localhost, SAN
#                             localhost + 127.0.0.1 -> certs/keystore.p12. Idempotent
#                             (use --force to regenerate).
#   gen-cert.sh --tailscale   a tailscale-issued cert for this node's MagicDNS name instead ->
#                             certs/tailscale/keystore.p12, alongside the cert.pem/key.pem pair
#                             scripts/ui.py's --tailscale also uses (see scripts/tailscale.py).
#                             Never touches certs/keystore.p12 — the two live side by side so
#                             switching back to loopback needs no regeneration. Re-issues every
#                             call, same as scripts/tailscale.py: cheap, idempotent, no
#                             cache-and-check-expiry step to get wrong.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
[[ -n "${ARCADEDB_KEYSTORE_PASSWORD:-}" ]] || die "ARCADEDB_KEYSTORE_PASSWORD not set in docker/.env"

case "${1:-}" in
  ""|--force) ;;
  --tailscale) ;;
  *) die "usage: gen-cert.sh [--force|--tailscale]" ;;
esac

if [[ "${1:-}" == "--tailscale" ]]; then
  export ROOT
  export TS_DIR="$ROOT/certs/tailscale"
  KEYSTORE="$TS_DIR/keystore.p12"
  read -r cert_pem key_pem fqdn < <(python3 -c '
import sys, os
sys.path.insert(0, os.path.join(os.environ["ROOT"], "scripts"))
import tailscale
from pathlib import Path
c, k, f = tailscale.provision_cert(Path(os.environ["TS_DIR"]))
print(c, k, f)
') || die "tailscale cert provisioning failed — is tailscale up? (see scripts/tailscale.py)"
  openssl pkcs12 -export -in "$cert_pem" -inkey "$key_pem" \
    -name indexia -out "$KEYSTORE" -passout pass:"$ARCADEDB_KEYSTORE_PASSWORD"
  chmod 600 "$KEYSTORE"
  ok "wrote certs/tailscale/keystore.p12 (tailscale-issued, CN=$fqdn)"
  exit 0
fi

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
