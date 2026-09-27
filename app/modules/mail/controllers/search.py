import logging
import threading

from flask import current_app, jsonify, redirect, render_template, request, session, url_for
from flask_babel import _

from app.modules.mail.controllers.helpers import (
    _format_short_date,
    _get_or_create_settings,
    _imap_for_account,
    _message_date_ts,
    _parse_flags,
    mail_bp,
)
from app.modules.mail.services.cache_db import list_cached_folders, open_cache, search_messages
from app.modules.mail.services.imap_client import (
    fetch_message,
    list_folders,
    move_message,
    safe_logout,
    search_full_text,
    search_headers,
    select_folder,
    set_flag,
)
from app.modules.mail.services.search_query import parse_search_query, quote_token
from app.modules.mail.services.secrets import decrypt_with_key
from app.modules.mail.utils.sanitize import (
    decode_address_header,
    normalize_header_text,
    normalize_preview_text,
)
from app.shared.auth import require_customer
from app.shared.events import push_event
from app.shared.keys import get_user_key
from app.shared.models.core import CustomerAccount

logger = logging.getLogger(__name__)

_APPLY_ACTIONS = {"mark_read", "mark_unread", "flag", "delete", "move"}


def _advanced_field_tokens(values):
    """Build operator tokens from the advanced-search panel fields (U7.5)."""
    tokens = []
    for key, field in (
        ("from", "f_from"),
        ("to", "f_to"),
        ("subject", "f_subject"),
        ("folder", "f_folder"),
        ("filename", "f_filename"),
    ):
        value = (values.get(field) or "").strip().strip('"')
        if value:
            tokens.append(f"{key}:{quote_token(value)}")
    for key, field in (("after", "f_after"), ("before", "f_before")):
        value = (values.get(field) or "").strip()
        if value:
            tokens.append(f"{key}:{value}")
    if values.get("f_attachment"):
        tokens.append("has:attachment")
    if values.get("f_unread"):
        tokens.append("is:unread")
    if values.get("f_starred"):
        tokens.append("is:starred")
    return tokens


def _decorate_row(row, timezone_name):
    flags = row["flags"] or ""
    return {
        "id": row["id"],
        "subject": normalize_header_text(row["subject"]) or _("(no subject)"),
        "sender": decode_address_header(row["sender"]),
        "snippet": normalize_preview_text(row["snippet"], limit=500, fallback=row["body"]),
        "date_display": _format_short_date(row["date"], timezone_name),
        "folder": row["folder"],
        "is_unread": "\\Seen" not in flags,
        "is_flagged": "\\Flagged" in flags,
    }


def _known_cache_folders(conn):
    return [
        row["folder"]
        for row in conn.execute(
            "SELECT DISTINCT folder FROM messages WHERE folder IS NOT NULL ORDER BY folder"
        ).fetchall()
    ]


@mail_bp.route("/mail/search/parse")
@require_customer
def search_parse():
    """Parse a raw query into advanced-search panel field values (U7.5).

    Pure parsing — the single grammar source is ``parse_search_query``; the
    panel JS fills its fields from this response so the Python grammar is
    never duplicated client-side.
    """
    query = (request.args.get("q") or "").strip()
    account_id = int(request.args.get("account_id") or 0)
    CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    filters = parse_search_query(query)
    return jsonify(
        {
            "query": filters.to_query(),
            "fields": filters.to_form_fields(),
            "chips": filters.chips(),
        }
    )


@mail_bp.route("/mail/search/folders")
@require_customer
def search_folders():
    """Folder names for the advanced-search panel's folder select (U7.5)."""
    account_id = int(request.args.get("account_id") or 0)
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    key = get_user_key(session.get("user_id"))
    conn = open_cache(account.cache_db_path, key)
    return jsonify({"folders": [row["name"] for row in list_cached_folders(conn)]})


