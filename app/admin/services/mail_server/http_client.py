from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class MailApiClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = f"{self.base_url}{path}"
        try:
            resp = requests.request(
                method, url, headers=self._headers(), timeout=self.timeout, **kwargs
            )
            return resp
        except requests.ConnectionError as exc:
            logger.error("mail-api connection failed: %s %s -> %s", method, url, exc)
            raise
        except requests.Timeout as exc:
            logger.error("mail-api timeout: %s %s -> %s", method, url, exc)
            raise

    def add_domain(self, domain: str) -> dict[str, Any]:
        resp = self._request("POST", "/api/domains", json={"domain": domain})
        resp.raise_for_status()
        return resp.json()

    def remove_domain(self, domain: str) -> dict[str, Any]:
        resp = self._request("DELETE", f"/api/domains/{domain}")
        resp.raise_for_status()
        return resp.json()

    def add_user(self, email: str, password: str, quota_bytes: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"email": email, "password": password}
        if quota_bytes is not None:
            payload["quota_bytes"] = quota_bytes
        resp = self._request("POST", "/api/users", json=payload)
        resp.raise_for_status()
        return resp.json()

    def remove_user(self, email: str) -> dict[str, Any]:
        resp = self._request("DELETE", f"/api/users/{email}")
        resp.raise_for_status()
        return resp.json()

    def set_password(self, email: str, password: str) -> dict[str, Any]:
        resp = self._request("PUT", f"/api/users/{email}/password", json={"password": password})
        resp.raise_for_status()
        return resp.json()

    def set_quota(self, email: str, quota_bytes: int) -> dict[str, Any]:
        resp = self._request("PUT", f"/api/users/{email}/quota", json={"quota_bytes": quota_bytes})
        resp.raise_for_status()
        return resp.json()

    def set_sending_limit(self, email: str, max_per_day: int) -> dict[str, Any]:
        resp = self._request("POST", f"/api/users/{email}/sending-limit", json={"max_per_day": max_per_day})
        resp.raise_for_status()
        return resp.json()

    def delete_sending_limit(self, email: str) -> dict[str, Any]:
        resp = self._request("DELETE", f"/api/users/{email}/sending-limit")
        resp.raise_for_status()
        return resp.json()

    def check_user(self, email: str) -> bool:
        try:
            resp = self._request("GET", f"/api/users/{email}/check")
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def list_users(self, domain: str = "") -> list[dict[str, Any]]:
        params = {}
        if domain:
            params["domain"] = domain
        resp = self._request("GET", "/api/users", params=params)
        resp.raise_for_status()
        return resp.json().get("data", [])

    def is_available(self) -> bool:
        try:
            resp = self._request("GET", "/health")
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def generate_dkim_key(self, domain: str, selector: str | None = None) -> dict[str, Any]:
        json_body: dict[str, Any] = {}
        if selector is not None:
            json_body["selector"] = selector
        resp = self._request("POST", f"/api/dkim/{domain}", json=json_body)
        resp.raise_for_status()
        return resp.json().get("dkim", {})

    def get_dkim_key(self, domain: str, selector: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {}
        if selector is not None:
            params["selector"] = selector
        resp = self._request("GET", f"/api/dkim/{domain}", params=params)
        resp.raise_for_status()
        return resp.json().get("dkim", {})

    def remove_dkim_key(self, domain: str, selector: str | None = None) -> dict[str, Any]:
        json_body: dict[str, Any] = {}
        if selector is not None:
            json_body["selector"] = selector
        resp = self._request("DELETE", f"/api/dkim/{domain}", json=json_body)
        resp.raise_for_status()
        return resp.json()
