from flask import session, request, redirect, url_for

from app.shared.models.core import CustomerAccount
from app.shared.keys import get_user_key
from app.modules.mail.services.secrets import decrypt_with_key
from app.modules.mail.services.imap_client import select_folder, set_flag, move_message
from app.modules.mail.services.cache_db import open_cache, get_message
from app.shared.auth import require_customer

from app.modules.mail.controllers.helpers import (
    mail_bp,
    _imap_for_account,
    _get_or_create_settings,
    _parse_flags,
)


@mail_bp.route("/mail/bulk", methods=["POST"])
@require_customer
def bulk_action():
    from app.modules.mail.services.protection import protection_reason

    action = request.form.get("action")
    account_id = int(request.form.get("account_id"))
    ids = request.form.getlist("message_ids")
    account = CustomerAccount.query.filter_by(id=account_id, customer_id=session.get("user_id")).first_or_404()
    key = get_user_key(session.get("user_id"))
    secret = decrypt_with_key(account.encrypted_secret, key) if account.encrypted_secret else None
    client, _domain = _imap_for_account(account, secret)
    conn = open_cache(account.cache_db_path, key)
    settings = _get_or_create_settings(session.get("user_id"))
    for message_id in ids:
        message = get_message(conn, int(message_id))
        if not message:
            continue
        uid = message["uid"]
        folder = message["folder"]
        destination = request.form.get("destination")
        targets_trash = action == "delete" or (destination or "").strip().lower() == "trash"
        if targets_trash and protection_reason(_parse_flags(message["flags"]), settings):
            continue
        select_folder(client, folder)
        if action == "mark_read":
            set_flag(client, uid, "\\Seen", add=True)
        elif action == "mark_unread":
            set_flag(client, uid, "\\Seen", add=False)
        elif action == "flag":
            set_flag(client, uid, "\\Flagged", add=True)
        elif action == "delete":
            move_message(client, uid, "Trash")
        elif action == "move":
            move_message(client, uid, destination)
    client.expunge()
    client.logout()
    return redirect(url_for("mail.folder_view", account_id=account_id, folder="Inbox"))