@mail_bp.route("/mail/search", methods=["GET", "POST"])
@require_customer
def search():
    raw_query = (request.values.get("q") or "").strip()
    account_id = int(request.values.get("account_id") or 0)
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    user_id = session.get("user_id")
    key = get_user_key(user_id)

    filters = parse_search_query(raw_query)
    advanced = _advanced_field_tokens(request.values)
    if advanced:
        filters = parse_search_query(" ".join([raw_query, *advanced]))
    query = filters.to_query() or raw_query

    conn = open_cache(account.cache_db_path, key)
    known_folders = _known_cache_folders(conn)
    settings = _get_or_create_settings(user_id)
    timezone_name = settings.timezone

    results, total = ([], 0)
    if not filters.is_empty:
        results, total = search_messages(conn, filters, limit=50)
    readable_results = [_decorate_row(row, timezone_name) for row in results]
    chips = [
        {
            **chip,
            "remove_url": url_for(
                "mail.search",
                account_id=account_id,
                q=filters.without(chip["key"], chip["value"]).to_query(),
            ),
        }
        for chip in filters.chips()
    ]

    if filters.is_empty:
        return render_template(
            "search.html",
            results=[],
            query=query,
            account_id=account_id,
            folders=known_folders,
            total=0,
            chips=[],
            search_prefill=query,
        )

    app = current_app._get_current_object()  # pyright: ignore[reportAttributeAccessIssue]
    expansion_terms = " ".join(filters.text_terms)
    expansion_folders = filters.folders

    def _expand():
        with app.app_context():
            if not expansion_terms:
                return
            try:
                secret = (
                    decrypt_with_key(account.encrypted_secret, key)
                    if account.encrypted_secret
                    else None
                )
                client, _domain = _imap_for_account(account, secret)
                results_remote = []
                folders = expansion_folders or list_folders(client)
                for folder in folders:
                    select_folder(client, folder)
                    uids = search_headers(client, expansion_terms)
                    for uid in uids:
                        msg = fetch_message(client, uid)
                        if not msg:
                            continue
                        results_remote.append(
                            {
                                "folder": folder,
                                "subject": normalize_header_text(msg.get("Subject", ""))
                                or _("(no subject)"),
                                "from": decode_address_header(msg.get("From", "")),
                                "date": msg.get("Date", ""),
                                "date_display": _format_short_date(
                                    msg.get("Date", ""), timezone_name
                                ),
                            }
                        )
                safe_logout(client)
                push_event(user_id, "search_results", {"query": query, "results": results_remote})
            except Exception:
                logger.warning(
                    "search expansion failed account_id=%s query=%r",
                    account.id,
                    query,
                    exc_info=True,
                )

    if expansion_terms:
        threading.Thread(target=_expand, daemon=True).start()
    return render_template(
        "search.html",
        results=readable_results,
        query=query,
        account_id=account_id,
        folders=known_folders,
        total=total,
        chips=chips,
        search_prefill=query,
    )


