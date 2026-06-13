from __future__ import annotations

import logging
from typing import Any, Optional

from flask import current_app

from app.admin.services.mail_server.http_client import MailApiClient

logger = logging.getLogger(__name__)


def get_mail_client() -> Optional[MailApiClient]:
    base_url = current_app.config.get("MAIL_API_URL", "")
    api_key = current_app.config.get("MAIL_API_KEY", "")
    if not base_url:
        return None
    return MailApiClient(base_url, api_key)


def get_mail_client_for_domain(domain: Any) -> Optional[MailApiClient]:
    if domain and getattr(domain, "mail_api_url", None):
        return MailApiClient(
            domain.mail_api_url,
            getattr(domain, "mail_api_key", "") or "",
        )
    return get_mail_client()
