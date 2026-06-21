from __future__ import annotations

import contextlib
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from typing import Any

from flask import Response, current_app, g, request

from app.api.controllers.helpers import (
    ApiError,
    api_error,
    api_paginated,
    api_response,
    get_api_account_id,
    require_api_token,
    require_scope,
)
from app.api.openapi import create_api_blueprint
from app.api.schemas.common import BulkResponse, ErrorResponse
from app.api.schemas.mail import (
    AttachmentPath,
    BulkDeleteBody,
    BulkFlagBody,
    BulkMoveBody,
    CreateDraftBody,
    CreateFolderBody,
    DraftPath,
    FolderListResponse,
    FolderMutationResponse,
    FolderPath,
    GetMessageQuery,
    ListMessagesQuery,
    MessageDetailResponse,
    MessageListResponse,
    MessagePath,
    MoveMessageBody,
    RenameFolderBody,
    SearchQuery,
    SendMessageBody,
    ThreadPath,
    UpdateFlagsBody,
)
from app.modules.mail.services.cache import build_cache_path
from app.modules.mail.services.cache_db import (
    delete_folder_in_cache,
    delete_message_by_id,
    get_message,
    get_message_by_uid_and_folder,
    list_cached_folders,
    list_messages_with_threading,
    list_thread_messages,
    open_cache,
    rename_folder_in_cache,
    search_local,
    update_flags,
    upsert_folder,
    upsert_message,
)
from app.modules.mail.services.protection import (
    LOCKED_KEYWORD,
    folder_is_protected,
    is_system_folder,
    message_is_protected,
    protected_delete_message,
    protection_reason,
)
from app.modules.mail.services.secrets import decrypt_with_key
from app.shared.models.core import CustomerAccount, CustomerSettings, Domain
from app.shared.ui_events import push_ui_event

bp = create_api_blueprint("mail", "Mail operations")


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}  # Row iteration yields values, not keys; .keys() is required


def _get_cache_conn(account_id, dek):
    account = CustomerAccount.query.filter_by(id=account_id).first()
    if not account:
        raise ApiError("NOT_FOUND", "Account not found", 404)
    path = account.cache_db_path or build_cache_path(account.customer_id, account.id)
    return open_cache(path, dek)


def _get_settings():
    return CustomerSettings.query.filter_by(customer_id=g.api_context["customer_id"]).first()


def _safe_enqueue_sync(account_id, *, folder=None, reason="manual", priority=10):
    sync_manager = getattr(current_app, "sync_manager", None)
    if sync_manager is None:
        return
    with contextlib.suppress(Exception):
        sync_manager.enqueue_sync(account_id, folder=folder, reason=reason, priority=priority)


def _message_to_dict(row, settings=None):
    d = _row_to_dict(row)
    flags_list = _parse_flags_list(d.get("flags"))
    return {
        "id": d["id"],
        "folder": d.get("folder"),
        "subject": d.get("subject") or "",
        "from": d.get("sender") or "",
        "to": d.get("recipients") or "",
        "cc": d.get("cc") or "",
        "date": d.get("date") or "",
        "flags": d.get("flags") or "",
        "snippet": d.get("snippet") or "",
        "thread_id": d.get("thread_id"),
        "unread": "\\Seen" not in (d.get("flags") or ""),
        "flagged": "\\Flagged" in (d.get("flags") or ""),
        "protected": message_is_protected(flags_list, settings),
    }


def _parse_flags_list(raw_flags):
    if not raw_flags:
        return []
    if isinstance(raw_flags, (list, tuple)):
        return list(raw_flags)
    try:
        parsed = json.loads(raw_flags)
        if isinstance(parsed, (list, tuple)):
            return list(parsed)
        return []
    except (TypeError, ValueError):
        return []


def _merge_flag(flags_list, flag, add):
    if add:
        return list(dict.fromkeys(flags_list + [flag]))
    return [f for f in flags_list if f != flag]


def _sync_single_message_to_cache(account, domain, secret, account_id, dek, folder, uid):
    from app.modules.mail.services.imap_client import (
        fetch_message_with_flags,
        safe_logout,
        select_folder,
    )
    from app.modules.mail.services.imap_sync import _prepare_message_args, _to_uid_str

    uid_str = _to_uid_str(uid)
    client = _imap_connect(account, domain, secret)
    try:
        select_folder(client, folder)
        msg, flags, internal_date = fetch_message_with_flags(client, uid_str)
    finally:
        safe_logout(client)
    if not msg:
        return None
    args = _prepare_message_args(msg, account=account)
    conn = _get_cache_conn(account_id, dek)
    try:
        upsert_message(
            conn,
            uid_str,
            folder,
            args["subject"],
            args["sender"],
            args["recipients"],
            args["date"],
            flags,
            args["snippet"],
            args["body"],
            args["has_attachments"],
            args["message_id"],
            thread_id=args["thread_id"],
            in_reply_to=args["in_reply_to"],
            ref_list=args["ref_list"],
            internal_date=internal_date,
            is_bounce=args["is_bounce"],
            bounce_reason=args["bounce_reason"],
            original_subject=args["original_subject"],
            cc=args["cc"],
            attachment_list=args["attachment_list"],
            body_html=args["body_html"],
        )
        row = get_message_by_uid_and_folder(conn, uid_str, folder)
    finally:
        conn.close()
    return row


