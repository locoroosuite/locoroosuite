#!/bin/sh
# Generate a self-signed TLS cert for the 5349 (turns) listener on first
# start. Production would mount real certificates instead.
set -eu

mkdir -p /data/tls
if [ ! -f /data/tls/cert.pem ]; then
    openssl req -x509 -newkey rsa:2048 -keyout /data/tls/pkey.pem \
        -out /data/tls/cert.pem -days 3650 -nodes \
        -subj "/CN=turn.localhost" >/dev/null 2>&1
    echo "turn: generated self-signed TLS certificate for turn.localhost"
fi

exec turnserver "$@"
