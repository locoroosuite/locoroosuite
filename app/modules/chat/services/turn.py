"""TURN credentials for 1:1 chat calls (HLD U25.21).

Uses coturn's REST-style ephemeral credentials: the username is
``<unix_expiry>:<arbitrary user>`` and the password is the base64
HMAC-SHA1 of the username keyed with the domain's shared secret
(coturn ``static-auth-secret``). Time-limited, so nothing long-lived is
handed to the browser and no per-user TURN accounts exist.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

DEFAULT_TTL_S = 86400


def ice_servers_for_domain(domain, username: str, ttl_s: int = DEFAULT_TTL_S) -> dict:
    """Return the ``GET /app/chat/api/turn`` payload for a domain.

    ``enabled`` is False when TURN is not configured — callers must treat
    that as "calls off" (HLD U25.21 fail-early: buttons hidden, not flaky).
    """
    host = (getattr(domain, "turn_host", None) or "").strip()
    secret = (getattr(domain, "turn_shared_secret", None) or "").strip()
    if not host or not secret:
        return {"enabled": False, "iceServers": []}

    expiry = int(time.time()) + ttl_s
    turn_username = f"{expiry}:{username}"
    digest = hmac.new(secret.encode(), turn_username.encode(), hashlib.sha1).digest()
    credential = base64.b64encode(digest).decode()

    port = getattr(domain, "turn_port", None) or 3478
    urls = [
        f"stun:{host}:{port}",
        f"turn:{host}:{port}?transport=udp",
        f"turn:{host}:{port}?transport=tcp",
    ]
    tls_port = getattr(domain, "turn_tls_port", None)
    if tls_port:
        urls.append(f"turns:{host}:{tls_port}?transport=tcp")
    return {
        "enabled": True,
        "iceServers": [
            {"urls": urls, "username": turn_username, "credential": credential}
        ],
        "ttl": ttl_s,
    }
