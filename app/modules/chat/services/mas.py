"""MAS (Matrix Authentication Service) client: admin API + compat login.

The chat module authenticates and provisions exclusively through MAS
(HLD U25.4/U25.6): with delegated authentication, Synapse disables its own
shared-secret registration and password-login endpoints, so accounts are
created/managed via the MAS Admin API and logins go through the MAS
compatibility layer (``POST /_matrix/client/v3/login``).

Admin API auth follows the "automated tools" model from the MAS docs: the
domain stores an OAuth client (id + secret) registered in MAS ``clients`` and
``policy.data.admin_clients``; the client fetches short-lived access tokens
via the client-credentials grant with the ``urn:mas:admin`` scope.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from app.modules.chat.services.matrix import MatrixError

logger = logging.getLogger(__name__)

ADMIN_API = "/api/admin/v1"
TOKEN_PATH = "/oauth2/token"
COMPAT_LOGIN_PATH = "/_matrix/client/v3/login"
ADMIN_SCOPE = "urn:mas:admin"

DEFAULT_TIMEOUT = 30

# Refresh admin tokens this many seconds before expiry.
_TOKEN_REFRESH_MARGIN = 60
# Fallback token lifetime when the server does not send expires_in.
_TOKEN_DEFAULT_TTL = 300


class MasClient:
    """Sync HTTP client for one MAS instance (per-domain configuration)."""

    def __init__(self, base_url: str, client_id: str, client_secret: str):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self._admin_token: str | None = None
        self._admin_token_expires_at: float = 0.0

    # --- low level -----------------------------------------------------

    def _error_from_response(self, resp: requests.Response, url: str) -> MatrixError:
        message = f"Authentication service error (HTTP {resp.status_code})"
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                message = str(errors[0].get("title") or message)
            elif payload.get("error"):
                message = str(payload["error"])
            elif payload.get("errcode"):
                message = f"{payload['errcode']}: {payload.get('error', '')}".rstrip(": ")
        logger.warning("mas error url=%s status=%s message=%s", url, resp.status_code, message)
        return MatrixError("MAS_ERROR", message, status=resp.status_code)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        data: dict | None = None,
        headers: dict | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = requests.request(
                method,
                url,
                params=params,
                json=json_body,
                data=data,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            logger.warning("mas request failed method=%s url=%s error=%s", method, url, exc)
            raise MatrixError(
                "MATRIX_MAS_UNREACHABLE",
                f"Cannot reach the chat authentication service: {exc}",
            ) from exc
        if resp.status_code >= 400:
            raise self._error_from_response(resp, url)
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise MatrixError(
                "MATRIX_MAS_BAD_RESPONSE",
                f"Authentication service returned invalid JSON for {path}",
            ) from exc

    # --- admin auth ----------------------------------------------------

    def fetch_admin_token(self) -> str:
        """Fetch (and cache) an admin access token via client credentials."""
        try:
            resp = requests.post(
                f"{self.base_url}{TOKEN_PATH}",
                auth=(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials", "scope": ADMIN_SCOPE},
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            logger.warning("mas token request failed error=%s", exc)
            raise MatrixError(
                "MATRIX_MAS_UNREACHABLE",
                f"Cannot reach the chat authentication service: {exc}",
            ) from exc
        if resp.status_code in (400, 401, 403):
            logger.warning("mas token request rejected status=%s", resp.status_code)
            raise MatrixError(
                "MATRIX_MAS_AUTH_FAILED",
                "The chat authentication service rejected the configured OAuth client. "
                "An administrator must check the MAS client id/secret and that the client "
                "is listed in policy.data.admin_clients.",
            )
        if resp.status_code >= 400:
            raise self._error_from_response(resp, f"{self.base_url}{TOKEN_PATH}")
        try:
            payload = resp.json()
            token = payload["access_token"]
        except (ValueError, KeyError) as exc:
            raise MatrixError(
                "MATRIX_MAS_BAD_RESPONSE", "Authentication service returned no access token"
            ) from exc
        expires_in = payload.get("expires_in") or _TOKEN_DEFAULT_TTL
        self._admin_token = str(token)
        self._admin_token_expires_at = time.time() + max(expires_in - _TOKEN_REFRESH_MARGIN, 30)
        return self._admin_token

    def _admin_request(self, method: str, path: str, **kwargs: Any) -> dict:
        for attempt in (1, 2):
            if not self._admin_token or time.time() >= self._admin_token_expires_at:
                self.fetch_admin_token()
            assert self._admin_token is not None
            headers = dict(kwargs.pop("headers", {}) or {})
            headers["Authorization"] = f"Bearer {self._admin_token}"
            url = f"{self.base_url}{path}"
            try:
                resp = requests.request(
                    method, url, headers=headers, timeout=DEFAULT_TIMEOUT, **kwargs
                )
            except requests.RequestException as exc:
                logger.warning(
                    "mas admin request failed method=%s url=%s error=%s", method, url, exc
                )
                raise MatrixError(
                    "MATRIX_MAS_UNREACHABLE",
                    f"Cannot reach the chat authentication service: {exc}",
                ) from exc
            if resp.status_code == 401 and attempt == 1:
                # Token expired or revoked server-side: refetch once.
                self._admin_token = None
                continue
            if resp.status_code >= 400:
                raise self._error_from_response(resp, url)
            if resp.status_code == 204 or not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError as exc:
                raise MatrixError(
                    "MATRIX_MAS_BAD_RESPONSE",
                    f"Authentication service returned invalid JSON for {path}",
                ) from exc
        raise MatrixError(
            "MATRIX_MAS_AUTH_FAILED", "Could not authenticate to the chat authentication service"
        )

    # --- users (admin) -------------------------------------------------

    @staticmethod
    def _user_resource(payload: dict) -> dict:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise MatrixError(
                "MATRIX_MAS_BAD_RESPONSE", "Authentication service returned a malformed user"
            )
        return data

    def _admin_api_disabled_error(self) -> MatrixError:
        return MatrixError(
            "MATRIX_MAS_ADMIN_API_DISABLED",
            "The chat authentication service does not expose its admin API. The "
            "homeserver operator must enable the 'adminapi' resource on a MAS listener.",
        )

    def get_user_by_username(self, username: str) -> dict | None:
        """Return the MAS user resource for a localpart, or None if unknown."""
        try:
            payload = self._admin_request("GET", f"{ADMIN_API}/users/by-username/{username}")
        except MatrixError as exc:
            if getattr(exc, "status", None) == 404:
                return None
            raise
        return self._user_resource(payload)

    def create_user(self, username: str, displayname: str | None = None) -> dict:
        """Create a MAS user (no password; use set_password afterwards)."""
        body: dict = {"username": username}
        if displayname:
            body["displayname"] = displayname
        try:
            payload = self._admin_request("POST", f"{ADMIN_API}/users", json=body)
        except MatrixError as exc:
            status = getattr(exc, "status", None)
            if status == 409:
                raise MatrixError(
                    "MAS_USER_EXISTS", f"The chat identity for {username} already exists"
                ) from exc
            if status == 404:
                # A collection endpoint has no other source of 404s.
                raise self._admin_api_disabled_error() from exc
            raise
        return self._user_resource(payload)

    def set_password(self, user_id: str, password: str) -> None:
        try:
            self._admin_request(
                "POST", f"{ADMIN_API}/users/{user_id}/set-password", json={"password": password}
            )
        except MatrixError as exc:
            if getattr(exc, "status", None) == 404:
                # The id comes from this client's own lookups; a 404 here is
                # the route being absent, not a stale id.
                raise self._admin_api_disabled_error() from exc
            raise

    # --- compat login ----------------------------------------------------

    def login(self, username: str, password: str, device_name: str = "LocoRooSuite") -> dict:
        """Matrix-compatible password login served by MAS.

        Returns the standard Matrix login payload (user_id, access_token,
        device_id). No admin token is involved.
        """
        return self._request(
            "POST",
            COMPAT_LOGIN_PATH,
            json_body={
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": username},
                "password": password,
                "initial_device_display_name": device_name,
            },
        )


def mas_client_for_domain(domain) -> MasClient:
    """Build a MasClient from the domain config, failing fast on gaps."""
    mas_url = getattr(domain, "matrix_mas_url", None)
    client_id = getattr(domain, "matrix_mas_client_id", None)
    client_secret = getattr(domain, "matrix_mas_client_secret", None)
    if not (getattr(domain, "matrix_host", None) and mas_url and client_id and client_secret):
        raise MatrixError(
            "MATRIX_NOT_CONFIGURED",
            "Chat is not configured for this domain yet. An administrator must set the "
            "Matrix homeserver and authentication service (MAS) under Admin → Domains → "
            "contacts, calendar & chat settings.",
        )
    return MasClient(str(mas_url), str(client_id), str(client_secret))
