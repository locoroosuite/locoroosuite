# coturn TURN server (dev infrastructure, HLD U25.21).
#
# Browsers connect to TURN directly (WebRTC ICE), so the container publishes
# 3478 tcp/udp, 5349 tcp (TLS) and the UDP relay range on the host. The app
# never talks to coturn; it only mints ephemeral REST credentials from the
# domain's `turn_shared_secret` (see app/modules/chat/services/turn.py).
FROM coturn/coturn:4.6

COPY turnserver.conf /etc/coturn/turnserver.conf
COPY entrypoint.sh /usr/local/bin/turn-dev-entrypoint.sh
RUN chmod +x /usr/local/bin/turn-dev-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/turn-dev-entrypoint.sh"]
CMD ["-c", "/etc/coturn/turnserver.conf"]
