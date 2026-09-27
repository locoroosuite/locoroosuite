from flask import jsonify, redirect, request, session, url_for

from app.modules.mail.controllers.helpers import (
    _get_or_create_settings,
    _imap_for_account,
    _parse_flags,
    mail_bp,
)
from app.modules.mail.services.cache_db import (
    get_message,
    open_cache,
    update_flags_bulk,
)
from app.modules.mail.services.imap_client import move_message, select_folder, set_flag
from app.modules.mail.services.secrets import decrypt_with_key
from app.shared.auth import require_customer
from app.shared.keys import get_user_key
from app.shared.models.core import CustomerAccount


@mail_bp.route("/mail/bulk", methods=["POST"])
@require_customer
def bulk_action():
    from app.modules.mail.services.protection import protection_reason

    action = request.form.get("action")
    account_id = int(request.form.get("account_id") or 0)
    ids = request.form.getlist("message_ids")
    is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    account = CustomerAccount.query.filter_by(
        id=account_id, customer_id=session.get("user_id")
    ).first_or_404()
    key = get_user_key(session.get("user_id"))
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    conn = open_cache(account.cache_db_path, key)
    settings = _get_or_create_settings(session.get("user_id"))
    applied = 0
    skipped = 0
    for message_id in ids:
        message = get_message(conn, int(message_id))
        if not message:
            continue
        uid = message["uid"]
        folder = message["folder"]
        destination = request.form.get("destination")
        targets_trash = action == "delete" or (destination or "").strip().lower() == "trash"
        if targets_trash and protection_reason(_parse_flags(message["flags"]), settings):
            skipped += 1
            continue
        select_folder(client, folder)
        if action == "mark_read":
            set_flag(client, uid, "\\Seen", add=True)
            update_flags_bulk(
                conn, folder, {uid: _merge_flag(_parse_flags(message["flags"]), "\\Seen")}
            )
        elif action == "mark_unread":
            set_flag(client, uid, "\\Seen", add=False)
            update_flags_bulk(
                conn, folder, {uid: [f for f in _parse_flags(message["flags"]) if f != "\\Seen"]}
            )
        elif action == "flag":
            set_flag(client, uid, "\\Flagged", add=True)
            update_flags_bulk(
                conn, folder, {uid: _merge_flag(_parse_flags(message["flags"]), "\\Flagged")}
            )
        elif action == "delete":
            move_message(client, uid, "Trash")
        elif action == "move":
            move_message(client, uid, destination)
        applied += 1
    client.expunge()
    client.logout()
    if is_xhr:
        return jsonify({"status": "ok", "applied": applied, "skipped": skipped})
    return redirect(url_for("mail.folder_view", account_id=account_id, folder="Inbox"))


def _merge_flag(flags, flag):
    merged = list(flags)
    if flag not in merged:
        merged.append(flag)
    return merged
