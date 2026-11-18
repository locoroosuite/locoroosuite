# Pinned to a release tag instead of :latest so dev e2e runs are reproducible
# and upgrades are deliberate (HLD U25.16/U26.11). Refresh via
# `make images-audit`. The chat module requires Synapse 1.159+ (U25.16).
FROM matrixdotorg/synapse:v1.165.0

COPY homeserver.yaml /config/homeserver.yaml
COPY entrypoint.sh /synapse-dev-entrypoint.sh
RUN chmod +x /synapse-dev-entrypoint.sh

ENTRYPOINT ["/synapse-dev-entrypoint.sh"]
