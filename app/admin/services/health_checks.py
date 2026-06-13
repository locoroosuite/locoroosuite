from __future__ import annotations

import logging
import socket
from typing import Any
from urllib.parse import urlparse

import requests
from flask import current_app

logger = logging.getLogger(__name__)

TIMEOUT = 3

SERVICE_LABELS: dict[str, str] = {
    "imap": "Email (IMAP)",
    "smtp": "Email (SMTP)",
    "carddav": "Contacts (CardDAV)",
    "caldav": "Calendar (CalDAV)",
    "collabora": "Docs (Collabora)",
    "mail_api": "Mail API",
}


def _tcp_check(host: str, port: int, timeout: int = TIMEOUT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _collabora_check(url: str, timeout: int = TIMEOUT) -> bool:
    try:
        r = requests.get(f"{url}/hosting/discovery", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _check_imap(domain) -> str:
    if not domain.imap_host:
        return "not_configured"
    return "connected" if _tcp_check(domain.imap_host, domain.imap_port or 993) else "misconfigured"


def _check_smtp(domain) -> str:
    if not domain.smtp_host:
        return "not_configured"
    return "connected" if _tcp_check(domain.smtp_host, domain.smtp_port or 587) else "misconfigured"


def _check_carddav(domain) -> str:
    if not domain.carddav_host:
        return "not_configured"
    return "connected" if _tcp_check(domain.carddav_host, domain.carddav_port or 5232) else "misconfigured"


def _check_caldav(domain) -> str:
    if not domain.caldav_host:
        return "not_configured"
    return "connected" if _tcp_check(domain.caldav_host, domain.caldav_port or 5232) else "misconfigured"


def _check_collabora(domain) -> str:
    url = current_app.config.get("COLLABORA_INTERNAL_URL", "")
    if not url:
        url = current_app.config.get("COLLABORA_URL", "")
    if not url:
        return "not_configured"
    return "connected" if _collabora_check(url) else "misconfigured"


def _check_mail_api(domain) -> str:
    if not getattr(domain, "mail_api_url", None):
        return "not_configured"
    from app.admin.services.mail_server import get_mail_client_for_domain
    client = get_mail_client_for_domain(domain)
    if client is None:
        return "not_configured"
    return "connected" if client.is_available() else "misconfigured"


_CHECKS: list[tuple[str, Any]] = [
    ("imap", _check_imap),
    ("smtp", _check_smtp),
    ("carddav", _check_carddav),
    ("caldav", _check_caldav),
    ("collabora", _check_collabora),
    ("mail_api", _check_mail_api),
]


def check_domain_services(domain) -> dict[str, str]:
    results: dict[str, str] = {}
    for key, fn in _CHECKS:
        try:
            results[key] = fn(domain)
        except Exception:
            logger.warning("Health check failed for %s on domain %s", key, domain.name, exc_info=True)
            results[key] = "misconfigured"
    return results
