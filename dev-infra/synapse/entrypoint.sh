#!/bin/sh
set -e

# Re-seed when the config predates MAS delegation: Synapse must delegate
# authentication to MAS (the app cannot provision without it), and stale
# volumes keep their originally-seeded homeserver.yaml.
if [ ! -f /data/homeserver.yaml ] || ! grep -q "matrix_authentication_service" /data/homeserver.yaml; then
  echo "[synapse-dev] seeding homeserver.yaml"
  cp /config/homeserver.yaml /data/homeserver.yaml
fi

if [ ! -f /data/homeserver.signing.key ]; then
  echo "[synapse-dev] generating signing key"
  python - <<'PYEOF'
from base64 import b64encode

from nacl.signing import SigningKey

seed = SigningKey.generate().encode()
with open("/data/homeserver.signing.key", "w") as fh:
    fh.write("ed25519 0 " + b64encode(seed).decode().rstrip("=") + "\n")
PYEOF
fi

echo "[synapse-dev] starting synapse"
exec python -m synapse.app.homeserver --config-path /data/homeserver.yaml
