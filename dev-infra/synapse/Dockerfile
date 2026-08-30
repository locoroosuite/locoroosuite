FROM matrixdotorg/synapse:latest

COPY homeserver.yaml /config/homeserver.yaml
COPY entrypoint.sh /synapse-dev-entrypoint.sh
RUN chmod +x /synapse-dev-entrypoint.sh

ENTRYPOINT ["/synapse-dev-entrypoint.sh"]
