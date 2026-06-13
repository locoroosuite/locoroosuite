FROM alpine:3.19

RUN apk add --no-cache opendkim openssl

COPY opendkim.conf /etc/opendkim/opendkim.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN mkdir -p /etc/opendkim/keys /var/run/opendkim && \
    echo "127.0.0.1\nlocalhost\n*.dev.local\n10.0.0.0/8\n172.16.0.0/12\n192.168.0.0/16" > /etc/opendkim/trusted-hosts

EXPOSE 8891

ENTRYPOINT ["/entrypoint.sh"]
CMD ["opendkim", "-f", "-x", "/etc/opendkim/opendkim.conf", "-u", "opendkim"]
