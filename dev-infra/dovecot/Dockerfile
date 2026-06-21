FROM dovecot/dovecot:2.3

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    inotify-tools \
    openssl \
    && rm -rf /var/lib/apt/lists/*

COPY conf.d/ /etc/dovecot/conf.d/
COPY dovecot.conf /etc/dovecot/dovecot.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN groupmod -g 5000 vmail || true && \
    usermod -u 5000 -g 5000 -d /var/mail/vhosts -s /usr/sbin/nologin vmail || true && \
    mkdir -p /var/mail/vhosts /var/lib/dovecot-sieve /etc/dovecot/users.d && \
    chown -R vmail:vmail /var/mail/vhosts /var/lib/dovecot-sieve

EXPOSE 143 993 110 995 24 2000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["dovecot", "-F"]
