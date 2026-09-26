#!/bin/sh
set -e

mkdir -p /var/spool/postfix/private

# /etc/postfix is a named volume (shared with mail-api), so image-baked
# config is masked after the first run. Master.cf is already synced on every
# start; do the same for main.cf so dev-infra/postfix/main.cf changes apply
# after a rebuild (certs and mail-api-managed maps in the volume are kept).
cp /tmp/master.cf.override /etc/postfix/master.cf
cp /tmp/main.cf.override /etc/postfix/main.cf

if [ ! -f /etc/postfix/ssl/tls.crt ]; then
  echo "Generating self-signed TLS certificate for Postfix..."
  mkdir -p /etc/postfix/ssl
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout /etc/postfix/ssl/tls.key \
    -out /etc/postfix/ssl/tls.crt \
    -days 3650 \
    -subj "/CN=mail.dev.local/O=LocoRoomail Dev" \
    -addext "subjectAltName=DNS:mail.dev.local,DNS:postfix,DNS:localhost,IP:127.0.0.1"
  echo "TLS certificate generated."
fi

if [ ! -f /etc/postfix/virtual_domains ]; then
  touch /etc/postfix/virtual_domains
fi

if [ ! -f /etc/postfix/virtual ]; then
  touch /etc/postfix/virtual
fi

postmap /etc/postfix/virtual 2>/dev/null || true

(
  while inotifywait -q -e modify,create,delete /etc/postfix/virtual_domains /etc/postfix/virtual 2>/dev/null; do
    echo "Config change detected, rebuilding maps and reloading..."
    postmap /etc/postfix/virtual 2>/dev/null || true
    postfix reload 2>/dev/null || true
  done
) &

echo "Starting Postfix..."

exec "$@"