@mail_bp.route("/mail/search/apply", methods=["POST"])
@require_customer
def search_apply():
    """Apply a bulk action to every message matching a search (U5.6/U7.6).

    The query is re-parsed and re-executed server-side; the client never sends
    a message ID list for "all results". Delete/move respect protection rules.
    """
    from app.modules.mail.services.protection import protection_reason

    query = (request.form.get("q") or "").strip()
    action = request.form.get("action") or ""
    destination = (request.form.get("destination") or "").strip()
    is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    account_id = int(request.form.get("account_id") or 0)
    user_id = session.get("user_id")
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first_or_404()
    key = get_user_key(user_id)
    settings = _get_or_create_settings(user_id)

    def _fail(message, status=400):
        if is_xhr:
            return jsonify({"status": "error", "error": message}), status
        return redirect(url_for("mail.search", account_id=account_id, q=query))

    filters = parse_search_query(query)
    if action not in _APPLY_ACTIONS:
        return _fail(_("Invalid action."))
    if action == "move" and not destination:
        return _fail(_("Choose a destination folder."))
    if filters.is_empty:
        return _fail(_("Enter a search query first."))

    conn = open_cache(account.cache_db_path, key)
    rows, total = search_messages(conn, filters, limit=None)
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    applied = 0
    skipped = 0
    try:
        by_folder: dict[str, list] = {}
        for row in rows:
            by_folder.setdefault(row["folder"], []).append(row)
        for folder, items in by_folder.items():
            select_folder(client, folder)
            flag_updates = {}
            for row in items:
                flag_list = _parse_flags(row["flags"])
                targets_trash = action == "delete" or destination.lower() == "trash"
                if targets_trash and protection_reason(flag_list, settings):
                    skipped += 1
                    continue
                if action == "mark_read":
                    set_flag(client, row["uid"], "\\Seen", add=True)
                    if "\\Seen" not in flag_list:
                        flag_list.append("\\Seen")
                    flag_updates[row["uid"]] = flag_list
                elif action == "mark_unread":
                    set_flag(client, row["uid"], "\\Seen", add=False)
                    flag_updates[row["uid"]] = [f for f in flag_list if f != "\\Seen"]
                elif action == "flag":
                    set_flag(client, row["uid"], "\\Flagged", add=True)
                    if "\\Flagged" not in flag_list:
                        flag_list.append("\\Flagged")
                    flag_updates[row["uid"]] = flag_list
                elif action == "delete":
                    move_message(client, row["uid"], "Trash")
                elif action == "move":
                    move_message(client, row["uid"], destination)
                applied += 1
            if flag_updates:
                from app.modules.mail.services.cache_db import update_flags_bulk

                update_flags_bulk(conn, folder, flag_updates)
            client.expunge()
    finally:
        safe_logout(client)
    logger.info(
        "search apply user_id=%s account_id=%s action=%s query=%r applied=%s skipped=%s total=%s",
        user_id,
        account.id,
        action,
        query,
        applied,
        skipped,
        total,
    )
    if is_xhr:
        return jsonify({"status": "ok", "applied": applied, "skipped": skipped, "total": total})
    return redirect(url_for("mail.search", account_id=account_id, q=query))


@mail_bp.route("/mail/search/full", methods=["POST"])
@require_customer
def full_search():
    query = request.form.get("q", "")
    user_id = session.get("user_id")
    account_id = int(request.form.get("account_id") or 0)
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=user_id).first_or_404()
    if not (query or "").strip():
        return render_template(
            "search_full.html", results=[], query=query, account_id=account_id, folders=[]
        )
    settings = _get_or_create_settings(user_id)
    key = get_user_key(user_id)
    conn = open_cache(account.cache_db_path, key)
    uid_folder_pairs = {}
    results_remote = []
    known_folders = []
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    for folder in list_folders(client):
        known_folders.append(folder)
        select_folder(client, folder)
        uids = search_full_text(client, query)
        for uid in uids:
            msg = fetch_message(client, uid)
            if not msg:
                continue
            uid_folder_pairs[len(results_remote)] = (str(uid), folder)
            results_remote.append(
                {
                    "folder": folder,
                    "subject": normalize_header_text(msg.get("Subject", "")) or _("(no subject)"),
                    "from": decode_address_header(msg.get("From", "")),
                    "date": msg.get("Date", ""),
                    "date_display": _format_short_date(msg.get("Date", ""), settings.timezone),
                    "date_ts": _message_date_ts(msg.get("Date", "")),
                }
            )
    safe_logout(client)

    cache_lookup = {}
    for idx, (uid_val, folder_name) in uid_folder_pairs.items():
        row = conn.execute(
            "SELECT id, flags FROM messages WHERE uid = ? AND folder = ?",
            (uid_val, folder_name),
        ).fetchone()
        if row:
            cache_lookup[idx] = {"id": row["id"], "flags": row["flags"] or ""}

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

    results_remote.sort(key=lambda row: row.get("date_ts") or 0, reverse=True)
    return render_template(
        "search_full.html",
        results=results_remote,
        query=query,
        account_id=account_id,
        folders=known_folders,
    )
