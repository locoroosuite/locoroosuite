import logging
import threading

from flask import session, request, render_template, current_app

from app.shared.models.core import CustomerAccount
from app.shared.keys import get_user_key
from app.modules.mail.services.secrets import decrypt_with_key
from app.modules.mail.services.imap_client import (
    select_folder, list_folders, fetch_message, search_headers,
    search_full_text, safe_logout,
)
from app.modules.mail.services.cache_db import open_cache, search_local
from app.shared.events import push_event
from app.shared.auth import require_customer

from app.modules.mail.controllers.helpers import (
    mail_bp,
    _get_or_create_settings,
    _imap_for_account,
    _format_short_date,
    _message_date_ts,
)

from app.modules.mail.utils.sanitize import (
    decode_address_header, normalize_header_text, normalize_preview_text,
)


logger = logging.getLogger(__name__)


@mail_bp.route("/mail/search", methods=["POST"])
@require_customer
def search():
    query = request.form.get("q", "")
    folder_scope = request.form.get("folder")
    account_id = int(request.form.get("account_id"))
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    conn = open_cache(account.cache_db_path, key)
    results = search_local(conn, query)
    user_id = session.get("user_id")
    settings = _get_or_create_settings(user_id)
    timezone_name = settings.timezone
    readable_results = []
    for row in results:
        flags = row[7] or ""
        readable_results.append({
            "id": row[0],
            "subject": normalize_header_text(row[3]) or "(no subject)",
            "sender": decode_address_header(row[4]),
            "snippet": normalize_preview_text(row[12], limit=500, fallback=row[8] if len(row) > 8 else None),
            "date_display": _format_short_date(row[6], timezone_name),
            "folder": row[2] if len(row) > 2 else "",
            "is_unread": "\\Seen" not in flags,
            "is_flagged": "\\Flagged" in flags,
        })

    if not (query or "").strip():
        return render_template("search.html", results=[], query=query, account_id=account_id)

    app = current_app._get_current_object()

    def _expand():
        with app.app_context():
            secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
            client, _domain = _imap_for_account(account, secret)
            results_remote = []
            folders = [folder_scope] if folder_scope else list_folders(client)
            for folder in folders:
                select_folder(client, folder)
                uids = search_headers(client, query)
                for uid in uids:
                    msg = fetch_message(client, uid)
                    if not msg:
                        continue
                    results_remote.append({
                        "folder": folder,
                        "subject": normalize_header_text(msg.get("Subject", "")) or "(no subject)",
                        "from": decode_address_header(msg.get("From", "")),
                        "date": msg.get("Date", ""),
                        "date_display": _format_short_date(msg.get("Date", ""), timezone_name),
                    })
            safe_logout(client)
            push_event(user_id, "search_results", {"query": query, "results": results_remote})

    threading.Thread(target=_expand, daemon=True).start()
    return render_template("search.html", results=readable_results, query=query, account_id=account_id)


@mail_bp.route("/mail/search/full", methods=["POST"])
@require_customer
def full_search():
    query = request.form.get("q", "")
    user_id = session.get("user_id")
    account_id = int(request.form.get("account_id"))
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first_or_404()
    if not (query or "").strip():
        return render_template("search_full.html", results=[], query=query, account_id=account_id)
    settings = _get_or_create_settings(user_id)
    key = get_user_key(user_id)
    conn = open_cache(account.cache_db_path, key)
    uid_folder_pairs = {}
    results_remote = []
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    for folder in list_folders(client):
        select_folder(client, folder)
        uids = search_full_text(client, query)
        for uid in uids:
            msg = fetch_message(client, uid)
            if not msg:
                continue
            uid_folder_pairs[len(results_remote)] = (str(uid), folder)
            results_remote.append({
                "folder": folder,
                "subject": normalize_header_text(msg.get("Subject", "")) or "(no subject)",
                "from": decode_address_header(msg.get("From", "")),
                "date": msg.get("Date", ""),
                "date_display": _format_short_date(msg.get("Date", ""), settings.timezone),
                "date_ts": _message_date_ts(msg.get("Date", "")),
            })
    safe_logout(client)

    cache_lookup = {}
    for idx, (uid_val, folder_name) in uid_folder_pairs.items():
        row = conn.execute(
            "SELECT id, flags FROM messages WHERE uid = ? AND folder = ?",
            (uid_val, folder_name),
        ).fetchone()
        if row:
            cache_lookup[idx] = {"id": row[0], "flags": row[1] or ""}

    for idx, item in enumerate(results_remote):
        cached = cache_lookup.get(idx)
        if cached:
            item["message_id"] = cached["id"]
            item["is_unread"] = "\\Seen" not in cached["flags"]
            item["is_flagged"] = "\\Flagged" in cached["flags"]
        else:
            item["message_id"] = None
            item["is_unread"] = False
            item["is_flagged"] = False

    results_remote.sort(key=lambda row: (row.get("date_ts") or 0), reverse=True)
    return render_template("search_full.html", results=results_remote, query=query, account_id=account_id)
