"""TURN admin settings: validation + verify-before-commit + health row (U25.21/U1b.11)."""

import struct
from types import SimpleNamespace
from unittest.mock import patch

from app.admin.services.health_checks import _check_turn
from app.admin.services.turn_verify import (
    ALLOCATE_ERROR,
    ALLOCATE_REQUEST,
    ALLOCATE_SUCCESS,
    ATTR_ERROR_CODE,
    ATTR_FINGERPRINT,
    ATTR_MESSAGE_INTEGRITY,
    ATTR_NONCE,
    ATTR_REALM,
    ATTR_REQUESTED_TRANSPORT,
    ATTR_USERNAME,
    MAGIC_COOKIE,
    _build_allocate,
    _error_code,
    _exchange_udp,
    _parse_response,
    parse_port,
    validate_turn_fields,
    verify_turn_server,
)
from app.shared.db import db
from app.shared.models.core import Domain

SECRET = "unit-test-shared-secret-0123"


def _create_domain(app, name="turn-test.com"):
    domain_id = None
    with app.app_context():
        domain = Domain()
        domain.name = name
        domain.is_active = True
        domain.status = "review"
        domain.imap_host = ""
        domain.imap_port = 993
        domain.smtp_host = ""
        domain.smtp_port = 587
        domain.smtp_tls_mode = "starttls"
        db.session.add(domain)
        db.session.flush()
        domain_id = domain.id
        db.session.commit()
    return domain_id


def _turn_form(**overrides):
    data = {"turn_host": "", "turn_port": "", "turn_tls_port": "", "turn_shared_secret": ""}
    data.update(overrides)
    return data


# --- pure validation -----------------------------------------------------


def test_parse_port():
    assert parse_port(None) == (None, None)
    assert parse_port("") == (None, None)
    assert parse_port(" 3478 ") == (3478, None)
    assert parse_port("abc")[1] is not None
    assert parse_port("0")[1] is not None
    assert parse_port("70000")[1] is not None


def test_validate_turn_fields_ok():
    assert validate_turn_fields("turn.example.com", 3478, 5349, SECRET) == []


def test_validate_turn_fields_both_or_neither():
    errors = validate_turn_fields("turn.example.com", 3478, None, None)
    assert any("shared secret is required" in e for e in errors)
    errors = validate_turn_fields(None, None, None, SECRET)
    assert any("host is required" in e for e in errors)
    assert validate_turn_fields(None, None, None, None) == []


def test_validate_turn_fields_host_format():
    for bad in ("turn://example.com", "example.com:3478", "exa mple.com", ""):
        if bad == "":
            continue
        assert validate_turn_fields(bad, 3478, None, SECRET), bad


def test_validate_turn_fields_secret_length_and_ports():
    assert any("at least 16" in e for e in validate_turn_fields("t.example.com", 1, None, "short"))
    assert any(
        "must differ" in e for e in validate_turn_fields("t.example.com", 3478, 3478, SECRET)
    )


# --- STUN wire format ----------------------------------------------------


def test_build_allocate_unauthenticated_shape():
    txid = b"\x01" * 12
    msg = _build_allocate(txid)
    msg_type, length, cookie = struct.unpack("!HHI", msg[:8])
    assert msg_type == ALLOCATE_REQUEST
    assert cookie == MAGIC_COOKIE
    assert msg[8:20] == txid
    assert length == len(msg) - 20
    _msg_type, attrs = _parse_response(msg)
    assert ATTR_REQUESTED_TRANSPORT in attrs
    assert attrs[ATTR_REQUESTED_TRANSPORT][0] == 17  # UDP


