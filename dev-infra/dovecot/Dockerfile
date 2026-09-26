FROM dovecot/dovecot:2.3

# Fully-static busybox (musl build) provides wget for the IMAPSieve pipe
# scripts (rspamd controller is plain HTTP, so no CA certificates are needed).
# Installed as a binary copy instead of apt because Debian bullseye left LTS
# (2026-08) and its package pools are no longer fully served by the standard
# mirrors. openssl ships in the base image; the former inotify-tools dependency
# was unused in this container.
COPY --from=busybox:musl /bin/busybox /bin/busybox

COPY conf.d/ /etc/dovecot/conf.d/
COPY dovecot.conf /etc/dovecot/dovecot.conf
COPY entrypoint.sh /entrypoint.sh

# vmail UID/GID must match the app's expectations BEFORE sievec runs, because
# sievec drops privileges to mail_uid (first_valid_uid=5000 in dovecot.conf).
RUN groupmod -g 5000 vmail || true && \
    usermod -u 5000 -g 5000 -d /var/mail/vhosts -s /usr/sbin/nologin vmail || true && \
    mkdir -p /var/mail/vhosts /var/lib/dovecot-sieve /etc/dovecot/users.d && \
    chown -R vmail:vmail /var/mail/vhosts /var/lib/dovecot-sieve

# Global sieve scripts (mirror of production /var/lib/dovecot/sieve/):
# compiled at build time with the plugins the runtime uses. The pipe dir must
# stay root-owned and NOT writable by vmail (sieve_pipe security requirement).
COPY sieve/ /var/lib/dovecot/sieve/
COPY pipe/ /var/lib/dovecot/sieve/pipe/
RUN sievec /var/lib/dovecot/sieve/global-spam.sieve /var/lib/dovecot/sieve/global-spam.svbin && \
    sievec -P sieve_extprograms -P sieve_imapsieve -x "+vnd.dovecot.pipe" /var/lib/dovecot/sieve/learn-spam.sieve /var/lib/dovecot/sieve/learn-spam.svbin && \
    sievec -P sieve_extprograms -P sieve_imapsieve -x "+vnd.dovecot.pipe" /var/lib/dovecot/sieve/learn-ham.sieve /var/lib/dovecot/sieve/learn-ham.svbin && \
    chown -R root:root /var/lib/dovecot/sieve && \
    chmod 644 /var/lib/dovecot/sieve/*.sieve /var/lib/dovecot/sieve/*.svbin && \
    chmod 755 /var/lib/dovecot/sieve/pipe/*.sh

RUN chmod +x /entrypoint.sh

EXPOSE 143 993 110 995 24 2000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["dovecot", "-F"]
