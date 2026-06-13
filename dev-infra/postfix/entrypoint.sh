#!/bin/sh
set -e

mkdir -p /var/spool/postfix/private

cp /tmp/master.cf.override /etc/postfix/master.cf

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
