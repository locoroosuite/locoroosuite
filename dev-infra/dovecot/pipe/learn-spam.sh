#!/bin/sh
# IMAPSieve pipe: feed the message on stdin to rspamd as spam.
# Dev equivalent of production's `exec /usr/bin/rspamc learn_spam` - the
# Dovecot image has no HTTP client (and its apt pools are gone post-LTS),
# so the fully-static busybox wget posts to the controller API instead.
tmp=$(mktemp) || exit 1
cat > "$tmp"
resp=$(/bin/busybox wget -q -O - --post-file="$tmp" http://rspamd:11334/learnspam)
rc=$?
rm -f "$tmp"
if [ "$rc" -ne 0 ]; then
  echo "learn-spam.sh: wget failed (exit $rc)" >&2
  exit 1
fi
case "$resp" in
  *'"success":true'*|*'"success": true'*) exit 0 ;;
  *)
    echo "learn-spam.sh: rspamd rejected learn: $resp" >&2
    exit 1
    ;;
esac
