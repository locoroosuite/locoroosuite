"""TURN settings validation and live verification (HLD U25.21 / U1b.11).

Admin saves reject invalid input early, and when a full TURN configuration is
present they probe the real server before committing:

- TCP connect to the TURN port (reachability),
- a TLS handshake when a TLS port is configured,
- an authenticated TURN ``Allocate`` request using freshly minted REST
  credentials (coturn ``static-auth-secret`` mechanism) — the only check that
  detects a shared-secret mismatch, which would otherwise surface as
  silently failing calls.

The Allocate follows RFC 5766/8489 long-term credentials: an unauthenticated
request draws a 401 with NONCE + REALM, the retry carries
USERNAME/REALM/NONCE + MESSAGE-INTEGRITY (key = MD5(user:realm:password) of
the ephemeral REST credential) + FINGERPRINT. Transport: TCP first (framed,
RFC 5766 §7), UDP fallback.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import socket
import ssl
import struct
import time
import zlib

PROBE_TTL_S = 300

MAGIC_COOKIE = 0x2112A442
ALLOCATE_REQUEST = 0x0003
ALLOCATE_SUCCESS = 0x0103
ALLOCATE_ERROR = 0x0113

ATTR_FINGERPRINT = 0x0002
ATTR_USERNAME = 0x0006
ATTR_MESSAGE_INTEGRITY = 0x0008
ATTR_ERROR_CODE = 0x0009
ATTR_REALM = 0x0014
ATTR_NONCE = 0x0015
ATTR_REQUESTED_TRANSPORT = 0x0019

_HOST_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9.\-]*[a-zA-Z0-9])?$")
_MIN_SECRET_LEN = 16


# --- input validation (pure) ---------------------------------------------


def parse_port(raw) -> tuple[int | None, str | None]:
    """Strictly parse a form port field: empty -> (None, None), invalid -> error."""
    if raw is None or str(raw).strip() == "":
        return None, None
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None, "must be a whole number"
    if not 1 <= value <= 65535:
        return None, "must be between 1 and 65535"
    return value, None


def validate_turn_fields(
    host: str | None, port: int | None, tls_port: int | None, secret: str | None
) -> list[str]:
    """Return a list of human-readable validation errors (empty = valid)."""
    errors: list[str] = []
    host = (host or "").strip()
    secret = (secret or "").strip()

    if host and (len(host) > 255 or not _HOST_RE.match(host)):
        errors.append(
            "TURN host must be a bare hostname or IP (no scheme, port, slashes or spaces)."
        )
    if host and not secret:
        errors.append("TURN shared secret is required when a TURN host is set.")
    if secret and not host:
        errors.append("TURN host is required when a shared secret is set.")
    if secret and len(secret) < _MIN_SECRET_LEN:
        errors.append(
            f"TURN shared secret must be at least {_MIN_SECRET_LEN} characters "
            "(short secrets enable TURN credential forgery)."
        )
    if port is not None and tls_port is not None and port == tls_port:
        errors.append("TURN TLS port must differ from the TURN port.")
    return errors


# --- STUN/TURN wire format (pure) ----------------------------------------


def _attr(attr_type: int, value: bytes) -> bytes:
    pad = (4 - len(value) % 4) % 4
    return struct.pack("!HH", attr_type, len(value)) + value + b"\x00" * pad


def _header(msg_type: int, length: int, txid: bytes) -> bytes:
    return struct.pack("!HHI", msg_type, length, MAGIC_COOKIE) + txid


def _rest_credential(secret: str, username: str) -> str:
    """coturn REST password: base64(HMAC-SHA1(secret, username))."""
    return base64.b64encode(
        hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()
    ).decode()


def _build_allocate(
    txid: bytes,
    *,
    username: str | None = None,
    realm: str | None = None,
    nonce: bytes | None = None,
    password: str | None = None,
) -> bytes:
    """Build an Allocate request; authenticated when username/realm/nonce are given."""
    attrs = [_attr(ATTR_REQUESTED_TRANSPORT, struct.pack("!B3x", 17))]
    if username and realm and nonce:
        attrs = [
            _attr(ATTR_USERNAME, username.encode()),
            _attr(ATTR_REALM, realm.encode()),
            _attr(ATTR_NONCE, nonce),
            *attrs,
        ]
        key = hashlib.md5(f"{username}:{realm}:{password}".encode()).digest()
        body = b"".join(attrs)
        mac = hmac.new(
            key, _header(ALLOCATE_REQUEST, len(body) + 24, txid) + body, hashlib.sha1
        ).digest()
        attrs.append(_attr(ATTR_MESSAGE_INTEGRITY, mac))
    body = b"".join(attrs)
    head = _header(ALLOCATE_REQUEST, len(body) + 8, txid)
    crc = (zlib.crc32(head + body) ^ 0x5354554E) & 0xFFFFFFFF
    return head + body + _attr(ATTR_FINGERPRINT, struct.pack("!I", crc))


def _parse_response(data: bytes) -> tuple[int, dict[int, bytes]]:
    """Return (message_type, attributes) for a STUN response."""
    if len(data) < 20:
        raise ValueError("short STUN response")
    msg_type = struct.unpack("!H", data[:2])[0]
    attrs: dict[int, bytes] = {}
    offset = 20
    while offset + 4 <= len(data):
        attr_type, attr_len = struct.unpack("!HH", data[offset : offset + 4])
        attrs[attr_type] = data[offset + 4 : offset + 4 + attr_len]
        offset += 4 + ((attr_len + 3) // 4) * 4
    return msg_type, attrs


def _error_code(attrs: dict[int, bytes]) -> int | None:
    raw = attrs.get(ATTR_ERROR_CODE)
    if not raw or len(raw) < 4:
        return None
    return (raw[2] & 0x07) * 100 + raw[3]


# --- transports -----------------------------------------------------------


def _exchange_tcp(host: str, port: int, secret: str, username: str, timeout: float) -> str:
    """Run the two-step Allocate over TCP (2-byte length framing). Raises OSError on transport failure."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        txid = secrets.token_bytes(12)
        sock.sendall(struct.pack("!H", len(_unauth := _build_allocate(txid))) + _unauth)
        msg_type, attrs = _parse_response(_recv_framed(sock))
        if msg_type != ALLOCATE_ERROR:
            return "ok"  # server accepted without auth challenge (no lt-cred-mech)
        nonce, realm = attrs.get(ATTR_NONCE), attrs.get(ATTR_REALM)
        if not nonce or not realm or _error_code(attrs) != 401:
            return "error"
        password = _rest_credential(secret, username)
        txid2 = secrets.token_bytes(12)
        retry = _build_allocate(
            txid2,
            username=username,
            realm=realm.decode("utf-8", "replace"),
            nonce=nonce,
            password=password,
        )
        sock.sendall(struct.pack("!H", len(retry)) + retry)
        msg_type, attrs = _parse_response(_recv_framed(sock))
        return _classify(msg_type, attrs)


