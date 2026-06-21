#!/bin/sh
set -e

mkdir -p /var/mail/vhosts /var/lib/dovecot-sieve /var/run/dovecot /var/spool/postfix/private
mkdir -p /var/lib/dovecot-sieve 2>/dev/null || true
chown -R vmail:vmail /var/lib/dovecot-sieve 2>/dev/null || true

if [ ! -f /etc/dovecot/ssl/tls.crt ]; then
  echo "Generating self-signed TLS certificate for Dovecot..."
  mkdir -p /etc/dovecot/ssl
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout /etc/dovecot/ssl/tls.key \
    -out /etc/dovecot/ssl/tls.crt \
    -days 3650 \
    -subj "/CN=mail.dev.local/O=LocoRoomail Dev" \
    -addext "subjectAltName=DNS:mail.dev.local,DNS:dovecot,DNS:localhost,IP:127.0.0.1"
  echo "TLS certificate generated."
fi

if [ ! -f /etc/dovecot/users ]; then
  touch /etc/dovecot/users
fi

doveadm pw -l 2>/dev/null || true

echo "Starting Dovecot..."

exec "$@"