def test_build_allocate_authenticated_and_integrity():
    txid = b"\x02" * 12
    realm, nonce = "turn.example.com", b"nonce-value"
    password = "cGFzc3dvcmQ="
    msg = _build_allocate(
        txid, username="123:admin-verify", realm=realm, nonce=nonce, password=password
    )
    msg_type, attrs = _parse_response(msg)
    assert msg_type == ALLOCATE_REQUEST
    assert attrs[ATTR_USERNAME] == b"123:admin-verify"
    assert attrs[ATTR_REALM] == realm.encode()
    assert attrs[ATTR_NONCE] == nonce
    assert ATTR_MESSAGE_INTEGRITY in attrs and ATTR_FINGERPRINT in attrs

    # MESSAGE-INTEGRITY covers header+attrs with the length adjusted to
    # include the MI attribute itself (RFC 5389 §15.4).
    import hashlib
    import hmac as hmac_mod

    mi_offset = msg.index(struct.pack("!H", ATTR_MESSAGE_INTEGRITY))
    attrs_before = msg[20:mi_offset]
    head = msg[:2] + struct.pack("!H", len(attrs_before) + 24) + msg[4:20]
    key = hashlib.md5(f"123:admin-verify:{realm}:{password}".encode()).digest()
    expected = hmac_mod.new(key, head + attrs_before, hashlib.sha1).digest()
    assert attrs[ATTR_MESSAGE_INTEGRITY] == expected


def test_parse_response_error_code():
    error_value = bytes([0, 0, 4, 1]) + b"Unauthorized"
    pad = (4 - len(error_value) % 4) % 4
    attr = struct.pack("!HH", ATTR_ERROR_CODE, len(error_value)) + error_value + b"\x00" * pad
    msg = struct.pack("!HHI", ALLOCATE_ERROR, len(attr), MAGIC_COOKIE) + b"\x03" * 12 + attr
    msg_type, attrs = _parse_response(msg)
    assert msg_type == ALLOCATE_ERROR
    assert _error_code(attrs) == 401


# --- UDP exchange against a scripted server ------------------------------


