FROM dovecot/dovecot:2.4.5

# The 2.4 image switches the default user to vmail (uid 1000). This container
# needs root for build steps (usermod renumbers vmail to 5000) and runtime
# (dovecot's master process binds 143/993 and drops privileges to mail_uid
# itself; entrypoint.sh chowns volumes and writes /etc/dovecot/ssl).
USER root

# Fully-static busybox (musl build) provides wget for the IMAPSieve pipe
# scripts (rspamd controller is plain HTTP, so no CA certificates are needed).
# Installed as a binary copy instead of apt because the image is coreutils-free.
# Dovecot 2.4's image ships NO coreutils at all (no mkdir/chown/chmod/cat), so
# beyond the copy we symlink every busybox applet into /usr/local/bin — the
# build RUN steps below and entrypoint.sh both need them. PATH is pinned
# because the stock image PATH omits /bin and starts with a bogus entry
# (/bin/sbin); openssl ships in the base image.
COPY --from=busybox:musl /bin/busybox /bin/busybox
RUN /bin/busybox mkdir -p /usr/local/bin && \
    /bin/busybox --install -s /usr/local/bin
ENV PATH=/usr/local/bin:/bin:/usr/bin:/usr/sbin:/dovecot/bin:/dovecot/sbin

# The 2.4 image ships only the vmail user (upstream runs everything as
# vmail). Starting the master as root for privileged listeners requires the
# dovecot/dovenull privilege-separation users, so create them here.
RUN addgroup -S -g 114 dovecot && adduser -S -D -H -u 114 -G dovecot -s /usr/sbin/nologin dovecot && \
    addgroup -S -g 115 dovenull && adduser -S -D -H -u 115 -G dovenull -s /usr/sbin/nologin dovenull

# The 2.4 image ships its own conf.d defaults (auth.conf with a passdb static,
# mail.conf, ssl.conf, ...) that !include conf.d/*.conf would merge on top of
# ours — remove them so only the files copied below are in effect.
RUN rm -rf /etc/dovecot/conf.d
COPY dovecot.conf /etc/dovecot/dovecot.conf
COPY conf.d/ /etc/dovecot/conf.d/
COPY entrypoint.sh /entrypoint.sh

# vmail UID/GID must match the app's expectations BEFORE sievec runs, because
# sievec drops privileges to mail_uid (first_valid_uid=5000 in dovecot.conf).
RUN groupmod -g 5000 vmail || true && \
    usermod -u 5000 -g 5000 -d /var/mail/vhosts -s /usr/sbin/nologin vmail || true && \
    mkdir -p /var/mail/vhosts /var/lib/dovecot-sieve /etc/dovecot/users.d && \
    chown -R vmail:vmail /var/mail/vhosts /var/lib/dovecot-sieve && \
    # The 2.4 image's /var/lib/dovecot is 0770 owned by the original uid 1000;
    # after renumbering vmail to 5000 the LMTP process (euid 5000) cannot
    # traverse it to reach the sieve scripts -> every delivery defers.
    chown vmail:vmail /var/lib/dovecot

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
