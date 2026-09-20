"""Thin sync HTTP client for the Matrix client-server API (Synapse).

Follows the hand-rolled client pattern of calendar/services/caldav.py:
sync ``requests`` calls, structured errors, no async.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time

import requests

logger = logging.getLogger(__name__)

CLIENT_API = "/_matrix/client/v3"
MEDIA_API = "/_matrix/media/v3"
MEDIA_DOWNLOAD_API = "/_matrix/client/v1/media"
SYNAPSE_ADMIN_API = "/_synapse/admin/v1"

DEFAULT_TIMEOUT = 30
MEDIA_TIMEOUT = 120

_LOCALPART_RE = re.compile(r"[^a-z0-9._=\-+/]")


class MatrixError(Exception):
    """Structured Matrix client error mapped to HTTP-ish codes."""

    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def homeserver_url(domain) -> str:
    if not getattr(domain, "matrix_host", None):
        raise MatrixError(
            "MATRIX_NOT_CONFIGURED",
            "Chat is not configured for this domain yet. An administrator must set the "
            "Matrix host, port and registration shared secret under Admin → Domains → "
            "contacts, calendar & chat settings.",
        )
    scheme = "https" if domain.matrix_use_tls else "http"
    port = domain.matrix_port or 8008
    return f"{scheme}://{domain.matrix_host}:{port}"


def normalize_localpart(email: str, username: str | None = None) -> str:
    source = username or email.split("@", 1)[0]
    localpart = source.strip().lower()
    localpart = _LOCALPART_RE.sub("-", localpart).strip(".-_")
    return localpart or f"user{secrets.token_hex(4)}"


def generate_device_password() -> str:
    return secrets.token_urlsafe(24)


class MatrixClient:
    def __init__(self, base_url: str, access_token: str | None = None, user_id: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.user_id = user_id

    # --- low level -----------------------------------------------------

    def _headers(self, extra: dict | None = None, set_content_type: bool = True) -> dict:
        headers: dict = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        if set_content_type:
            headers["Content-Type"] = "application/json"
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        api: str = CLIENT_API,
        params: dict | None = None,
        json_body: dict | None = None,
        data: bytes | None = None,
        headers: dict | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> dict:
        url = f"{self.base_url}{api}{path}"
        try:
            resp = requests.request(
                method,
                url,
                params=params,
                json=json_body if data is None else None,
                data=data,
                headers=self._headers(headers, set_content_type=(data is None)),
                timeout=timeout,
            )
        except requests.RequestException as exc:
            logger.warning("matrix request failed method=%s url=%s error=%s", method, url, exc)
            raise MatrixError("MATRIX_UNREACHABLE", f"Cannot reach the chat server: {exc}") from exc

        if resp.status_code >= 400:
            errcode = "M_UNKNOWN"
            message = f"Chat server error (HTTP {resp.status_code})"
            try:
                payload = resp.json()
                errcode = payload.get("errcode", errcode)
                message = payload.get("error", message)
            except ValueError:
                logger.warning(
                    "matrix error response not json url=%s status=%s", url, resp.status_code
                )
            logger.warning(
                "matrix error url=%s status=%s errcode=%s message=%s",
                url,
                resp.status_code,
                errcode,
                message,
            )
            if resp.status_code == 503 and "introspect" in message.lower():
                # Synapse delegates token introspection to the Matrix
                # Authentication Service (MAS). When MAS is down, every
                # authenticated request fails with 503 while unauthenticated
                # endpoints stay healthy.
                raise MatrixError(
                    "MATRIX_AUTH_BACKEND_DOWN",
                    "The chat server's authentication backend is down; contact the "
                    "homeserver operator. Until it is restored, no one can use chat.",
                    status=503,
                )
            raise MatrixError(errcode, message, status=resp.status_code)
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise MatrixError(
                "MATRIX_BAD_RESPONSE", f"Chat server returned invalid JSON for {path}"
            ) from exc

    # --- account -------------------------------------------------------

    def admin_register(self, username: str, password: str, shared_secret: str) -> dict:
        if not shared_secret:
            raise MatrixError(
                "MATRIX_NOT_CONFIGURED",
                "No Matrix registration shared secret configured for this domain",
            )
        nonce_resp = self._request("GET", "/register", api=SYNAPSE_ADMIN_API)
        nonce = nonce_resp.get("nonce")
        if not nonce:
            raise MatrixError(
                "MATRIX_BAD_RESPONSE", "Chat server register endpoint returned no nonce"
            )
        mac = hmac.new(
            shared_secret.encode(),
            f"{nonce}\0{username}\0{password}\0notadmin".encode(),
            hashlib.sha1,
        ).hexdigest()
        return self._request(
            "POST",
            "/register",
            api=SYNAPSE_ADMIN_API,
            json_body={
                "nonce": nonce,
                "username": username,
                "password": password,
                "admin": False,
                "mac": mac,
            },
        )

    def login(self, username: str, password: str, device_name: str = "LocoRooSuite") -> dict:
        return self._request(
            "POST",
            "/login",
            json_body={
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": username},
                "password": password,
                "initial_device_display_name": device_name,
            },
        )

    def whoami(self, timeout: float = DEFAULT_TIMEOUT) -> dict:
        return self._request("GET", "/account/whoami", timeout=timeout)

    def get_displayname(self, user_id: str) -> str | None:
        try:
            resp = self._request("GET", f"/profile/{user_id}/displayname")
        except MatrixError:
            return None
        return resp.get("displayname")

    def set_displayname(self, user_id: str, displayname: str) -> dict:
        return self._request(
            "PUT",
            f"/profile/{user_id}/displayname",
            json_body={"displayname": displayname},
        )

    # --- sync ----------------------------------------------------------

    def sync(self, since: str | None = None, timeout_ms: int = 25000, limit: int = 100) -> dict:
        params: dict = {"timeout": timeout_ms, "limit": limit}
        if since:
            params["since"] = since
        return self._request(
            "GET", "/sync", params=params, timeout=(timeout_ms / 1000) + DEFAULT_TIMEOUT
        )

    def room_messages(
        self, room_id: str, from_token: str | None, direction: str = "b", limit: int = 50
    ) -> dict:
        params: dict = {"dir": direction, "limit": limit}
        if from_token:
            params["from"] = from_token
        return self._request("GET", f"/rooms/{room_id}/messages", params=params)

    # --- rooms ---------------------------------------------------------

    def create_room(
        self,
        *,
        name: str | None = None,
        topic: str | None = None,
        is_public: bool = False,
        is_direct: bool = False,
        invite: list[str] | None = None,
    ) -> dict:
        body: dict = {
            "preset": "public_chat" if is_public else "private_chat",
            "visibility": "public" if is_public else "private",
        }
        if name:
            body["name"] = name
        if topic:
            body["topic"] = topic
        if invite:
            body["invite"] = invite
        if is_direct:
            body["is_direct"] = True
        return self._request("POST", "/createRoom", json_body=body)

    def join_room(self, room_id_or_alias: str) -> dict:
        return self._request("POST", f"/join/{room_id_or_alias}")

    def leave_room(self, room_id: str) -> dict:
        return self._request("POST", f"/rooms/{room_id}/leave", json_body={})

    def forget_room(self, room_id: str) -> dict:
        return self._request("POST", f"/rooms/{room_id}/forget", json_body={})

    def invite(self, room_id: str, user_id: str) -> dict:
        return self._request("POST", f"/rooms/{room_id}/invite", json_body={"user_id": user_id})

    def joined_members(self, room_id: str) -> dict:
        return self._request("GET", f"/rooms/{room_id}/joined_members")

    def set_room_name(self, room_id: str, name: str) -> dict:
        return self._request("PUT", f"/rooms/{room_id}/state/m.room.name", json_body={"name": name})

    def set_room_topic(self, room_id: str, topic: str) -> dict:
        return self._request(
            "PUT", f"/rooms/{room_id}/state/m.room.topic", json_body={"topic": topic}
        )

    def user_directory_search(self, term: str, limit: int = 10) -> dict:
        return self._request(
            "POST", "/user_directory/search", json_body={"search_term": term, "limit": limit}
        )

    # --- events --------------------------------------------------------

    def _txn_id(self, prefix: str) -> str:
        return f"{prefix}{time.time_ns()}"

    def send_event(
        self, room_id: str, event_type: str, content: dict, txn_prefix: str = "lr"
    ) -> dict:
        return self._request(
            "PUT",
            f"/rooms/{room_id}/send/{event_type}/{self._txn_id(txn_prefix)}",
            json_body=content,
        )

    def send_message(
        self, room_id: str, body: str, formatted_body: str | None = None, msgtype: str = "m.text"
    ) -> dict:
        content: dict = {"msgtype": msgtype, "body": body}
        if formatted_body:
            content["format"] = "org.matrix.custom.html"
            content["formatted_body"] = formatted_body
        return self.send_event(room_id, "m.room.message", content)

    def send_reaction(self, room_id: str, target_event_id: str, key: str) -> dict:
        content = {
            "m.relates_to": {"rel_type": "m.annotation", "event_id": target_event_id, "key": key}
        }
        return self.send_event(room_id, "m.reaction", content)

    def send_edit(self, room_id: str, target_event_id: str, new_body: str) -> dict:
        content = {
            "msgtype": "m.text",
            "body": f"* {new_body}",
            "m.new_content": {"msgtype": "m.text", "body": new_body},
            "m.relates_to": {"rel_type": "m.replace", "event_id": target_event_id},
        }
        return self.send_event(room_id, "m.room.message", content)

    def redact(self, room_id: str, event_id: str, reason: str | None = None) -> dict:
        body: dict = {}
        if reason:
            body["reason"] = reason
        return self._request(
            "PUT", f"/rooms/{room_id}/redact/{event_id}/{self._txn_id('lrr')}", json_body=body
        )

    def send_receipt(self, room_id: str, event_id: str) -> dict:
        return self._request("POST", f"/rooms/{room_id}/receipt/m.read/{event_id}", json_body={})

    def set_read_marker(self, room_id: str, fully_read_event_id: str) -> dict:
        return self._request(
            "POST",
            f"/rooms/{room_id}/read_markers",
            json_body={"m.fully_read": fully_read_event_id, "m.read": fully_read_event_id},
        )

    def typing(self, room_id: str, user_id: str, is_typing: bool, timeout_ms: int = 20000) -> dict:
        return self._request(
            "PUT",
            f"/rooms/{room_id}/typing/{user_id}",
            json_body={"typing": is_typing, "timeout": timeout_ms},
        )

    # --- media ---------------------------------------------------------

    def upload_media(self, content: bytes, filename: str, content_type: str) -> dict:
        params = {"filename": filename}
        return self._request(
            "POST",
            "/upload",
            api=MEDIA_API,
            params=params,
            data=content,
            headers={"Content-Type": content_type},
            timeout=MEDIA_TIMEOUT,
        )

    def download_media(self, server_name: str, media_id: str) -> requests.Response:
        url = f"{self.base_url}{MEDIA_DOWNLOAD_API}/download/{server_name}/{media_id}"
        try:
            resp = requests.get(
                url,
                headers=self._headers(set_content_type=False),
                timeout=MEDIA_TIMEOUT,
                stream=True,
            )
        except requests.RequestException as exc:
            logger.warning("matrix media download failed url=%s error=%s", url, exc)
            raise MatrixError("MATRIX_UNREACHABLE", f"Cannot reach the chat server: {exc}") from exc
        if resp.status_code >= 400:
            logger.warning("matrix media download error url=%s status=%s", url, resp.status_code)
            raise MatrixError(
                "M_MEDIA_UNAVAILABLE",
                f"Media not available (HTTP {resp.status_code})",
                resp.status_code,
            )
        return resp

    def thumbnail_media(
        self, server_name: str, media_id: str, width: int, height: int
    ) -> requests.Response:
        url = f"{self.base_url}{MEDIA_DOWNLOAD_API}/thumbnail/{server_name}/{media_id}"
        try:
            resp = requests.get(
                url,
                params={"width": width, "height": height, "method": "crop"},
                headers=self._headers(set_content_type=False),
                timeout=DEFAULT_TIMEOUT,
                stream=True,
            )
        except requests.RequestException as exc:
            logger.warning("matrix thumbnail failed url=%s error=%s", url, exc)
            raise MatrixError("MATRIX_UNREACHABLE", f"Cannot reach the chat server: {exc}") from exc
        if resp.status_code >= 400:
            logger.warning("matrix thumbnail error url=%s status=%s", url, resp.status_code)
            raise MatrixError(
                "M_MEDIA_UNAVAILABLE",
                f"Thumbnail not available (HTTP {resp.status_code})",
                resp.status_code,
            )
        return resp
