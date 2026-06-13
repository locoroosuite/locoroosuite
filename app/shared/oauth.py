from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import (
    Blueprint,
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)

from app.shared.db import db
from app.shared.models.oauth import (
    OAuthAccessToken,
    OAuthAuthorizationCode,
    OAuthClient,
)
from app.shared.models.core import CustomerAccount

_logger = logging.getLogger(__name__)

_CONSENT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Authorize {{ client_name }}</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       background:#f5f5f5;display:flex;align-items:center;justify-content:center;
       min-height:100vh;color:#1f2937}
  .card{background:#fff;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.08);
        padding:32px;max-width:480px;width:100%}
  h1{font-size:20px;font-weight:600;margin-bottom:8px}
  p.sub{font-size:14px;color:#6b7280;margin-bottom:20px}
  .group{margin-bottom:16px;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden}
  .group-header{display:flex;align-items:center;justify-content:space-between;
                padding:10px 14px;background:#f9fafb;border-bottom:1px solid #e5e7eb;
                font-size:13px;font-weight:600;color:#374151;cursor:pointer;user-select:none}
  .group-header label{cursor:pointer;display:flex;align-items:center;gap:8px}
  .group-header .toggle{font-size:11px;color:#9ca3af;font-weight:400}
  .scope-item{display:flex;align-items:center;gap:10px;padding:10px 14px;
              font-size:14px;color:#374151;border-bottom:1px solid #f3f4f6}
  .scope-item:last-child{border-bottom:none}
  .scope-item input[type=checkbox]{width:18px;height:18px;accent-color:#3b82f6;
              cursor:pointer;flex-shrink:0}
  .scope-item.disabled{opacity:.6}
  .scope-item.disabled input[type=checkbox]{cursor:default}
  .scope-desc{flex:1}
  .scope-label{font-size:12px;color:#9ca3af;margin-left:auto;white-space:nowrap}
  .actions{display:flex;gap:12px;margin-top:20px}
  .btn{flex:1;padding:10px 16px;border-radius:8px;font-size:14px;font-weight:500;
       cursor:pointer;border:none;transition:background .15s}
  .btn-approve{background:#3b82f6;color:#fff}
  .btn-approve:hover{background:#2563eb}
  .btn-approve:disabled{background:#93c5fd;cursor:not-allowed}
  .btn-deny{background:#e5e7eb;color:#374151}
  .btn-deny:hover{background:#d1d5db}
  .select-all-row{display:flex;align-items:center;gap:8px;padding:8px 14px;
                  font-size:13px;color:#6b7280;border-bottom:1px solid #e5e7eb;
                  background:#f9fafb}
  .select-all-row input{width:16px;height:16px;accent-color:#3b82f6;cursor:pointer}
  .select-all-row label{cursor:pointer}
</style>
</head>
<body>
<div class="card">
  <h1>Authorize {{ client_name }}</h1>
  <p class="sub">Select the permissions you want to grant to this application.</p>
  <form method="post" action="{{ url_for('oauth.authorize') }}" id="consent-form">
    <input type="hidden" name="client_id" value="{{ client_id }}">
    <input type="hidden" name="redirect_uri" value="{{ redirect_uri }}">
    <input type="hidden" name="scope" value="{{ scope }}">
    <input type="hidden" name="resource" value="{{ resource }}">
    <input type="hidden" name="state" value="{{ state }}">
    <input type="hidden" name="code_challenge" value="{{ code_challenge }}">
    <input type="hidden" name="code_challenge_method" value="{{ code_challenge_method }}">
    <input type="hidden" name="response_type" value="code">
    {% for group in scope_groups %}
    <div class="group" data-group="{{ group.module }}">
      <div class="group-header">
        <label>
          <input type="checkbox" class="group-toggle" data-module="{{ group.module }}"
                 onchange="toggleGroup('{{ group.module }}', this.checked)">
          {{ group.label }}
        </label>
        <span class="toggle" id="toggle-{{ group.module }}">none selected</span>
      </div>
      {% for s in group.scopes %}
      <div class="scope-item {% if s.required %}disabled{% endif %}">
        {% if s.required %}
        <input type="hidden" name="scopes" value="{{ s.name }}">
        {% endif %}
        <input type="checkbox" name="scopes" value="{{ s.name }}"
               id="scope-{{ s.name }}" {% if s.checked %}checked{% endif %}
               {% if s.required %}disabled{% endif %}
               onchange="updateGroup('{{ group.module }}')">
        <span class="scope-desc">{{ s.description }}</span>
        <span class="scope-label">{{ s.name }}</span>
      </div>
      {% endfor %}
    </div>
    {% endfor %}
    <div class="actions">
      <button type="submit" name="action" value="deny" class="btn btn-deny">Deny</button>
      <button type="submit" name="action" value="approve" class="btn btn-approve" id="btn-allow">Allow</button>
    </div>
  </form>
</div>
<script>
function toggleGroup(module, checked) {
  var items = document.querySelectorAll('[data-group="' + module + '"] input[name=scopes]');
  items.forEach(function(cb) { if (!cb.disabled) cb.checked = checked; });
  updateGroup(module);
  updateAllowBtn();
}
function updateGroup(module) {
  var items = document.querySelectorAll('[data-group="' + module + '"] input[name=scopes]');
  var allBox = document.querySelector('.group-toggle[data-module="' + module + '"]');
  var total = 0, checked = 0;
  items.forEach(function(cb) {
    total++;
    if (cb.checked) checked++;
  });
  if (allBox) allBox.checked = (checked === total);
  var label = document.getElementById('toggle-' + module);
  if (label) {
    if (checked === 0) label.textContent = 'none selected';
    else if (checked === total) label.textContent = 'all selected';
    else label.textContent = checked + ' of ' + total + ' selected';
  }
  updateAllowBtn();
}
function updateAllowBtn() {
  var all = document.querySelectorAll('input[name=scopes]');
  var any = false;
  all.forEach(function(cb) { if (cb.checked) any = true; });
  document.getElementById('btn-allow').disabled = !any;
}
document.querySelectorAll('.group').forEach(function(g) {
  var module = g.dataset.group;
  updateGroup(module);
});
</script>
</body>
</html>"""

_MISCONFIG_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }}</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       background:#f5f5f5;display:flex;align-items:center;justify-content:center;
       min-height:100vh;color:#1f2937}
  .card{background:#fff;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.08);
        padding:32px;max-width:520px;width:100%}
  h1{font-size:20px;font-weight:600;margin-bottom:8px;color:#dc2626}
  p{font-size:14px;color:#6b7280;line-height:1.6}
  code{background:#f3f4f6;padding:2px 6px;border-radius:4px;font-size:13px}
</style>
</head>
<body>
<div class="card">
  <h1>{{ title }}</h1>
  <p>{{ message }}</p>
</div>
</body>
</html>"""

_SCOPE_DESCRIPTIONS = {
    "mail.read": "Read your email messages",
    "mail.write": "Send email on your behalf",
    "mail": "Full access to your email",
    "contacts.read": "Read your contacts",
    "contacts.write": "Create and modify contacts",
    "contacts": "Full access to your contacts",
    "calendar.read": "View your calendar events",
    "calendar.write": "Create and modify calendar events",
    "calendar": "Full access to your calendar",
    "docs.read": "View your documents",
    "docs.write": "Create and modify documents",
    "docs": "Full access to your documents",
    "openid": "Verify your identity",
}


def _get_issuer(app: Flask) -> str:
    scheme = app.config.get("PREFERRED_URL_SCHEME", "https")
    host = app.config.get("SERVER_NAME") or ""
    if not host:
        try:
            from flask import request
            host = request.host
        except RuntimeError:
            host = "localhost"
    return f"{scheme}://{host}"


def _generate_code() -> str:
    return secrets.token_urlsafe(48)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    digest = hashlib.sha256(code_verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return computed == code_challenge


def _generate_jti() -> str:
    return uuid.uuid4().hex


def _is_valid_redirect_uri(uri: str) -> bool:
    return (
        uri.startswith("https://chatgpt.com/connector/oauth/")
        or uri == "https://chatgpt.com/connector_platform_oauth_redirect"
    )


def get_or_create_key_pair(app: Flask) -> rsa.RSAPrivateKey:
    key_path = app.config.get("OAUTH_SIGNING_KEY_PATH", str(Path("data/oauth_signing_key.pem")))
    pub_path = key_path.replace(".pem", "_pub.pem") if key_path.endswith(".pem") else key_path + ".pub"

    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
        return private_key

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    os.makedirs(os.path.dirname(key_path) or ".", exist_ok=True)
    with open(key_path, "wb") as f:
        f.write(priv_pem)

    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    with open(pub_path, "wb") as f:
        f.write(pub_pem)

    _logger.info("Generated new OAuth signing key pair at %s", key_path)
    return private_key


def get_private_key(app: Flask) -> bytes:
    key_path = app.config.get("OAUTH_SIGNING_KEY_PATH", str(Path("data/oauth_signing_key.pem")))
    with open(key_path, "rb") as f:
        return f.read()


def get_public_key(app: Flask) -> bytes:
    key_path = app.config.get("OAUTH_SIGNING_KEY_PATH", str(Path("data/oauth_signing_key.pem")))
    pub_path = key_path.replace(".pem", "_pub.pem") if key_path.endswith(".pem") else key_path + ".pub"
    with open(pub_path, "rb") as f:
        return f.read()


def get_jwks(app: Flask) -> dict:
    pub_bytes = get_public_key(app)
    pub_key = serialization.load_pem_public_key(pub_bytes)
    numbers = pub_key.public_numbers()
    e_bytes = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, byteorder="big")
    n_bytes = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, byteorder="big")
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": "oauth-signing-key",
                "n": base64.urlsafe_b64encode(n_bytes).rstrip(b"=").decode(),
                "e": base64.urlsafe_b64encode(e_bytes).rstrip(b"=").decode(),
            }
        ]
    }


def _scope_groups(scope_str: str) -> list[dict[str, Any]]:
    scopes = scope_str.split()
    _MODULE_LABELS = {
        "mail": "Email",
        "contacts": "Contacts",
        "calendar": "Calendar",
        "docs": "Documents",
        "openid": "Identity",
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for s in scopes:
        module = s.split(".")[0] if "." in s else s
        if module not in groups:
            groups[module] = []
            order.append(module)
        groups[module].append({
            "name": s,
            "description": _SCOPE_DESCRIPTIONS.get(s, f"Access: {s}"),
            "checked": True,
            "required": s == "openid",
        })
    result: list[dict[str, Any]] = []
    for module in order:
        result.append({
            "module": module,
            "label": _MODULE_LABELS.get(module, module.title()),
            "scopes": groups[module],
        })
    return result


oauth_bp = Blueprint("oauth", __name__)


@oauth_bp.route("/.well-known/oauth-authorization-server")
def authorization_server_metadata():
    app = _current_app()
    issuer = _get_issuer(app)
    return jsonify(
        {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth/authorize",
            "token_endpoint": f"{issuer}/oauth/token",
            "registration_endpoint": f"{issuer}/oauth/register",
            "jwks_uri": f"{issuer}/oauth/jwks.json",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": list(_SCOPE_DESCRIPTIONS.keys()),
            "claims_supported": ["sub", "email", "aud", "iss", "exp", "iat", "jti"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }
    )


@oauth_bp.route("/.well-known/openid-configuration")
def openid_configuration():
    app = _current_app()
    issuer = _get_issuer(app)
    return jsonify(
        {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/oauth/authorize",
            "token_endpoint": f"{issuer}/oauth/token",
            "registration_endpoint": f"{issuer}/oauth/register",
            "jwks_uri": f"{issuer}/oauth/jwks.json",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": list(_SCOPE_DESCRIPTIONS.keys()),
            "claims_supported": ["sub", "email", "aud", "iss", "exp", "iat", "jti"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }
    )


@oauth_bp.route("/.well-known/oauth-protected-resource")
def protected_resource_metadata():
    app = _current_app()
    issuer = _get_issuer(app)
    return jsonify(
        {
            "resource": issuer,
            "authorization_servers": [issuer],
            "bearer_methods_supported": ["header"],
            "scopes_supported": list(_SCOPE_DESCRIPTIONS.keys()),
        }
    )


@oauth_bp.route("/oauth/authorize", methods=["GET", "POST"])
def authorize():
    app = _current_app()

    server_name = app.config.get("SERVER_NAME", "")
    is_dev = app.config.get("APP_ENV") == "development"
    if not server_name and not is_dev:
        _logger.error("OAuth authorize called but SERVER_NAME is not configured")
        return render_template_string(
            _MISCONFIG_TEMPLATE, title="Configuration Required",
            message=(
                "OAuth is not available because SERVER_NAME is not configured. "
                "Set the SERVER_NAME environment variable to your public domain "
                "(e.g., mail.example.com) and restart the application."
            ),
        ), 503

    if request.method == "GET":
        client_id = request.args.get("client_id")
        redirect_uri = request.args.get("redirect_uri")
        response_type = request.args.get("response_type")
        scope = request.args.get("scope", "mail.read")
        resource = request.args.get("resource")
        state = request.args.get("state", "")
        code_challenge = request.args.get("code_challenge")
        code_challenge_method = request.args.get("code_challenge_method", "S256")

        errors = _validate_authorize_params(
            client_id, redirect_uri, response_type, code_challenge, code_challenge_method, resource
        )
        if errors:
            return jsonify({"error": "invalid_request", "error_description": "; ".join(errors)}), 400

        if session.get("role") != "customer" or not session.get("user_id"):
            return redirect(url_for("mail.login", next=request.url))

        client = OAuthClient.query.filter_by(client_id=client_id).first()
        if not client:
            return jsonify({"error": "invalid_client", "error_description": "Unknown client_id"}), 400

        allowed_uris = json.loads(client.redirect_uris)
        if redirect_uri not in allowed_uris:
            return jsonify({"error": "invalid_request", "error_description": "redirect_uri not registered"}), 400

        return render_template_string(
            _CONSENT_TEMPLATE,
            client_name=client.client_name,
            client_id=client_id,
            redirect_uri=redirect_uri,
            scope=scope,
            resource=resource,
            state=state,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope_groups=_scope_groups(scope),
        )

    action = request.form.get("action")
    if action == "deny":
        redirect_uri = request.form.get("redirect_uri", "")
        state = request.form.get("state", "")
        sep = "&" if "?" in redirect_uri else "?"
        return redirect(f"{redirect_uri}{sep}error=access_denied&state={state}")

    client_id = request.form.get("client_id")
    redirect_uri = request.form.get("redirect_uri")
    requested_scope = request.form.get("scope", "mail.read")
    resource = request.form.get("resource")
    state = request.form.get("state", "")
    code_challenge = request.form.get("code_challenge")
    code_challenge_method = request.form.get("code_challenge_method", "S256")

    selected_scopes = request.form.getlist("scopes")
    if not selected_scopes:
        return jsonify({"error": "invalid_request", "error_description": "At least one scope must be selected"}), 400

    requested_set = set(requested_scope.split())
    granted_scopes = list(dict.fromkeys(s for s in selected_scopes if s in requested_set))
    if not granted_scopes:
        return jsonify({"error": "invalid_request", "error_description": "At least one scope must be selected"}), 400

    scope = " ".join(granted_scopes)

    errors = _validate_authorize_params(
        client_id, redirect_uri, "code", code_challenge, code_challenge_method, resource
    )
    if errors:
        return jsonify({"error": "invalid_request", "error_description": "; ".join(errors)}), 400

    customer_id = session.get("user_id")

    from app.shared.keys import get_user_key, set_user_key
    credential_key = get_user_key(customer_id)
    if not credential_key:
        credential_key = session.get("user_key")
        if credential_key:
            set_user_key(customer_id, credential_key)
    if credential_key:
        from app.api.token_service import ensure_api_enabled
        ensure_api_enabled(customer_id, credential_key)

    code = _generate_code()
    code_hash = _hash_code(code)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    auth_code = OAuthAuthorizationCode(
        code_hash=code_hash,
        client_id=client_id,
        customer_id=customer_id,
        redirect_uri=redirect_uri,
        scope=scope,
        resource=resource,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        expires_at=expires_at,
    )
    db.session.add(auth_code)
    db.session.commit()

    sep = "&" if "?" in redirect_uri else "?"
    redirect_url = f"{redirect_uri}{sep}code={code}"
    if state:
        redirect_url += f"&state={state}"
    return redirect(redirect_url)


def _validate_authorize_params(
    client_id: str | None,
    redirect_uri: str | None,
    response_type: str | None,
    code_challenge: str | None,
    code_challenge_method: str | None,
    resource: str | None,
) -> list[str]:
    errors: list[str] = []
    if not client_id:
        errors.append("client_id is required")
    if not redirect_uri:
        errors.append("redirect_uri is required")
    if response_type != "code":
        errors.append("response_type must be 'code'")
    if not code_challenge:
        errors.append("code_challenge is required")
    if code_challenge_method != "S256":
        errors.append("code_challenge_method must be 'S256'")
    if not resource:
        errors.append("resource is required")
    return errors


@oauth_bp.route("/oauth/token", methods=["POST"])
def token():
    app = _current_app()

    grant_type = request.form.get("grant_type")
    code = request.form.get("code")
    redirect_uri = request.form.get("redirect_uri")
    code_verifier = request.form.get("code_verifier")
    client_id = request.form.get("client_id")

    if grant_type != "authorization_code":
        return jsonify({"error": "unsupported_grant_type"}), 400

    if not all([code, redirect_uri, client_id]):
        return jsonify({"error": "invalid_request", "error_description": "Missing required parameters"}), 400

    code_hash = _hash_code(code)
    auth_code = OAuthAuthorizationCode.query.filter_by(code_hash=code_hash).first()

    if not auth_code:
        return jsonify({"error": "invalid_grant", "error_description": "Invalid authorization code"}), 400

    if auth_code.used:
        return jsonify({"error": "invalid_grant", "error_description": "Authorization code already used"}), 400

    if auth_code.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        return jsonify({"error": "invalid_grant", "error_description": "Authorization code expired"}), 400

    if auth_code.client_id != client_id:
        return jsonify({"error": "invalid_grant", "error_description": "client_id mismatch"}), 400

    if auth_code.redirect_uri != redirect_uri:
        return jsonify({"error": "invalid_grant", "error_description": "redirect_uri mismatch"}), 400

    if not code_verifier or not _verify_pkce(code_verifier, auth_code.code_challenge):
        return jsonify({"error": "invalid_grant", "error_description": "PKCE verification failed"}), 400

    issuer = _get_issuer(app)
    now = datetime.now(timezone.utc)
    jti = _generate_jti()
    expires_in = 3600

    payload = {
        "iss": issuer,
        "sub": str(auth_code.customer_id),
        "aud": auth_code.resource,
        "scope": auth_code.scope,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "jti": jti,
    }

    priv_key = get_private_key(app)
    access_token = pyjwt.encode(payload, priv_key, algorithm="RS256", headers={"kid": "oauth-signing-key"})

    wrapped_dek = None
    from app.shared.keys import get_user_key
    dek_hex = get_user_key(auth_code.customer_id)
    if dek_hex:
        from app.api.token_service import wrap_dek_with_token
        wrapped_dek = wrap_dek_with_token(dek_hex, access_token.encode())

    access_token_record = OAuthAccessToken(
        jti=jti,
        client_id=client_id,
        customer_id=auth_code.customer_id,
        scope=auth_code.scope,
        resource=auth_code.resource,
        expires_at=now + timedelta(seconds=expires_in),
        wrapped_dek=wrapped_dek,
    )
    db.session.add(access_token_record)

    auth_code.used = True
    db.session.commit()

    response_body: dict[str, Any] = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "scope": auth_code.scope,
    }

    scopes = auth_code.scope.split()
    if "openid" in scopes:
        user = CustomerAccount.query.filter_by(customer_id=auth_code.customer_id).first()
        id_token_claims: dict[str, Any] = {
            "iss": issuer,
            "sub": str(auth_code.customer_id),
            "aud": client_id,
            "exp": now + timedelta(seconds=expires_in),
            "iat": now,
            "jti": _generate_jti(),
        }
        if user:
            id_token_claims["email"] = user.email_address
        id_token = pyjwt.encode(id_token_claims, priv_key, algorithm="RS256", headers={"kid": "oauth-signing-key"})
        response_body["id_token"] = id_token

    return jsonify(response_body)


@oauth_bp.route("/oauth/register", methods=["POST"])
def register_client():
    data = request.get_json(silent=True) or {}
    client_name = data.get("client_name")
    redirect_uris = data.get("redirect_uris")

    if not client_name or not redirect_uris:
        return jsonify({"error": "invalid_client_metadata", "error_description": "client_name and redirect_uris are required"}), 400

    if isinstance(redirect_uris, str):
        redirect_uris = [redirect_uris]

    for uri in redirect_uris:
        if not _is_valid_redirect_uri(uri):
            return jsonify({"error": "invalid_redirect_uri", "error_description": f"redirect_uri not allowed: {uri}"}), 400

    client_id = secrets.token_urlsafe(32)
    client = OAuthClient(
        client_id=client_id,
        client_name=client_name,
        redirect_uris=json.dumps(redirect_uris),
        token_endpoint_auth_method="none",
    )
    db.session.add(client)
    db.session.commit()

    return jsonify(
        {
            "client_id": client_id,
            "client_name": client_name,
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
        }
    ), 201


@oauth_bp.route("/oauth/jwks.json")
def jwks():
    app = _current_app()
    return jsonify(get_jwks(app))


def _current_app() -> Flask:
    from flask import current_app

    return current_app._get_current_object()


def register(app: Flask):
    get_or_create_key_pair(app)
    app.register_blueprint(oauth_bp)