@bp.get(
    "/mail/folders",
    summary="List mail folders",
    description="Returns all mail folders for the authenticated account with unread message counts. Requires `mail:read` scope.",
    responses={"200": FolderListResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["mail:read"])
def api_list_folders():
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    settings = _get_settings()
    conn = _get_cache_conn(account_id, dek)
    try:
        folders = list_cached_folders(conn)
        items = []
        for f in folders:
            fd = _row_to_dict(f)
            name = fd["name"]
            items.append(
                {
                    "id": name,
                    "name": name,
                    "unread_count": fd.get("unread_count", 0),
                    "protected": folder_is_protected(settings, name),
                }
            )
        return api_response(items)
    finally:
        conn.close()


@bp.post(
    "/mail/folders",
    summary="Create mail folder",
    description="Creates a new mail folder (IMAP CREATE). Idempotent: creating an existing mailbox returns success with created=false. An optional `parent` nests the folder using the server hierarchy delimiter. Requires `mail:write` scope.",
    responses={"200": FolderMutationResponse, "401": ErrorResponse, "400": ErrorResponse},
)
@require_api_token(scopes=["mail:write"])
@require_scope("mail", "write")
def api_create_folder(body: CreateFolderBody):
    import imaplib as _imaplib

    from app.modules.mail.services.imap_client import (
        create_folder as imap_create_folder,
    )
    from app.modules.mail.services.imap_client import (
        encode_mailbox_name,
        get_folder_delimiter,
        list_folders,
        safe_logout,
    )

    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    name = (body.name or "").strip()
    if not name:
        return api_error("VALIDATION_ERROR", "'name' is required", 400)
    parent = (body.parent or "").strip() or None
    account, domain, secret = _get_account_and_secret(account_id, dek)
    client = _imap_connect(account, domain, secret)
    full_name = name
    try:
        existing = [f.lower() for f in list_folders(client)]
        if parent:
            delim = get_folder_delimiter(client)
            full_name = f"{parent}{delim}{name}"
        created = False
        if full_name.lower() not in existing:
            try:
                status, _ = imap_create_folder(client, encode_mailbox_name(full_name))
                created = status == "OK"
            except _imaplib.IMAP4.error as exc:
                return api_error("IMAP_ERROR", f"Failed to create folder: {exc}", 400)
    finally:
        safe_logout(client)
    conn = _get_cache_conn(account_id, dek)
    try:
        upsert_folder(conn, full_name, 0)
    finally:
        conn.close()
    _safe_enqueue_sync(account_id, folder=full_name, reason="folder_created", priority=5)
    push_ui_event(
        g.api_context["customer_id"],
        "mail",
        "folder_created",
        {"account_id": account_id, "folder": full_name},
    )
    return api_response({"id": full_name, "name": full_name, "created": created})


@bp.post(
    "/mail/folders/<path:folder>/rename",
    summary="Rename mail folder",
    description="Renames a mail folder (IMAP RENAME). System folders (INBOX, Sent, Drafts, Trash, Junk, Bookings) cannot be renamed. Requires `mail:write` scope.",
    responses={
        "200": FolderMutationResponse,
        "401": ErrorResponse,
        "400": ErrorResponse,
        "409": ErrorResponse,
    },
)
@require_api_token(scopes=["mail:write"])
@require_scope("mail", "write")
def api_rename_folder(path: FolderPath, body: RenameFolderBody):
    import imaplib as _imaplib

    from app.modules.mail.services.imap_client import (
        encode_mailbox_name,
        safe_logout,
    )
    from app.modules.mail.services.imap_client import (
        rename_folder as imap_rename_folder,
    )

    old_name = path.folder
    new_name = (body.name or "").strip()
    if not new_name:
        return api_error("VALIDATION_ERROR", "'name' is required", 400)
    if is_system_folder(old_name):
        return api_error("PROTECTED", "System folders cannot be renamed.", 409)
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account, domain, secret = _get_account_and_secret(account_id, dek)
    client = _imap_connect(account, domain, secret)
    try:
        try:
            status, _ = imap_rename_folder(
                client, encode_mailbox_name(old_name), encode_mailbox_name(new_name)
            )
            if status != "OK":
                return api_error("IMAP_ERROR", "Failed to rename folder", 400)
        except _imaplib.IMAP4.error as exc:
            return api_error("IMAP_ERROR", f"Failed to rename folder: {exc}", 400)
    finally:
        safe_logout(client)
    conn = _get_cache_conn(account_id, dek)
    try:
        rename_folder_in_cache(conn, old_name, new_name)
    finally:
        conn.close()
    _safe_enqueue_sync(account_id, folder=new_name, reason="folder_renamed", priority=5)
    push_ui_event(
        g.api_context["customer_id"],
        "mail",
        "folder_renamed",
        {"account_id": account_id, "old_folder": old_name, "new_folder": new_name},
    )
    return api_response({"id": new_name, "name": new_name})


@bp.delete(
    "/mail/folders/<path:folder>",
    summary="Delete mail folder",
    description="Deletes a mail folder (IMAP DELETE). System folders and folders the customer has marked protected cannot be deleted. Requires `mail:write` scope.",
    responses={
        "200": FolderMutationResponse,
        "401": ErrorResponse,
        "400": ErrorResponse,
        "409": ErrorResponse,
    },
)
@require_api_token(scopes=["mail:write"])
@require_scope("mail", "write")
def api_delete_folder(path: FolderPath):
    import imaplib as _imaplib

    from app.modules.mail.services.imap_client import (
        delete_folder as imap_delete_folder,
    )
    from app.modules.mail.services.imap_client import (
        encode_mailbox_name,
        safe_logout,
    )

    folder = path.folder
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    customer_id = g.api_context["customer_id"]
    settings = CustomerSettings.query.filter_by(customer_id=customer_id).first()
    if folder_is_protected(settings, folder):
        return api_error("PROTECTED", "This folder is protected and cannot be deleted.", 409)
    account, domain, secret = _get_account_and_secret(account_id, dek)
    client = _imap_connect(account, domain, secret)
    try:
        try:
            status, _ = imap_delete_folder(client, encode_mailbox_name(folder))
            if status != "OK":
                return api_error("IMAP_ERROR", "Failed to delete folder", 400)
        except _imaplib.IMAP4.error as exc:
            return api_error("IMAP_ERROR", f"Failed to delete folder: {exc}", 400)
    finally:
        safe_logout(client)
    conn = _get_cache_conn(account_id, dek)
    try:
        delete_folder_in_cache(conn, folder)
    finally:
        conn.close()
    push_ui_event(
        customer_id, "mail", "folder_deleted", {"account_id": account_id, "folder": folder}
    )
    return api_response({"id": folder, "deleted": True})


@bp.get(
    "/mail/folders/<path:folder>/messages",
    summary="List messages in folder",
    description="Returns paginated messages from the specified folder, newest first. Supports filtering by unread/flagged status and cursor-based pagination. Requires `mail:read` scope.",
    responses={"200": MessageListResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["mail:read"])
def api_list_messages(path: FolderPath, query: ListMessagesQuery):
    folder = path.folder
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    limit = min(int(request.args.get("max_results", 50)), 200)
    unread_only = request.args.get("unread") == "true"
    flagged_only = request.args.get("flagged") == "true"
    cursor_param = request.args.get("cursor")
    after_id = int(cursor_param) if cursor_param else None
    conn = _get_cache_conn(account_id, dek)
    try:
        rows = list_messages_with_threading(conn, folder, limit=limit + 1, after_id=after_id)
        has_more = len(rows) > limit
        rows = rows[:limit]
        settings = _get_settings()
        items = [_message_to_dict(r, settings) for r in rows]
        next_cursor = None
        if has_more and items:
            next_cursor = str(items[-1]["id"])
        if unread_only:
            items = [i for i in items if i["unread"]]
        if flagged_only:
            items = [i for i in items if i["flagged"]]
        return api_paginated(items, next_cursor=next_cursor, has_more=has_more)
    finally:
        conn.close()


@bp.get(
    "/mail/messages/<int:message_id>",
    summary="Get message detail",
    description="Returns the full message including plain-text and HTML bodies. Optionally marks the message as read via IMAP. Requires `mail:read` scope.",
    responses={"200": MessageDetailResponse, "401": ErrorResponse, "404": ErrorResponse},
)
@require_api_token(scopes=["mail:read"])
def api_get_message(path: MessagePath, query: GetMessageQuery):
    message_id = path.message_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    mark_read = request.args.get("mark_read", "").lower() == "true"
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_message(conn, message_id)
        if not row:
            return api_error("NOT_FOUND", "Message not found", 404)
        d = _row_to_dict(row)
        if mark_read:
            flags = _parse_flags_list(d.get("flags", ""))
            if "\\Seen" not in flags:
                flags = _merge_flag(flags, "\\Seen", True)
                update_flags(conn, message_id, flags)
                d["flags"] = json.dumps(flags)
    finally:
        conn.close()
    if mark_read:
        try:
            account, domain, secret = _get_account_and_secret(account_id, dek)
            from app.modules.mail.services.imap_client import safe_logout, select_folder, set_flag

            client = _imap_connect(account, domain, secret)
            try:
                select_folder(client, d.get("folder", "INBOX"))
                set_flag(client, d.get("uid"), "\\Seen", add=True)
            finally:
                safe_logout(client)
        except Exception:
            pass
    result = _message_to_dict_from_raw(d, _get_settings())
    return api_response(result)


def _message_to_dict_from_raw(d, settings=None):
    flags_list = _parse_flags_list(d.get("flags"))
    return {
        "id": d["id"],
        "folder": d.get("folder"),
        "subject": d.get("subject") or "",
        "from": d.get("sender") or "",
        "to": d.get("recipients") or "",
        "cc": d.get("cc") or "",
        "date": d.get("date") or "",
        "flags": d.get("flags") or "",
        "snippet": d.get("snippet") or "",
        "thread_id": d.get("thread_id"),
        "unread": "\\Seen" not in (d.get("flags") or ""),
        "flagged": "\\Flagged" in (d.get("flags") or ""),
        "protected": message_is_protected(flags_list, settings),
        "body_plain": d.get("body") or "",
        "body_html": d.get("body_html") or "",
    }


@bp.get(
    "/mail/threads/<thread_id>",
    summary="Get message thread",
    description="Returns all messages in a conversation thread, ordered chronologically. Requires `mail:read` scope.",
    responses={"200": MessageListResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["mail:read"])
def api_get_thread(path: ThreadPath):
    thread_id = path.thread_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    conn = _get_cache_conn(account_id, dek)
    try:
        rows = list_thread_messages(conn, thread_id)
        settings = _get_settings()
        items = [_message_to_dict_from_raw(_row_to_dict(r), settings) for r in rows]
        return api_response(items)
    finally:
        conn.close()


@bp.get(
    "/mail/search",
    summary="Search messages",
    description="Full-text search across all folders in the account cache. Returns matching messages sorted by relevance. Requires `mail:read` scope.",
    responses={"200": MessageListResponse, "401": ErrorResponse, "400": ErrorResponse},
)
@require_api_token(scopes=["mail:read"])
def api_search_messages(query: SearchQuery):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    q = request.args.get("q", "")
    if not q:
        return api_error("VALIDATION_ERROR", "Query parameter 'q' is required", 400)
    limit = min(int(request.args.get("max_results", 50)), 200)
    conn = _get_cache_conn(account_id, dek)
    try:
        rows = search_local(conn, q, limit=limit)
        settings = _get_settings()
        items = [_message_to_dict(r, settings) for r in rows]
        return api_paginated(items)
    finally:
        conn.close()


@bp.patch(
    "/mail/messages/<int:message_id>",
    summary="Update message flags",
    description="Updates read/flagged status of a message. Changes are synced to the IMAP server. Requires `mail:write` scope.",
    responses={"200": MessageDetailResponse, "401": ErrorResponse, "404": ErrorResponse},
)
@require_api_token(scopes=["mail:write"])
@require_scope("mail", "write")
def api_update_flags(path: MessagePath, body: UpdateFlagsBody):
    message_id = path.message_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    flags_data = body.flags
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_message(conn, message_id)
        if not row:
            return api_error("NOT_FOUND", "Message not found", 404)
        d = _row_to_dict(row)
        flag_list = _parse_flags_list(d.get("flags", ""))
        changed_flags = []
        if flags_data.get("read") is True and "\\Seen" not in flag_list:
            flag_list = _merge_flag(flag_list, "\\Seen", True)
            changed_flags.append(("\\Seen", True))
        elif flags_data.get("read") is False and "\\Seen" in flag_list:
            flag_list = _merge_flag(flag_list, "\\Seen", False)
            changed_flags.append(("\\Seen", False))
        if flags_data.get("flagged") is True and "\\Flagged" not in flag_list:
            flag_list = _merge_flag(flag_list, "\\Flagged", True)
            changed_flags.append(("\\Flagged", True))
        elif flags_data.get("flagged") is False and "\\Flagged" in flag_list:
            flag_list = _merge_flag(flag_list, "\\Flagged", False)
            changed_flags.append(("\\Flagged", False))
        if flags_data.get("locked") is True and LOCKED_KEYWORD not in flag_list:
            flag_list = _merge_flag(flag_list, LOCKED_KEYWORD, True)
            changed_flags.append((LOCKED_KEYWORD, True))
        elif flags_data.get("locked") is False and LOCKED_KEYWORD in flag_list:
            flag_list = _merge_flag(flag_list, LOCKED_KEYWORD, False)
            changed_flags.append((LOCKED_KEYWORD, False))
        update_flags(conn, message_id, flag_list)
    finally:
        conn.close()
    if changed_flags:
        try:
            account, domain, secret = _get_account_and_secret(account_id, dek)
            from app.modules.mail.services.imap_client import safe_logout, select_folder, set_flag

            client = _imap_connect(account, domain, secret)
            try:
                select_folder(client, d.get("folder", "INBOX"))
                for flag, add in changed_flags:
                    set_flag(client, d.get("uid"), flag, add=add)
            finally:
                safe_logout(client)
        except Exception:
            pass
    push_ui_event(
        g.api_context["customer_id"],
        "mail",
        "flags_updated",
        {"account_id": account_id, "message_id": message_id},
    )
    return api_response({"id": message_id, "flags": json.dumps(flag_list)})


@bp.post(
    "/mail/bulk/flag",
    summary="Bulk update message flags",
    description="Updates read/flagged status for up to 100 messages in a single request. Changes are synced to the IMAP server. Requires `mail:write` scope.",
    responses={"200": BulkResponse, "401": ErrorResponse, "400": ErrorResponse},
)
@require_api_token(scopes=["mail:write"])
@require_scope("mail", "write")
def api_bulk_flag(body: BulkFlagBody):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    data = request.get_json(silent=True)
    items = data.get("items", [])[:100] if data else []
    if not items:
        return api_error("VALIDATION_ERROR", "No items provided", 400)
    conn = _get_cache_conn(account_id, dek)
    succeeded = []
    failed = []
    try:
        for i, item in enumerate(items):
            mid = item.get("message_id")
            if not mid:
                failed.append({"index": i, "error": {"code": "MISSING_ID"}})
                continue
            row = get_message(conn, mid)
            if not row:
                failed.append({"index": i, "error": {"code": "NOT_FOUND"}})
                continue
            current = _row_to_dict(row).get("flags", "") or ""
            flag_list = _parse_flags_list(current)
            flags = item.get("flags", {})
            if flags.get("read") is True and "\\Seen" not in flag_list:
                flag_list = _merge_flag(flag_list, "\\Seen", True)
            elif flags.get("read") is False and "\\Seen" in flag_list:
                flag_list = _merge_flag(flag_list, "\\Seen", False)
            if flags.get("flagged") is True and "\\Flagged" not in flag_list:
                flag_list = _merge_flag(flag_list, "\\Flagged", True)
            elif flags.get("flagged") is False and "\\Flagged" in flag_list:
                flag_list = _merge_flag(flag_list, "\\Flagged", False)
            if flags.get("locked") is True and LOCKED_KEYWORD not in flag_list:
                flag_list = _merge_flag(flag_list, LOCKED_KEYWORD, True)
            elif flags.get("locked") is False and LOCKED_KEYWORD in flag_list:
                flag_list = _merge_flag(flag_list, LOCKED_KEYWORD, False)
            update_flags(conn, mid, flag_list)
            succeeded.append({"message_id": mid})
        push_ui_event(
            g.api_context["customer_id"], "mail", "flags_updated", {"account_id": account_id}
        )
        return api_response({"succeeded": succeeded, "failed": failed})
    finally:
        conn.close()


@bp.post(
    "/mail/bulk/move",
    summary="Bulk move messages",
    description="Moves up to 100 messages to a destination folder. Messages are moved via IMAP and expunged. Requires `mail:write` scope.",
    responses={"200": BulkResponse, "401": ErrorResponse, "400": ErrorResponse},
)
@require_api_token(scopes=["mail:write"])
@require_scope("mail", "write")
def api_bulk_move(body: BulkMoveBody):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    data = request.get_json(silent=True)
    items = data.get("items", [])[:100] if data else []
    if not items:
        return api_error("VALIDATION_ERROR", "No items provided", 400)
    dest_folder = data.get("folder_id") or data.get("destination")
    if not dest_folder:
        return api_error("VALIDATION_ERROR", "'folder_id' or 'destination' is required", 400)
    conn = _get_cache_conn(account_id, dek)
    succeeded = []
    failed = []
    to_move = []
    trash_move = (dest_folder or "").strip().lower() == "trash"
    settings = CustomerSettings.query.filter_by(customer_id=g.api_context["customer_id"]).first()
    try:
        for i, item in enumerate(items):
            mid = item.get("message_id")
            if not mid:
                failed.append({"index": i, "error": {"code": "MISSING_ID"}})
                continue
            row = get_message(conn, mid)
            if not row:
                failed.append({"index": i, "error": {"code": "NOT_FOUND"}})
                continue
            d = _row_to_dict(row)
            if trash_move:
                reason = protection_reason(_parse_flags_list(d.get("flags", "")), settings)
                if reason:
                    failed.append(
                        {
                            "message_id": mid,
                            "error": {
                                "code": "PROTECTED",
                                "message": protected_delete_message(reason),
                            },
                        }
                    )
                    continue
            to_move.append(
                {"message_id": mid, "uid": d.get("uid"), "folder": d.get("folder", "INBOX")}
            )
    finally:
        conn.close()
    if not to_move:
        return api_response({"succeeded": succeeded, "failed": failed})
    account, domain, secret = _get_account_and_secret(account_id, dek)
    from app.modules.mail.services.imap_client import move_message, safe_logout, select_folder

    client = _imap_connect(account, domain, secret)
    try:
        current_folder = None
        for item in to_move:
            uid = item["uid"]
            folder = item["folder"]
            if folder != current_folder:
                select_folder(client, folder)
                current_folder = folder
            try:
                move_message(client, uid, dest_folder)
                succeeded.append({"message_id": item["message_id"]})
            except Exception:
                failed.append({"message_id": item["message_id"], "error": {"code": "IMAP_ERROR"}})
        client.expunge()
    finally:
        safe_logout(client)
    push_ui_event(
        g.api_context["customer_id"],
        "mail",
        "messages_moved",
        {"account_id": account_id, "folder": dest_folder},
    )
    return api_response({"succeeded": succeeded, "failed": failed})


@bp.post(
    "/mail/bulk/delete",
    summary="Bulk delete messages",
    description="Moves up to 100 messages to the Trash folder and removes them from the cache. Creates the Trash folder if it does not exist. Requires `mail:write` scope.",
    responses={"200": BulkResponse, "401": ErrorResponse, "400": ErrorResponse},
)
@require_api_token(scopes=["mail:write"])
@require_scope("mail", "write")
def api_bulk_delete(body: BulkDeleteBody):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    data = request.get_json(silent=True)
    items = data.get("items", [])[:100] if data else []
    if not items:
        return api_error("VALIDATION_ERROR", "No items provided", 400)
    conn = _get_cache_conn(account_id, dek)
    succeeded = []
    failed = []
    to_delete = []
    settings = CustomerSettings.query.filter_by(customer_id=g.api_context["customer_id"]).first()
    try:
        for i, item in enumerate(items):
            mid = item.get("message_id")
            if not mid:
                failed.append({"index": i, "error": {"code": "MISSING_ID"}})
                continue
            row = get_message(conn, mid)
            if not row:
                failed.append({"index": i, "error": {"code": "NOT_FOUND"}})
                continue
            d = _row_to_dict(row)
            reason = protection_reason(_parse_flags_list(d.get("flags", "")), settings)
            if reason:
                failed.append(
                    {
                        "message_id": mid,
                        "error": {"code": "PROTECTED", "message": protected_delete_message(reason)},
                    }
                )
                continue
            to_delete.append(
                {"message_id": mid, "uid": d.get("uid"), "folder": d.get("folder", "INBOX")}
            )
    finally:
        conn.close()
    if not to_delete:
        return api_response({"succeeded": succeeded, "failed": failed})
    account, domain, secret = _get_account_and_secret(account_id, dek)
    from app.modules.mail.services.imap_client import (
        create_folder,
        list_folders,
        move_message,
        safe_logout,
        select_folder,
    )

    client = _imap_connect(account, domain, secret)
    try:
        server_folders = [f.upper() for f in list_folders(client)]
        trash = "Trash"
        if "TRASH" not in server_folders:
            if "DELETED" in server_folders:
                trash = "Deleted"
            else:
                create_folder(client, "Trash")
        current_folder = None
        for item in to_delete:
            uid = item["uid"]
            folder = item["folder"]
            if folder != current_folder:
                select_folder(client, folder)
                current_folder = folder
            try:
                move_message(client, uid, trash)
                succeeded.append({"message_id": item["message_id"]})
            except Exception:
                failed.append({"message_id": item["message_id"], "error": {"code": "IMAP_ERROR"}})
        client.expunge()
    finally:
        safe_logout(client)
    succeeded_ids = {s["message_id"] for s in succeeded}
    if succeeded_ids:
        conn = _get_cache_conn(account_id, dek)
        try:
            for mid in succeeded_ids:
                delete_message_by_id(conn, mid)
        finally:
            conn.close()
    push_ui_event(
        g.api_context["customer_id"], "mail", "messages_deleted", {"account_id": account_id}
    )
    return api_response({"succeeded": succeeded, "failed": failed})


def _get_account_and_secret(account_id, dek):
    account = CustomerAccount.query.filter_by(id=account_id, is_active=True).first()
    if not account:
        raise ApiError("NOT_FOUND", "Account not found", 404)
    domain = Domain.query.filter_by(id=account.domain_id).first()
    if not domain or not domain.is_active:
        raise ApiError("FORBIDDEN", "Domain is not active", 403)
    secret = decrypt_with_key(account.encrypted_secret, dek)
    return account, domain, secret


def _imap_connect(account, domain, secret):
    from app.modules.mail.services.imap_client import connect_imap, login_imap

    client = connect_imap(domain.imap_host, domain.imap_port, domain.imap_tls)
    login_imap(client, account.username, password=secret)
    return client


@bp.post(
    "/mail/messages/<int:message_id>/move",
    summary="Move message",
    description="Moves a single message to a destination folder via IMAP. Requires `mail:write` scope.",
    responses={
        "200": MessageDetailResponse,
        "401": ErrorResponse,
        "400": ErrorResponse,
        "404": ErrorResponse,
    },
)
@require_api_token(scopes=["mail:write"])
@require_scope("mail", "write")
def api_move_message(path: MessagePath, body: MoveMessageBody):
    message_id = path.message_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    dest_folder = body.folder_id or body.destination
    if not dest_folder:
        return api_error("VALIDATION_ERROR", "'folder_id' or 'destination' is required", 400)
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_message(conn, message_id)
        if not row:
            return api_error("NOT_FOUND", "Message not found", 404)
        d = _row_to_dict(row)
        if (dest_folder or "").strip().lower() == "trash":
            settings = CustomerSettings.query.filter_by(
                customer_id=g.api_context["customer_id"]
            ).first()
            reason = protection_reason(_parse_flags_list(d.get("flags", "")), settings)
            if reason:
                return api_error("PROTECTED", protected_delete_message(reason), 409)
        folder = d.get("folder", "INBOX")
        uid = d.get("uid")
    finally:
        conn.close()
    account, domain, secret = _get_account_and_secret(account_id, dek)
    from app.modules.mail.services.imap_client import move_message, safe_logout, select_folder

    client = _imap_connect(account, domain, secret)
    try:
        select_folder(client, folder)
        move_message(client, uid, dest_folder)
        client.expunge()
    finally:
        safe_logout(client)
    push_ui_event(
        g.api_context["customer_id"],
        "mail",
        "message_moved",
        {"account_id": account_id, "folder": dest_folder, "message_id": message_id},
    )
    return api_response({"id": message_id, "moved_to": dest_folder})


@bp.delete(
    "/mail/messages/<int:message_id>",
    summary="Delete message",
    description="Moves a message to the Trash folder via IMAP and removes it from the cache. Creates the Trash folder if it does not exist. Requires `mail:write` scope.",
    responses={"200": MessageDetailResponse, "401": ErrorResponse, "404": ErrorResponse},
)
@require_api_token(scopes=["mail:write"])
@require_scope("mail", "write")
def api_delete_message(path: MessagePath):
    message_id = path.message_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_message(conn, message_id)
        if not row:
            return api_error("NOT_FOUND", "Message not found", 404)
        d = _row_to_dict(row)
        settings = CustomerSettings.query.filter_by(
            customer_id=g.api_context["customer_id"]
        ).first()
        reason = protection_reason(_parse_flags_list(d.get("flags", "")), settings)
        if reason:
            return api_error("PROTECTED", protected_delete_message(reason), 409)
        folder = d.get("folder", "INBOX")
        uid = d.get("uid")
    finally:
        conn.close()
    account, domain, secret = _get_account_and_secret(account_id, dek)
    from app.modules.mail.services.imap_client import (
        create_folder,
        list_folders,
        move_message,
        safe_logout,
        select_folder,
    )

    client = _imap_connect(account, domain, secret)
    try:
        server_folders = [f.upper() for f in list_folders(client)]
        trash = "Trash"
        if "TRASH" not in server_folders:
            if "DELETED" in server_folders:
                trash = "Deleted"
            else:
                create_folder(client, "Trash")
        select_folder(client, folder)
        move_message(client, uid, trash)
        client.expunge()
    finally:
        safe_logout(client)
    conn = _get_cache_conn(account_id, dek)
    try:
        delete_message_by_id(conn, message_id)
    finally:
        conn.close()
    push_ui_event(
        g.api_context["customer_id"],
        "mail",
        "message_deleted",
        {"account_id": account_id, "message_id": message_id},
    )
    return api_response({"id": message_id, "deleted": True})


@bp.get(
    "/mail/messages/<int:message_id>/raw",
    summary="Get raw message source",
    description="Fetches the raw RFC 822 message source (.eml) from the IMAP server. Requires `mail:read` scope.",
    responses={"200": MessageDetailResponse, "401": ErrorResponse, "404": ErrorResponse},
)
@require_api_token(scopes=["mail:read"])
@require_scope("mail", "read")
def api_raw_message(path: MessagePath):
    message_id = path.message_id
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account, domain, secret = _get_account_and_secret(account_id, dek)
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_message(conn, message_id)
        if not row:
            return api_error("NOT_FOUND", "Message not found", 404)
        d = _row_to_dict(row)
        folder = d.get("folder", "INBOX")
        uid = d.get("uid")
    finally:
        conn.close()
    from app.modules.mail.services.imap_client import fetch_raw_message, safe_logout, select_folder

    client = _imap_connect(account, domain, secret)
    try:
        select_folder(client, folder)
        raw = fetch_raw_message(client, uid)
    finally:
        safe_logout(client)
    if raw is None:
        return api_error("NOT_FOUND", "Raw message not available", 404)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    return api_response(
        {
            "mime_type": "message/rfc822",
            "data": text,
            "filename": "message.eml",
        }
    )


@bp.post(
    "/mail/drafts",
    summary="Create draft",
    description="Creates a new draft in the Drafts folder via IMAP. If a replace_uid is provided, the existing draft is deleted first. Requires `mail:write` scope.",
    responses={"200": MessageDetailResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["mail:write"])
@require_scope("mail", "write")
def api_mail_create_draft(body: CreateDraftBody):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account, domain, secret = _get_account_and_secret(account_id, dek)
    to_list = body.to
    cc_list = body.cc
    bcc_list = body.bcc
    subject = body.subject
    body_html = body.body_html
    body_plain = body.body_plain
    replace_uid = body.replace_uid

    msg = MIMEMultipart("mixed")
    msg["From"] = account.email_address
    msg["To"] = ", ".join(to_list) if isinstance(to_list, list) else to_list
    if cc_list:
        msg["Cc"] = ", ".join(cc_list) if isinstance(cc_list, list) else cc_list
    if bcc_list:
        msg["Bcc"] = ", ".join(bcc_list) if isinstance(bcc_list, list) else bcc_list
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=account.email_address.split("@")[1])

    alt = MIMEMultipart("alternative")
    if body_plain:
        alt.attach(MIMEText(body_plain, "plain", "utf-8"))
    if body_html:
        alt.attach(MIMEText(body_html, "html", "utf-8"))
    msg.attach(alt)

    from app.modules.mail.services.imap_client import (
        append_message,
        create_folder,
        delete_message_by_uid,
        parse_append_uid,
        safe_logout,
        select_folder,
    )

    client = _imap_connect(account, domain, secret)
    try:
        if replace_uid:
            try:
                select_folder(client, "Drafts")
                delete_message_by_uid(client, str(replace_uid))
            except Exception:
                pass

        import imaplib as _imaplib

        try:
            status, resp_data = append_message(client, "Drafts", msg.as_bytes(), flags=["\\Draft"])
        except _imaplib.IMAP4.error:
            create_folder(client, "Drafts")
            status, resp_data = append_message(client, "Drafts", msg.as_bytes(), flags=["\\Draft"])
        draft_uid = parse_append_uid(resp_data)
    finally:
        safe_logout(client)

    _safe_enqueue_sync(account_id, folder="Drafts", reason="draft_saved", priority=5)

    push_ui_event(g.api_context["customer_id"], "mail", "draft_saved", {"account_id": account_id})

    result = {
        "status": "draft",
        "draft_id": draft_uid,
        "draft_uid": draft_uid,
        "message_id": msg["Message-ID"],
    }
    if draft_uid:
        try:
            row = _sync_single_message_to_cache(
                account, domain, secret, account_id, dek, "Drafts", draft_uid
            )
            if row:
                result.update(_message_to_dict(row))
        except Exception:
            pass

    return api_response(result, 201)


@bp.delete(
    "/mail/drafts/<uid>",
    summary="Delete draft",
    description="Deletes a draft from the Drafts folder by UID via IMAP. Requires `mail:write` scope.",
    responses={"200": MessageDetailResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["mail:write"])
@require_scope("mail", "write")
def api_mail_delete_draft(path: DraftPath):
    uid = path.uid
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account, domain, secret = _get_account_and_secret(account_id, dek)

    from app.modules.mail.services.imap_client import (
        delete_message_by_uid,
        safe_logout,
        select_folder,
    )

    client = _imap_connect(account, domain, secret)
    try:
        select_folder(client, "Drafts")
        delete_message_by_uid(client, str(uid))
    finally:
        safe_logout(client)

    _safe_enqueue_sync(account_id, folder="Drafts", reason="draft_deleted", priority=5)

    push_ui_event(g.api_context["customer_id"], "mail", "draft_deleted", {"account_id": account_id})

    return api_response({"status": "deleted", "draft_id": uid, "draft_uid": uid})


@bp.post(
    "/mail/messages",
    summary="Send message",
    description="Sends an email via SMTP and stores a copy in the Sent folder. If a draft_id is provided, the corresponding draft is deleted after sending. At least one recipient and one body (plain or HTML) are required. Requires `mail:write` scope.",
    responses={"200": MessageDetailResponse, "401": ErrorResponse, "400": ErrorResponse},
)
@require_api_token(scopes=["mail:write"])
@require_scope("mail", "write")
def api_send_message(body: SendMessageBody):
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account, domain, secret = _get_account_and_secret(account_id, dek)
    to_list = body.to
    cc_list = body.cc
    bcc_list = body.bcc
    if isinstance(to_list, str):
        to_list = [to_list]
    if isinstance(cc_list, str):
        cc_list = [cc_list]
    if isinstance(bcc_list, str):
        bcc_list = [bcc_list]
    subject = body.subject
    body_html = body.body_html
    body_plain = body.body_plain
    draft_id = body.draft_id or body.draft_uid
    if not to_list:
        return api_error("VALIDATION_ERROR", "'to' is required", 400)
    if not body_html and not body_plain:
        return api_error("VALIDATION_ERROR", "'body_html' or 'body_plain' is required", 400)

    from app.modules.mail.services.send import send_message

    result = send_message(
        account,
        domain,
        secret,
        to=to_list,
        cc=cc_list,
        bcc=bcc_list,
        subject=subject,
        body_plain=body_plain,
        body_html=body_html,
        draft_id=draft_id,
        get_cache_conn=lambda: _get_cache_conn(account_id, dek),
    )

    sent_uid = result.pop("sent_uid", None)

    _safe_enqueue_sync(account_id, folder="Sent", reason="send_complete", priority=5)
    _safe_enqueue_sync(account_id, folder="Drafts", reason="send_complete", priority=5)

    push_ui_event(g.api_context["customer_id"], "mail", "message_sent", {"account_id": account_id})

    if sent_uid:
        try:
            row = _sync_single_message_to_cache(
                account, domain, secret, account_id, dek, "Sent", sent_uid
            )
            if row:
                result.update(_message_to_dict(row))
        except Exception:
            pass

    return api_response(result, 201)


@bp.get(
    "/mail/messages/<int:message_id>/attachments/<int:attachment_index>",
    summary="Download attachment",
    description="Downloads an attachment from a message by its zero-based index. Returns the file as a binary download with appropriate Content-Type and Content-Disposition headers. Requires `mail:read` scope.",
    responses={"200": MessageDetailResponse, "401": ErrorResponse, "404": ErrorResponse},
)
@require_api_token(scopes=["mail:read"])
@require_scope("mail", "read")
def api_download_attachment(path: AttachmentPath):
    message_id = path.message_id
    attachment_index = path.attachment_index
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account, domain, secret = _get_account_and_secret(account_id, dek)
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_message(conn, message_id)
        if not row:
            return api_error("NOT_FOUND", "Message not found", 404)
        d = _row_to_dict(row)
        folder = d.get("folder", "INBOX")
        uid = d.get("uid")
    finally:
        conn.close()
    from app.modules.mail.services.imap_client import fetch_message, safe_logout, select_folder

    client = _imap_connect(account, domain, secret)
    try:
        select_folder(client, folder)
        msg = fetch_message(client, uid)
    finally:
        safe_logout(client)
    if not msg:
        return api_error("NOT_FOUND", "Message not available", 404)
    idx = 0
    for part in msg.walk():
        cd = part.get("Content-Disposition", "")
        if "attachment" not in cd:
            continue
        if idx == attachment_index:
            payload = part.get_payload(decode=True)
            filename = part.get_filename() or f"attachment_{attachment_index}"
            return Response(
                payload,
                mimetype=part.get_content_type(),
                headers={"Content-Disposition": f"attachment; filename={filename}"},
            )
        idx += 1
    return api_error("NOT_FOUND", "Attachment not found", 404)


@bp.get(
    "/mail/messages/<int:message_id>/attachments/<int:attachment_index>/view",
    summary="View attachment as HTML",
    description="Converts a message attachment to HTML for inline viewing. Uses pandoc for supported document types. Returns an error for unsupported file types. Requires `mail:read` scope.",
    responses={
        "200": MessageDetailResponse,
        "401": ErrorResponse,
        "404": ErrorResponse,
        "400": ErrorResponse,
    },
)
@require_api_token(scopes=["mail:read"])
@require_scope("mail", "read")
def api_view_attachment(path: AttachmentPath):
    from app.shared.pandoc_formats import convert_to_html, get_attachment_actions

    message_id = path.message_id
    attachment_index = path.attachment_index
    account_id = get_api_account_id()
    dek = g.api_context["dek"]
    account, domain, secret = _get_account_and_secret(account_id, dek)
    conn = _get_cache_conn(account_id, dek)
    try:
        row = get_message(conn, message_id)
        if not row:
            return api_error("NOT_FOUND", "Message not found", 404)
        d = _row_to_dict(row)
        folder = d.get("folder", "INBOX")
        uid = d.get("uid")
    finally:
        conn.close()
    from app.modules.mail.services.imap_client import fetch_message, safe_logout, select_folder

    client = _imap_connect(account, domain, secret)
    try:
        select_folder(client, folder)
        msg = fetch_message(client, uid)
    finally:
        safe_logout(client)
    if not msg:
        return api_error("NOT_FOUND", "Message not available", 404)
    idx = 0
    for part in msg.walk():
        cd = part.get("Content-Disposition", "")
        if "attachment" not in cd:
            continue
        if idx == attachment_index:
            payload = part.get_payload(decode=True)
            filename = part.get_filename() or f"attachment_{attachment_index}"
            actions = get_attachment_actions(filename)
            if not actions.get("view"):
                return api_error(
                    "UNSUPPORTED",
                    f"Cannot view .{filename.rsplit('.', 1)[-1] if '.' in filename else ''} files inline",
                    400,
                )
            pandoc_reader = actions.get("pandoc_reader")
            if not pandoc_reader:
                return api_error("UNSUPPORTED", "This file type cannot be converted to HTML", 400)
            html_content = convert_to_html(payload, pandoc_reader)
            if not html_content:
                return api_error("CONVERSION_ERROR", "Failed to convert attachment to HTML", 500)
            return api_response(
                {
                    "filename": filename,
                    "content_type": "text/html",
                    "html_content": html_content,
                }
            )
        idx += 1
    return api_error("NOT_FOUND", "Attachment not found", 404)
