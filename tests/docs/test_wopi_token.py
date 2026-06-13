import json
import time

import pytest


def test_generate_and_validate_token(app):
    with app.app_context():
        from app.modules.docs.services.wopi_token import generate_token, validate_token
        token = generate_token("doc-1", 42, 7, writable=True)
        payload = validate_token(token)
        assert payload is not None
        assert payload["doc_id"] == "doc-1"
        assert payload["user_id"] == 42
        assert payload["account_id"] == 7
        assert payload["writable"] is True


def test_validate_token_empty(app):
    with app.app_context():
        from app.modules.docs.services.wopi_token import validate_token
        assert validate_token("") is None
        assert validate_token(None) is None


def test_validate_token_tampered(app):
    with app.app_context():
        from app.modules.docs.services.wopi_token import generate_token, validate_token
        token = generate_token("doc-1", 1, 1)
        tampered = token[:-5] + "xxxxx"
        assert validate_token(tampered) is None


def test_validate_token_expired(app):
    with app.app_context():
        from app.modules.docs.services.wopi_token import generate_token, validate_token
        token = generate_token("doc-1", 1, 1)
        payload = validate_token(token)
        assert payload is not None
        payload["exp"] = int(time.time()) - 100
        header_b64 = token.split(".", 1)[0]
        from app.modules.docs.services.wopi_token import _b64url_encode, _sign
        new_header = _b64url_encode(json.dumps(payload, sort_keys=True).encode())
        new_sig = _sign(new_header, app.config["WOPI_JWT_SECRET"])
        expired_token = f"{new_header}.{new_sig}"
        assert validate_token(expired_token) is None


def test_token_readonly(app):
    with app.app_context():
        from app.modules.docs.services.wopi_token import generate_token, validate_token
        token = generate_token("doc-1", 1, 1, writable=False)
        payload = validate_token(token)
        assert payload["writable"] is False