def _exchange_udp(host: str, port: int, secret: str, username: str, timeout: float) -> str:
    """Run the two-step Allocate over UDP. Raises OSError/timeout on transport failure."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        txid = secrets.token_bytes(12)
        sock.sendto(_build_allocate(txid), (host, port))
        data, _addr = sock.recvfrom(2048)
        msg_type, attrs = _parse_response(data)
        if msg_type != ALLOCATE_ERROR:
            return "ok"
        nonce, realm = attrs.get(ATTR_NONCE), attrs.get(ATTR_REALM)
        if not nonce or not realm or _error_code(attrs) != 401:
            return "error"
        password = _rest_credential(secret, username)
        txid2 = secrets.token_bytes(12)
        retry = _build_allocate(
            txid2,
            username=username,
            realm=realm.decode("utf-8", "replace"),
            nonce=nonce,
            password=password,
        )
        sock.sendto(retry, (host, port))
        data, _addr = sock.recvfrom(2048)
        msg_type, attrs = _parse_response(data)
        return _classify(msg_type, attrs)


def _recv_framed(sock, max_len: int = 4096) -> bytes:
    header = b""
    while len(header) < 2:
        chunk = sock.recv(2 - len(header))
        if not chunk:
            raise OSError("connection closed mid-frame")
        header += chunk
    (length,) = struct.unpack("!H", header)
    if length > max_len:
        raise OSError("STUN frame too large")
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise OSError("connection closed mid-frame")
        data += chunk
    return data


def _classify(msg_type: int, attrs: dict[int, bytes]) -> str:
    if msg_type == ALLOCATE_SUCCESS:
        return "ok"
    code = _error_code(attrs)
    if code in (401, 438):
        return "auth_failed"
    return "error"


# --- public API -----------------------------------------------------------


def _probe_username() -> str:
    return f"{int(time.time()) + PROBE_TTL_S}:admin-verify"


def verify_turn_server(
    host: str,
    port: int,
    tls_port: int | None,
    secret: str,
    *,
    timeout: float = 4.0,
) -> dict:
    """Verify a full TURN configuration against the live server.

    Returns ``{"ok": bool, "status": str, "message": str}`` where status is
    one of: connected, unreachable, auth_failed, tls_failed, error.
    """
    username = _probe_username()

    # TLS listener first: a bad certificate would otherwise only surface for
    # browsers that prefer turns:.
    if tls_port:
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((host, tls_port), timeout=timeout) as raw:
                raw.settimeout(timeout)
                with ctx.wrap_socket(raw, server_hostname=host):
                    pass
        except (OSError, ssl.SSLError, ValueError) as exc:
            return {
                "ok": False,
                "status": "tls_failed",
                "message": (
                    f"TLS handshake failed on port {tls_port} ({exc.__class__.__name__}). "
                    "Leave the TLS port empty if the TURN server has no certificate."
                ),
            }

    # Authenticated Allocate over TCP, then UDP.
    try:
        result = _exchange_tcp(host, port, secret, username, timeout)
    except (TimeoutError, OSError):
        try:
            result = _exchange_udp(host, port, secret, username, timeout)
        except (TimeoutError, OSError):
            return {
                "ok": False,
                "status": "unreachable",
                "message": (
                    f"The TURN server at {host}:{port} is not reachable. Check that it "
                    f"is running and the firewall allows {port} on TCP and UDP."
                ),
            }

    if result == "ok":
        return {
            "ok": True,
            "status": "connected",
            "message": "TURN server reachable and credentials accepted.",
        }
    if result == "auth_failed":
        return {
            "ok": False,
            "status": "auth_failed",
            "message": (
                "The TURN server rejected the credentials: the shared secret does not "
                "match its static-auth-secret."
            ),
        }
    return {
        "ok": False,
        "status": "error",
        "message": f"The TURN server at {host}:{port} answered but rejected the Allocate request.",
    }
