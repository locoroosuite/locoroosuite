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

# Daily Junk/Trash retention sweep - dev mirror of production's
# mailbox-retention.timer + /usr/local/bin/mailbox-retention-sweep.sh.
# The dev userdb source is /var/lib/dovecot-users/passwd (mail-api managed),
# the production equivalent is /etc/dovecot/users. Retention is env-tunable.
retention_sweep() {
  junk="${JUNK_RETENTION:-30d}"
  trash="${TRASH_RETENTION:-30d}"
  passwd_file="/var/lib/dovecot-users/passwd"
  [ -f "$passwd_file" ] || return 0
  cut -d: -f1 "$passwd_file" | grep -v '^$' | while IFS= read -r user; do
    doveadm expunge -u "$user" mailbox Junk savedbefore "$junk" 2>/dev/null \
      || echo "retention: Junk sweep failed for $user" >&2
    doveadm expunge -u "$user" mailbox Trash savedbefore "$trash" 2>/dev/null \
      || echo "retention: Trash sweep failed for $user" >&2
  done
}
(
  # Give Dovecot time to start before the first sweep, then sweep daily.
  sleep 60
  while true; do
    retention_sweep
    sleep 86400
  done
) &

doveadm pw -l 2>/dev/null || true

echo "Starting Dovecot..."

exec "$@"
