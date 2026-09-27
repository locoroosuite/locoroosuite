#!/bin/sh
# Generate a self-signed TLS cert for the 5349 (turns) listener on first
# start. Production would mount real certificates instead.
set -eu

if [ -f /data/tls/cert.pem ] && [ -f /data/tls/pkey.pem ]; then
    # Operator-provided certificates (or leftovers from an older writable
    # start): use them where turnserver.conf expects them.
    exec turnserver "$@"
fi

# The named volume is root-owned while the coturn image runs as an
# unprivileged user (nobody), so /data/tls is not writable. Self-signed dev
# certs go to /tmp instead and are passed on the CLI, overriding the config
# file paths. They regenerate on each start — fine for dev; browsers do not
# pin TURN TLS certificates.
mkdir -p /tmp/tls
openssl req -x509 -newkey rsa:2048 -keyout /tmp/tls/pkey.pem \
    -out /tmp/tls/cert.pem -days 3650 -nodes \
    -subj "/CN=turn.localhost" >/dev/null 2>&1
echo "turn: generated self-signed TLS certificate for turn.localhost"

exec turnserver --cert=/tmp/tls/cert.pem --pkey=/tmp/tls/pkey.pem "$@"