class _FakeTurnUdp:
    """Scripted coturn: 401+nonce+realm first, then a configured verdict."""

    def __init__(self, final_type, final_error=None):
        self.final_type = final_type
        self.final_error = final_error
        self.sent = []
        self._step = 0

    def _response(self, request: bytes) -> bytes:
        txid = request[8:20]
        if self._step == 0:
            self._step = 1
            error_value = bytes([0, 0, 4, 1]) + b"Unauthorized"
            attrs = (
                struct.pack("!HH", ATTR_ERROR_CODE, len(error_value))
                + error_value
                + b"\x00" * ((4 - len(error_value) % 4) % 4)
                + struct.pack("!HH", ATTR_REALM, len(b"turn.example.com"))
                + b"turn.example.com"
                + struct.pack("!HH", ATTR_NONCE, len(b"n0nc3"))
                + b"n0nc3"
            )
            return struct.pack("!HHI", ALLOCATE_ERROR, len(attrs), MAGIC_COOKIE) + txid + attrs
        if self.final_type == ALLOCATE_SUCCESS:
            attrs = struct.pack("!HH", 0x0020, 4) + b"\x00" * 4  # XOR-MAPPED-ADDRESS
            return struct.pack("!HHI", ALLOCATE_SUCCESS, len(attrs), MAGIC_COOKIE) + txid + attrs
        code = self.final_error or 401
        error_value = bytes([0, 0, code // 100, code % 100]) + b"Nope"
        attrs = (
            struct.pack("!HH", ATTR_ERROR_CODE, len(error_value))
            + error_value
            + b"\x00" * ((4 - len(error_value) % 4) % 4)
        )
        return struct.pack("!HHI", ALLOCATE_ERROR, len(attrs), MAGIC_COOKIE) + txid + attrs

    # socket API
    def settimeout(self, timeout):
        self.timeout = timeout

    def sendto(self, data, addr):
        self.sent.append(data)

    def recvfrom(self, bufsize):
        return self._response(self.sent[-1]), ("10.0.0.1", 3478)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_udp_socket(fake):
    return patch(
        "app.admin.services.turn_verify.socket",
        SimpleNamespace(socket=lambda *a, **k: fake, AF_INET=2, SOCK_DGRAM=2),
    )


def test_exchange_udp_success():
    fake = _FakeTurnUdp(ALLOCATE_SUCCESS)
    with _patch_udp_socket(fake):
        result = _exchange_udp("t.example.com", 3478, SECRET, "123:admin-verify", 1.0)
    assert result == "ok"
    assert len(fake.sent) == 2  # unauthenticated challenge + authenticated retry


def test_exchange_udp_auth_failure():
    fake = _FakeTurnUdp(ALLOCATE_ERROR, final_error=401)
    with _patch_udp_socket(fake):
        result = _exchange_udp("t.example.com", 3478, "wrong-secret-value-00", "123:u", 1.0)
    assert result == "auth_failed"


# --- verify_turn_server --------------------------------------------------


def test_verify_turn_server_unreachable():
    def raise_oserror(*args, **kwargs):
        raise OSError("no route")

    with (
        patch("app.admin.services.turn_verify._exchange_tcp", side_effect=raise_oserror),
        patch("app.admin.services.turn_verify._exchange_udp", side_effect=raise_oserror),
    ):
        result = verify_turn_server("t.example.com", 3478, None, SECRET)
    assert result["ok"] is False
    assert result["status"] == "unreachable"
    assert "firewall" in result["message"]


def test_verify_turn_server_tls_failure():
    def raise_oserror(*args, **kwargs):
        raise OSError("tls nope")

    with (
        patch(
            "app.admin.services.turn_verify.socket",
            SimpleNamespace(create_connection=raise_oserror),
        ),
        patch("app.admin.services.turn_verify._exchange_tcp", return_value="ok"),
    ):
        result = verify_turn_server("t.example.com", 3478, 5349, SECRET)
    assert result["ok"] is False
    assert result["status"] == "tls_failed"


def test_verify_turn_server_connected_and_auth_failed():
    with patch("app.admin.services.turn_verify._exchange_tcp", return_value="ok"):
        result = verify_turn_server("t.example.com", 3478, None, SECRET)
    assert result == {"ok": True, "status": "connected", "message": result["message"]}
    with patch("app.admin.services.turn_verify._exchange_tcp", return_value="auth_failed"):
        result = verify_turn_server("t.example.com", 3478, None, SECRET)
    assert result["ok"] is False
    assert result["status"] == "auth_failed"
    assert "static-auth-secret" in result["message"]


# --- save endpoint (verify-before-commit, U1b.11) ------------------------


def _post_turn(client, domain_id, data):
    return client.post(f"/admin/domains/{domain_id}/dav-config", data=data)


def _fetch_domain(app, domain_id):
    with app.app_context():
        domain = db.session.get(Domain, domain_id)
        assert domain is not None
        return (
            domain.turn_host,
            domain.turn_port,
            domain.turn_tls_port,
            domain.turn_shared_secret,
        )


@patch("app.admin.controllers.admin.log_audit")
def test_save_turn_rejects_invalid_port(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain(app, "bad-port.com")
    resp = _post_turn(
        client,
        domain_id,
        _turn_form(turn_host="t.example.com", turn_port="not-a-port", turn_shared_secret=SECRET),
    )
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False
    assert "TURN port" in resp.get_json()["error"]
    assert _fetch_domain(app, domain_id) == (None, 3478, 5349, None)


@patch("app.admin.controllers.admin.log_audit")
def test_save_turn_rejects_partial_and_bad_values(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain(app, "partial.com")
    resp = _post_turn(
        client, domain_id, _turn_form(turn_host="t.example.com", turn_shared_secret="")
    )
    assert resp.status_code == 400
    assert "shared secret is required" in resp.get_json()["error"]

    resp = _post_turn(
        client, domain_id, _turn_form(turn_host="t.example.com", turn_shared_secret="tooshort")
    )
    assert resp.status_code == 400
    assert "at least 16" in resp.get_json()["error"]

    resp = _post_turn(
        client, domain_id, _turn_form(turn_host="turn://example.com", turn_shared_secret=SECRET)
    )
    assert resp.status_code == 400
    assert "bare hostname" in resp.get_json()["error"]
    assert _fetch_domain(app, domain_id) == (None, 3478, 5349, None)


@patch("app.admin.controllers.admin.log_audit")
def test_save_turn_blocks_unreachable_server(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain(app, "unreachable.com")
    probe = {"ok": False, "status": "unreachable", "message": "nope"}
    with patch("app.admin.services.turn_verify.verify_turn_server", return_value=probe):
        resp = _post_turn(
            client,
            domain_id,
            _turn_form(turn_host="t.example.com", turn_port="3478", turn_shared_secret=SECRET),
        )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "nope"
    assert _fetch_domain(app, domain_id) == (None, 3478, 5349, None)


@patch("app.admin.controllers.admin.log_audit")
def test_save_turn_blocks_secret_mismatch(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain(app, "mismatch.com")
    probe = {"ok": False, "status": "auth_failed", "message": "rejected"}
    with patch("app.admin.services.turn_verify.verify_turn_server", return_value=probe):
        resp = _post_turn(
            client, domain_id, _turn_form(turn_host="t.example.com", turn_shared_secret=SECRET)
        )
    assert resp.status_code == 400
    assert _fetch_domain(app, domain_id) == (None, 3478, 5349, None)


@patch("app.admin.controllers.admin.log_audit")
def test_save_turn_happy_path_persists(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain(app, "happy.com")
    probe = {"ok": True, "status": "connected", "message": "fine"}
    with patch(
        "app.admin.services.turn_verify.verify_turn_server", return_value=probe
    ) as mock_verify:
        resp = _post_turn(
            client,
            domain_id,
            _turn_form(
                turn_host="t.example.com",
                turn_port="3478",
                turn_tls_port="5349",
                turn_shared_secret=SECRET,
            ),
        )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert _fetch_domain(app, domain_id) == ("t.example.com", 3478, 5349, SECRET)
    # The probe ran with the submitted values before committing.
    mock_verify.assert_called_once_with("t.example.com", 3478, 5349, SECRET)


@patch("app.admin.controllers.admin.log_audit")
def test_save_turn_clearing_skips_probe(mock_audit, admin_client, app):
    client, _ = admin_client
    domain_id = _create_domain(app, "clear.com")
    with patch("app.admin.services.turn_verify.verify_turn_server") as mock_verify:
        resp = _post_turn(client, domain_id, _turn_form())
    assert resp.status_code == 200
    # Empty port fields are stored as NULL; consumers default them (turn_port or 3478).
    assert _fetch_domain(app, domain_id) == (None, None, None, None)
    mock_verify.assert_not_called()


def test_save_turn_requires_admin(client):
    resp = client.post("/admin/domains/1/dav-config", data=_turn_form())
    assert resp.status_code == 302


# --- health row ----------------------------------------------------------


class _TurnDomain:
    turn_host: str | None = None
    turn_port: int | None = None
    turn_tls_port: int | None = None
    turn_shared_secret: str | None = None


def test_check_turn_states():
    assert _check_turn(_TurnDomain()) == "not_configured"

    domain = _TurnDomain()
    domain.turn_host, domain.turn_shared_secret = "t.example.com", SECRET
    probes = {
        "connected": "connected",
        "auth_failed": "auth_failed",
        "unreachable": "misconfigured",
    }
    for probe_status, expected in probes.items():
        with patch(
            "app.admin.services.turn_verify.verify_turn_server",
            return_value={"ok": probe_status == "connected", "status": probe_status, "message": ""},
        ):
            assert _check_turn(domain) == expected
