#!/bin/sh
set -e

mkdir -p /var/run/opendkim /etc/opendkim/keys

if [ ! -f /etc/opendkim/trusted-hosts ]; then
  cat > /etc/opendkim/trusted-hosts << 'EOF'
127.0.0.1
localhost
*.dev.local
10.0.0.0/8
172.16.0.0/12
192.168.0.0/16
EOF
fi

if [ ! -f /etc/opendkim/key-table ]; then
  touch /etc/opendkim/key-table
fi

if [ ! -f /etc/opendkim/signing-table ]; then
  touch /etc/opendkim/signing-table
fi

if [ ! -f /etc/opendkim/keys/default.private ]; then
  echo "Generating default DKIM key for dev.local..."
  openssl genrsa -out /etc/opendkim/keys/default.private 2048 2>/dev/null
  openssl rsa -in /etc/opendkim/keys/default.private -pubout -out /etc/opendkim/keys/default.txt 2>/dev/null

  if ! grep -q "default._domainkey.dev.local" /etc/opendkim/key-table 2>/dev/null; then
    echo "default._domainkey.dev.local dev.local:default:/etc/opendkim/keys/default.private" >> /etc/opendkim/key-table
  fi
  if ! grep -q "dev.local" /etc/opendkim/signing-table 2>/dev/null; then
    echo "*@dev.local default._domainkey.dev.local" >> /etc/opendkim/signing-table
  fi
fi

chown -R opendkim:opendkim /etc/opendkim/keys 2>/dev/null || true

exec "$@"
