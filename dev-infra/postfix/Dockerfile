FROM debian:bookworm-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    postfix \
    inotify-tools \
    openssl \
    libsasl2-modules \
    && rm -rf /var/lib/apt/lists/*

COPY main.cf /etc/postfix/main.cf
COPY master.cf /tmp/master.cf.override
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN mkdir -p /var/spool/postfix/private

EXPOSE 25 587 465

ENTRYPOINT ["/entrypoint.sh"]
CMD ["postfix", "start-fg"]
