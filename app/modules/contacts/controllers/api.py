import logging

from flask import jsonify, session, request

from app.shared.auth import require_customer
from app.shared.email_validation import is_valid_email
from app.modules.contacts.controllers.helpers import (
    contacts_bp,
    _get_account,
    _get_carddav_config,
    _get_credentials,
    _open_cache_for_account,
    _carddav_base_url,
)
from app.modules.contacts.services import carddav, cache_db
from app.modules.contacts.services.vcard import generate_vcard, extract_uid

logger = logging.getLogger(__name__)


@contacts_bp.route("/contacts/api/search")
@require_customer
def api_search():
    q = request.args.get("q", "").strip()
    if not q or len(q) < 2:
        return jsonify([])

    user_id = session.get("user_id")
    account_id = session.get("active_account_id")
    if not account_id:
        return jsonify([])

    account = _get_account(account_id, user_id)
    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify([])

    try:
        results = cache_db.search_contacts_api(conn, q)
        return jsonify(results)
    except Exception:
        logger.exception("contacts api search failed")
        return jsonify([])
    finally:
        conn.close()


@contacts_bp.route("/contacts/api/auto-save", methods=["POST"])
@require_customer
def api_auto_save():
    data = request.get_json(silent=True) or {}
    recipients = data.get("recipients", [])
    account_id = data.get("account_id") or session.get("active_account_id")

    if not recipients or not isinstance(recipients, list) or not account_id:
        return jsonify({"saved": 0, "skipped": 0, "failed": 0})

    user_id = session.get("user_id")
    account = _get_account(account_id, user_id)
    config = _get_carddav_config(account)
    if not config:
        return jsonify({"saved": 0, "skipped": 0, "failed": 0})

    conn = _open_cache_for_account(account)
    if not conn:
        return jsonify({"saved": 0, "skipped": 0, "failed": 0})

    saved = 0
    skipped = 0
    failed = 0

    try:
        password = _get_credentials(account)
        if not password:
            return jsonify({"saved": 0, "skipped": 0, "failed": 0})

        dav_session = None
        abook_url = None
        try:
            dav_session, abook_url, _ = carddav.discover_address_book(
                _carddav_base_url(config), account.username, password
            )
            if not abook_url:
                abook_url = carddav.create_address_book(
                    dav_session, _carddav_base_url(config), account.username
                )
        except Exception:
            logger.exception("auto-save: CardDAV discovery failed")
            dav_session = None

        for recipient in recipients:
            email = (recipient.get("email") or "").strip()
            name = (recipient.get("name") or "").strip()
            if not email:
                continue

            if not is_valid_email(email):
                logger.warning("auto-save: skipping invalid email %r", email)
                skipped += 1
                continue

            if cache_db.email_exists(conn, email):
                skipped += 1
                continue

            if not dav_session or not abook_url:
                failed += 1
                continue

            fn = name or email.split("@")[0]
            vcard_text = generate_vcard({"fn": fn, "email_work": email})
            uid = extract_uid(vcard_text)

            try:
                href, etag = carddav.create_contact(
                    dav_session, abook_url, vcard_text, uid=uid
                )
                cache_db.upsert_contact(conn, uid, href, etag, vcard_text)
                saved += 1
            except Exception:
                logger.exception("auto-save: failed for %s", email)
                failed += 1
    finally:
        conn.close()

    return jsonify({"saved": saved, "skipped": skipped, "failed": failed})
