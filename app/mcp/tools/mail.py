from __future__ import annotations

import json
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Annotated, Any

from flask import Flask
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from app.mcp.auth import McpAuthError
from app.mcp.errors import resilient_tool
from app.mcp.helpers import binary_response, err, ok, ok_paginated, resolve_read, resolve_write
from app.mcp.schemas import BulkFlagItem, BulkMessageIdItem, ensure_typed
from app.modules.mail.services.protection import folder_is_protected, message_is_protected

_AccId = Annotated[int | None, Field(description="Account ID (uses default account if omitted)")]

_mail_logger = logging.getLogger(__name__)


class _ServiceConnectionError(Exception):
    def __init__(self, service: str, host: str, original: Exception):
        self.service = service
        self.host = host
        self.original = original
        super().__init__(f"{service} ({host}): {original}")


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}  # Row iteration yields values, not keys; .keys() is required


def _parse_flags(raw):
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    return []


def _settings_for(customer_id):
    from app.shared.models.core import CustomerSettings

    return CustomerSettings.query.filter_by(customer_id=customer_id).first()


def _enqueue_sync(flask_app, account_id, *, folder=None, reason="manual", priority=10):
    sync_manager = getattr(flask_app, "sync_manager", None)
    if sync_manager is None:
        return
    try:
        sync_manager.enqueue_sync(account_id, folder=folder, reason=reason, priority=priority)
    except Exception:
        _mail_logger.debug("enqueue_sync failed (reason=%s)", reason, exc_info=True)


def _message_summary(row, settings=None):
    d = _row_to_dict(row) if not isinstance(row, dict) else row
    flags = _parse_flags(d.get("flags"))
    return {
        "id": d.get("id"),
        "folder": d.get("folder"),
        "subject": d.get("subject", ""),
        "from": d.get("sender", ""),
        "to": d.get("recipients", ""),
        "cc": d.get("cc", ""),
        "date": d.get("date", ""),
        "snippet": d.get("snippet", ""),
        "thread_id": d.get("thread_id"),
        "unread": "\\Seen" not in flags,
        "flagged": "\\Flagged" in flags,
        "protected": message_is_protected(flags, settings),
    }


def _message_detail(d, settings=None):
    result = _message_summary(d, settings)
    result["body_plain"] = d.get("body") or ""
    result["body_html"] = d.get("body_html") or ""
    return result


def _get_cache_conn(account_id, dek, flask_app):
    from app.modules.mail.services.cache import build_cache_path
    from app.modules.mail.services.cache_db import open_cache
    from app.shared.db import db
    from app.shared.models.core import CustomerAccount

    account = db.session.get(CustomerAccount, account_id)
    if not account:
        raise McpAuthError("NOT_FOUND", f"Account {account_id} not found")
    db_path = account.cache_db_path or build_cache_path(account.customer_id, account_id)
    return open_cache(db_path, dek)


def _get_account_and_secret(account_id, dek, flask_app):
    from app.modules.mail.services.secrets import decrypt_with_key
    from app.shared.db import db
    from app.shared.models.core import CustomerAccount, Domain

    account = db.session.get(CustomerAccount, account_id)
    if not account or not account.is_active:
        raise McpAuthError("NOT_FOUND", f"Account {account_id} not found")
    domain = db.session.get(Domain, account.domain_id)
    if not domain or not domain.is_active:
        raise McpAuthError("FORBIDDEN", "Domain not active")
    try:
        secret = decrypt_with_key(account.encrypted_secret, dek) if account.encrypted_secret else ""
    except Exception as exc:
        raise McpAuthError(
            "DEK_MISMATCH",
            "Your encryption key does not match the stored credentials. "
            "Reset your API access: go to Settings \u2192 API \u2192 Disable, then re-enable and create a new token.",
        ) from exc
    return account, domain, secret


def _imap_connect(account, domain, secret):
    from app.modules.mail.services.imap_client import connect_imap, login_imap

    host_label = f"{domain.imap_host}:{domain.imap_port}"
    try:
        client = connect_imap(domain.imap_host, domain.imap_port, domain.imap_tls)
        login_imap(client, account.username, password=secret)
        return client
    except Exception as exc:
        raise _ServiceConnectionError("IMAP", host_label, exc) from exc


def _merge_flag(flags_list, flag, add):
    result = list(flags_list)
    if add:
        if flag not in result:
            result.append(flag)
    else:
        result = [f for f in result if f != flag]
    return result


def register(mcp: FastMCP, flask_app: Flask) -> None:
    @mcp.tool(
        name="mail_list_folders",
        title="List Mail Folders",
        description="List all email folders with unread counts. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_list_folders(account_id: _AccId = None) -> str:
        ctx, aid, dek = resolve_read(flask_app, "mail", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import list_cached_folders

                rows = list_cached_folders(conn)
                settings = _settings_for(ctx["customer_id"])
                items = [
                    {
                        "name": r["name"],
                        "unread_count": r["unread_count"],
                        "protected": folder_is_protected(settings, r["name"]),
                    }
                    for r in rows
                ]
            finally:
                conn.close()
        return ok(items)

    @mcp.tool(
        name="mail_list_messages",
        title="List Messages in Folder",
        description="List messages in a specific email folder with optional filters. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_list_messages(
        folder_id: Annotated[str, Field(description="Folder ID (e.g. 'INBOX', 'Archive')")],
        cursor: Annotated[
            int | None, Field(description="Pagination cursor from previous response")
        ] = None,
        max_results: Annotated[
            int | None,
            Field(
                description="Maximum number of messages to return (1–200, default 50)", ge=1, le=200
            ),
        ] = None,
        unread: Annotated[bool | None, Field(description="Filter to unread messages only")] = None,
        flagged: Annotated[
            bool | None, Field(description="Filter to flagged/starred messages only")
        ] = None,
        since: Annotated[
            str | None, Field(description="ISO 8601 datetime — only messages after this time")
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "mail", account_id)
        limit = max_results or 50
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import list_messages_with_threading

                rows = list_messages_with_threading(conn, folder_id, limit=limit + 1)
            finally:
                conn.close()
            settings = _settings_for(ctx["customer_id"])
        items = [_message_summary(r, settings) for r in rows[:limit]]
        if unread:
            items = [m for m in items if m["unread"]]
        if flagged:
            items = [m for m in items if m["flagged"]]
        has_more = len(rows) > limit
        return ok_paginated(items, has_more=has_more)

    @mcp.tool(
        name="mail_get_message",
        title="Get Message",
        description="Get full details of a specific email message. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_get_message(
        message_id: Annotated[int, Field(description="ID of the message to retrieve")],
        mark_read: Annotated[
            bool | None, Field(description="Mark the message as read after retrieval")
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "mail", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import get_message

                row = get_message(conn, message_id)
                if not row:
                    return err("NOT_FOUND", "Message not found")
                d = _row_to_dict(row)
                if mark_read:
                    flags = _parse_flags(d.get("flags"))
                    if "\\Seen" not in flags:
                        flags.append("\\Seen")
                        from app.modules.mail.services.cache_db import update_flags

                        update_flags(conn, message_id, flags)
            finally:
                conn.close()
            if mark_read:
                try:
                    account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
                    from app.modules.mail.services.imap_client import (
                        safe_logout,
                        select_folder,
                        set_flag,
                    )

                    client = _imap_connect(account, domain, secret)
                    try:
                        select_folder(client, d.get("folder", "INBOX"))
                        set_flag(client, d.get("uid"), "\\Seen", add=True)
                    finally:
                        safe_logout(client)
                except Exception:
                    _mail_logger.warning(
                        "mark_read IMAP sync failed for message_id=%s uid=%s",
                        message_id,
                        d.get("uid"),
                        exc_info=True,
                    )
            settings = _settings_for(ctx["customer_id"])
        return ok(_message_detail(d, settings))

    @mcp.tool(
        name="mail_search",
        title="Search Messages",
        description="Search email messages by query string with optional filters. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_search(
        q: Annotated[
            str, Field(description="Search query string (matched against subject, from, to, body)")
        ],
        folder_id: Annotated[
            str | None, Field(description="Restrict search to a specific folder")
        ] = None,
        unread: Annotated[bool | None, Field(description="Filter to unread messages only")] = None,
        flagged: Annotated[
            bool | None, Field(description="Filter to flagged/starred messages only")
        ] = None,
        since: Annotated[
            str | None, Field(description="ISO 8601 datetime — only messages after this time")
        ] = None,
        until: Annotated[
            str | None, Field(description="ISO 8601 datetime — only messages before this time")
        ] = None,
        max_results: Annotated[
            int | None,
            Field(
                description="Maximum number of results to return (1–200, default 50)", ge=1, le=200
            ),
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "mail", account_id)
        limit = max_results or 50
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import search_local

                rows = search_local(conn, q, limit=limit)
            finally:
                conn.close()
            settings = _settings_for(ctx["customer_id"])
        items = [_message_summary(r, settings) for r in rows]
        return ok(items)

    @mcp.tool(
        name="mail_send",
        title="Send Email",
        description="Send an email message to one or more recipients.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_send(
        to: Annotated[list[str], Field(description="Recipient email addresses")],
        subject: Annotated[str, Field(description="Email subject line")],
        cc: Annotated[list[str] | None, Field(description="CC recipient email addresses")] = None,
        bcc: Annotated[list[str] | None, Field(description="BCC recipient email addresses")] = None,
        body_plain: Annotated[str | None, Field(description="Plain text body content")] = None,
        body_html: Annotated[str | None, Field(description="HTML body content")] = None,
        draft_id: Annotated[
            str | None, Field(description="ID of a draft to delete after sending")
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "mail", account_id)
        with flask_app.app_context():
            account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
            from app.modules.mail.services.send import send_message

            result = send_message(
                account,
                domain,
                secret,
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                body_plain=body_plain,
                body_html=body_html,
                draft_id=draft_id,
                get_cache_conn=lambda: _get_cache_conn(aid, dek, flask_app),
            )
        result.pop("sent_uid", None)
        from app.shared.ui_events import push_ui_event

        push_ui_event(ctx["customer_id"], "mail", "message_sent", {"account_id": aid})
        return ok(result)

    @mcp.tool(
        name="mail_move_message",
        title="Move Message",
        description="Move a message to a different folder. Refuses to move to Trash if the message is protected (locked or starred when protect-starred is on).",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_move_message(
        message_id: Annotated[int, Field(description="ID of the message to move")],
        folder_id: Annotated[
            str, Field(description="Destination folder ID (e.g. 'Archive', 'Trash')")
        ],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "mail", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import get_message

                row = get_message(conn, message_id)
                if not row:
                    return err("NOT_FOUND", "Message not found")
                d = _row_to_dict(row)
                if (folder_id or "").strip().lower() == "trash":
                    from app.modules.mail.services.protection import (
                        protected_delete_message,
                        protection_reason,
                    )
                    from app.shared.models.core import CustomerSettings

                    settings = CustomerSettings.query.filter_by(
                        customer_id=ctx["customer_id"]
                    ).first()
                    reason = protection_reason(_parse_flags(d.get("flags")), settings)
                    if reason:
                        return err("PROTECTED", protected_delete_message(reason))
            finally:
                conn.close()
            account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
            from app.modules.mail.services.imap_client import (
                move_message,
                safe_logout,
                select_folder,
            )

            client = _imap_connect(account, domain, secret)
            try:
                select_folder(client, d.get("folder", "INBOX"))
                move_message(client, d.get("uid"), folder_id)
                client.expunge()
            finally:
                safe_logout(client)
        from app.shared.ui_events import push_ui_event

        push_ui_event(
            ctx["customer_id"],
            "mail",
            "message_moved",
            {"account_id": aid, "message_id": message_id, "folder_id": folder_id},
        )
        return ok({"id": message_id, "moved_to": folder_id})

    @mcp.tool(
        name="mail_delete_message",
        title="Delete Message",
        description="Delete a message by moving it to the Trash folder. Refuses if the message is protected (locked or starred when protect-starred is on).",
        annotations=ToolAnnotations(
            readOnlyHint=False, openWorldHint=False, destructiveHint=True, idempotentHint=True
        ),
    )
    @resilient_tool
    async def mail_delete_message(
        message_id: Annotated[int, Field(description="ID of the message to delete")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "mail", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import delete_message_by_id, get_message

                row = get_message(conn, message_id)
                if not row:
                    return err("NOT_FOUND", "Message not found")
                d = _row_to_dict(row)
                from app.modules.mail.services.protection import (
                    protected_delete_message,
                    protection_reason,
                )
                from app.shared.models.core import CustomerSettings

                settings = CustomerSettings.query.filter_by(customer_id=ctx["customer_id"]).first()
                reason = protection_reason(_parse_flags(d.get("flags")), settings)
                if reason:
                    return err("PROTECTED", protected_delete_message(reason))
            finally:
                conn.close()
            account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
            from app.modules.mail.services.imap_client import (
                create_folder,
                list_folders,
                move_message,
                safe_logout,
                select_folder,
            )

            client = _imap_connect(account, domain, secret)
            try:
                existing = [f.lower() for f in list_folders(client)]
                if "trash" not in existing:
                    create_folder(client, "Trash")
                select_folder(client, d.get("folder", "INBOX"))
                move_message(client, d.get("uid"), "Trash")
                client.expunge()
            finally:
                safe_logout(client)
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                delete_message_by_id(conn, message_id)
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event

        push_ui_event(
            ctx["customer_id"],
            "mail",
            "message_deleted",
            {"account_id": aid, "message_id": message_id},
        )
        return ok({"id": message_id, "deleted": True})

    @mcp.tool(
        name="mail_update_flags",
        title="Update Message Flags",
        description="Update read, flagged, or locked status on a message.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_update_flags(
        message_id: Annotated[int, Field(description="ID of the message to update")],
        read: Annotated[
            bool | None, Field(description="Set read status (true = read, false = unread)")
        ] = None,
        flagged: Annotated[bool | None, Field(description="Set flagged/starred status")] = None,
        locked: Annotated[
            bool | None, Field(description="Set delete-protection lock ($Locked) on the message")
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "mail", account_id)
        from app.modules.mail.services.protection import LOCKED_KEYWORD

        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import get_message, update_flags

                row = get_message(conn, message_id)
                if not row:
                    return err("NOT_FOUND", "Message not found")
                d = _row_to_dict(row)
                flags = _parse_flags(d.get("flags"))
                if read is not None:
                    flags = _merge_flag(flags, "\\Seen", read)
                if flagged is not None:
                    flags = _merge_flag(flags, "\\Flagged", flagged)
                if locked is not None:
                    flags = _merge_flag(flags, LOCKED_KEYWORD, locked)
                update_flags(conn, message_id, flags)
            finally:
                conn.close()
            account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
            from app.modules.mail.services.imap_client import safe_logout, select_folder, set_flag

            client = _imap_connect(account, domain, secret)
            try:
                select_folder(client, d.get("folder", "INBOX"))
                if read is not None:
                    set_flag(client, d.get("uid"), "\\Seen", add=read)
                if flagged is not None:
                    set_flag(client, d.get("uid"), "\\Flagged", add=flagged)
                if locked is not None:
                    set_flag(client, d.get("uid"), LOCKED_KEYWORD, add=locked)
            except Exception:
                _mail_logger.warning(
                    "IMAP flag sync failed for message_id=%s uid=%s",
                    message_id,
                    d.get("uid"),
                    exc_info=True,
                )
            finally:
                safe_logout(client)
        from app.shared.ui_events import push_ui_event

        push_ui_event(
            ctx["customer_id"],
            "mail",
            "flags_updated",
            {"account_id": aid, "message_id": message_id},
        )
        return ok({"id": message_id, "flags": flags})

    @mcp.tool(
        name="mail_get_attachment",
        title="Get Attachment",
        description="Download an attachment from a message. Returns base64-encoded attachment data. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_get_attachment(
        message_id: Annotated[
            int, Field(description="ID of the message containing the attachment")
        ],
        attachment_id: Annotated[
            int, Field(description="Index/ID of the attachment within the message")
        ],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "mail", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import get_message

                row = get_message(conn, message_id)
                if not row:
                    return err("NOT_FOUND", "Message not found")
                d = _row_to_dict(row)
            finally:
                conn.close()
            account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
            from app.modules.mail.services.imap_client import (
                fetch_message,
                safe_logout,
                select_folder,
            )

            client = _imap_connect(account, domain, secret)
            try:
                select_folder(client, d.get("folder", "INBOX"))
                msg = fetch_message(client, d.get("uid"))
            finally:
                safe_logout(client)
        if not msg:
            return err("NOT_FOUND", "Message not available")
        idx = 0
        for part in msg.walk():
            if "attachment" not in part.get("Content-Disposition", ""):
                continue
            if idx == attachment_id:
                payload = part.get_payload(decode=True)
                if not isinstance(payload, bytes):
                    return err("UNSUPPORTED", "Could not decode attachment")
                filename = part.get_filename() or f"attachment_{attachment_id}"
                return ok(binary_response(payload, part.get_content_type(), filename))
            idx += 1
        return err("NOT_FOUND", "Attachment not found")

    @mcp.tool(
        name="mail_view_attachment",
        title="View Attachment",
        description="Convert a pandoc-supported attachment to HTML for viewing. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_view_attachment(
        message_id: Annotated[
            int, Field(description="ID of the message containing the attachment")
        ],
        attachment_id: Annotated[
            int, Field(description="Index/ID of the attachment within the message")
        ],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "mail", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import get_message

                row = get_message(conn, message_id)
                if not row:
                    return err("NOT_FOUND", "Message not found")
                d = _row_to_dict(row)
            finally:
                conn.close()
            account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
            from app.modules.mail.services.imap_client import (
                fetch_message,
                safe_logout,
                select_folder,
            )

            client = _imap_connect(account, domain, secret)
            try:
                select_folder(client, d.get("folder", "INBOX"))
                msg = fetch_message(client, d.get("uid"))
            finally:
                safe_logout(client)
        if not msg:
            return err("NOT_FOUND", "Message not available")
        idx = 0
        for part in msg.walk():
            if "attachment" not in part.get("Content-Disposition", ""):
                continue
            if idx == attachment_id:
                payload = part.get_payload(decode=True)
                if not isinstance(payload, bytes):
                    return err("UNSUPPORTED", "Could not decode attachment")
                filename = part.get_filename() or f"attachment_{attachment_id}"
                from app.shared.pandoc_formats import convert_to_html, get_attachment_actions

                actions = get_attachment_actions(filename)
                if not actions or not actions.get("view"):
                    return err("UNSUPPORTED", f"Cannot view file type: {filename}")
                html = convert_to_html(payload, actions.get("pandoc_reader", ""))
                if not html:
                    return err("CONVERSION_ERROR", "Failed to convert attachment to HTML")
                return ok({"filename": filename, "content_type": "text/html", "html_content": html})
            idx += 1
        return err("NOT_FOUND", "Attachment not found")

    @mcp.tool(
        name="mail_get_thread",
        title="Get Thread Messages",
        description="Get all messages in an email thread. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_get_thread(
        thread_id: Annotated[str, Field(description="Thread ID to retrieve messages for")],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "mail", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import list_thread_messages

                rows = list_thread_messages(conn, thread_id)
            finally:
                conn.close()
            settings = _settings_for(ctx["customer_id"])
        return ok([_message_detail(_row_to_dict(r), settings) for r in rows])

    @mcp.tool(
        name="mail_bulk_move",
        title="Bulk Move Messages",
        description="Move multiple messages to a destination folder. When moving to Trash, protected messages (locked or starred when protect-starred is on) are skipped and reported in the failed list with code PROTECTED.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_bulk_move(
        items: Annotated[
            list[BulkMessageIdItem],
            Field(description="Array of items to move, each with a message_id"),
        ],
        folder_id: Annotated[str, Field(description="Destination folder ID")],
        account_id: _AccId = None,
    ) -> str:
        if not items or len(items) > 100:
            return err("VALIDATION_ERROR", "Items must contain 1-100 entries")
        items = ensure_typed(items, BulkMessageIdItem)
        ctx, aid, dek = resolve_write(flask_app, "mail", account_id)
        succeeded = []
        failed: list[dict[str, Any]] = []
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import get_message
                from app.modules.mail.services.protection import (
                    protected_delete_message,
                    protection_reason,
                )
                from app.shared.models.core import CustomerSettings

                move_items = []
                trash_move = (folder_id or "").strip().lower() == "trash"
                settings = (
                    CustomerSettings.query.filter_by(customer_id=ctx["customer_id"]).first()
                    if trash_move
                    else None
                )
                for i, v in enumerate(items):
                    row = get_message(conn, v.message_id)
                    if not row:
                        failed.append({"index": i, "error": {"code": "NOT_FOUND"}})
                        continue
                    d = _row_to_dict(row)
                    if trash_move:
                        reason = protection_reason(_parse_flags(d.get("flags")), settings)
                        if reason:
                            failed.append(
                                {
                                    "message_id": v.message_id,
                                    "error": {
                                        "code": "PROTECTED",
                                        "message": protected_delete_message(reason),
                                    },
                                }
                            )
                            continue
                    move_items.append(
                        {"index": i, "uid": d.get("uid"), "folder": d.get("folder", "INBOX")}
                    )
            finally:
                conn.close()
            if move_items:
                try:
                    account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
                    from app.modules.mail.services.imap_client import (
                        move_message,
                        safe_logout,
                        select_folder,
                    )

                    client = _imap_connect(account, domain, secret)
                    try:
                        for mi in move_items:
                            try:
                                select_folder(client, mi["folder"])
                                move_message(client, mi["uid"], folder_id)
                                succeeded.append({"message_id": items[mi["index"]].message_id})
                            except Exception as exc:
                                failed.append(
                                    {
                                        "index": mi["index"],
                                        "error": {
                                            "code": "IMAP_ERROR",
                                            "message": f"Failed to move message: {exc}",
                                        },
                                    }
                                )
                        try:
                            client.expunge()
                        except Exception:
                            _mail_logger.debug("IMAP expunge failed after bulk move", exc_info=True)
                    finally:
                        safe_logout(client)
                except Exception as exc:
                    return err("IMAP_ERROR", str(exc))
        from app.shared.ui_events import push_ui_event

        push_ui_event(ctx["customer_id"], "mail", "messages_moved", {"account_id": aid})
        return ok({"succeeded": succeeded, "failed": failed})

    @mcp.tool(
        name="mail_bulk_delete",
        title="Bulk Delete Messages",
        description="Delete multiple messages by moving them to Trash.",
        annotations=ToolAnnotations(
            readOnlyHint=False, openWorldHint=False, destructiveHint=True, idempotentHint=True
        ),
    )
    @resilient_tool
    async def mail_bulk_delete(
        items: Annotated[
            list[BulkMessageIdItem],
            Field(description="Array of items to delete, each with a message_id"),
        ],
        account_id: _AccId = None,
    ) -> str:
        if not items or len(items) > 100:
            return err("VALIDATION_ERROR", "Items must contain 1-100 entries")
        items = ensure_typed(items, BulkMessageIdItem)
        ctx, aid, dek = resolve_write(flask_app, "mail", account_id)
        succeeded = []
        failed: list[dict[str, Any]] = []
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import get_message

                del_items = []
                from app.modules.mail.services.protection import (
                    protected_delete_message,
                    protection_reason,
                )
                from app.shared.models.core import CustomerSettings

                settings = CustomerSettings.query.filter_by(customer_id=ctx["customer_id"]).first()
                for i, v in enumerate(items):
                    row = get_message(conn, v.message_id)
                    if not row:
                        failed.append({"index": i, "error": {"code": "NOT_FOUND"}})
                        continue
                    d = _row_to_dict(row)
                    reason = protection_reason(_parse_flags(d.get("flags")), settings)
                    if reason:
                        failed.append(
                            {
                                "message_id": v.message_id,
                                "error": {
                                    "code": "PROTECTED",
                                    "message": protected_delete_message(reason),
                                },
                            }
                        )
                        continue
                    del_items.append(
                        {
                            "index": i,
                            "uid": d.get("uid"),
                            "folder": d.get("folder", "INBOX"),
                            "message_id": v.message_id,
                        }
                    )
            finally:
                conn.close()
            if del_items:
                try:
                    account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
                    from app.modules.mail.services.imap_client import (
                        create_folder,
                        list_folders,
                        move_message,
                        safe_logout,
                        select_folder,
                    )

                    client = _imap_connect(account, domain, secret)
                    try:
                        existing = [f.lower() for f in list_folders(client)]
                        if "trash" not in existing:
                            create_folder(client, "Trash")
                        for di in del_items:
                            try:
                                select_folder(client, di["folder"])
                                move_message(client, di["uid"], "Trash")
                                succeeded.append({"message_id": di["message_id"]})
                            except Exception as exc:
                                failed.append(
                                    {
                                        "index": di["index"],
                                        "error": {
                                            "code": "IMAP_ERROR",
                                            "message": f"Failed to delete message: {exc}",
                                        },
                                    }
                                )
                        try:
                            client.expunge()
                        except Exception:
                            _mail_logger.debug(
                                "IMAP expunge failed after bulk delete", exc_info=True
                            )
                    finally:
                        safe_logout(client)
                except Exception as exc:
                    return err("IMAP_ERROR", str(exc))
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import delete_message_by_id

                for s in succeeded:
                    try:
                        delete_message_by_id(conn, s["message_id"])
                    except Exception:
                        _mail_logger.warning(
                            "cache cleanup failed for message_id=%s after bulk delete",
                            s["message_id"],
                            exc_info=True,
                        )
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event

        push_ui_event(ctx["customer_id"], "mail", "messages_deleted", {"account_id": aid})
        return ok({"succeeded": succeeded, "failed": failed})

    @mcp.tool(
        name="mail_bulk_flag",
        title="Bulk Update Flags",
        description="Update read or flagged status on multiple messages.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_bulk_flag(
        items: Annotated[
            list[BulkFlagItem],
            Field(description="Array of items to update, each with message_id and flags dict"),
        ],
        account_id: _AccId = None,
    ) -> str:
        if not items or len(items) > 100:
            return err("VALIDATION_ERROR", "Items must contain 1-100 entries")
        items = ensure_typed(items, BulkFlagItem)
        ctx, aid, dek = resolve_write(flask_app, "mail", account_id)
        succeeded = []
        failed: list[dict[str, Any]] = []
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import get_message, update_flags

                for i, v in enumerate(items):
                    row = get_message(conn, v.message_id)
                    if not row:
                        failed.append({"index": i, "error": {"code": "NOT_FOUND"}})
                        continue
                    d = _row_to_dict(row)
                    flags = _parse_flags(d.get("flags"))
                    from app.modules.mail.services.protection import LOCKED_KEYWORD

                    for flag_name, add in v.flags.items():
                        if flag_name.lower() == "locked":
                            flags = _merge_flag(flags, LOCKED_KEYWORD, add)
                        else:
                            imap_flag = f"\\{flag_name.capitalize()}"
                            flags = _merge_flag(flags, imap_flag, add)
                    update_flags(conn, v.message_id, flags)
                    succeeded.append({"message_id": v.message_id})
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event

        push_ui_event(ctx["customer_id"], "mail", "flags_updated", {"account_id": aid})
        return ok({"succeeded": succeeded, "failed": failed})

    @mcp.tool(
        name="mail_save_draft",
        title="Save Email Draft",
        description="Save an email as a draft in the Drafts folder. Optionally replace an existing draft by providing its draft ID. Returns the saved draft with draft_id for future reference.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_save_draft(
        subject: Annotated[str, Field(description="Email subject line")] = "",
        to: Annotated[list[str] | None, Field(description="Recipient email addresses")] = None,
        cc: Annotated[list[str] | None, Field(description="CC recipient email addresses")] = None,
        bcc: Annotated[list[str] | None, Field(description="BCC recipient email addresses")] = None,
        body_plain: Annotated[str | None, Field(description="Plain text body content")] = None,
        body_html: Annotated[str | None, Field(description="HTML body content")] = None,
        draft_id: Annotated[
            str | None, Field(description="ID of an existing draft to replace")
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "mail", account_id)
        with flask_app.app_context():
            account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
            from_addr = account.email_address
            msg = MIMEMultipart("alternative")
            msg["From"] = from_addr
            if to:
                msg["To"] = ", ".join(to)
            if cc:
                msg["Cc"] = ", ".join(cc)
            msg["Subject"] = subject or "(no subject)"
            msg["Message-ID"] = f"<{__import__('uuid').uuid4()}@{domain.name}>"
            if body_plain:
                msg.attach(MIMEText(body_plain, "plain"))
            if body_html:
                msg.attach(MIMEText(body_html, "html"))
            if not body_plain and not body_html:
                msg.attach(MIMEText("", "plain"))
            msg_bytes = msg.as_bytes()
            from app.modules.mail.services.imap_client import (
                append_message,
                create_folder,
                delete_message_by_uid,
                parse_append_uid,
                safe_logout,
                select_folder,
            )

            imap = _imap_connect(account, domain, secret)
            try:
                if draft_id:
                    try:
                        select_folder(imap, "Drafts")
                        delete_message_by_uid(imap, str(draft_id))
                    except Exception:
                        _mail_logger.debug(
                            "old draft deletion failed during draft save, draft_id=%s",
                            draft_id,
                            exc_info=True,
                        )
                try:
                    select_folder(imap, "Drafts")
                    _, append_data = append_message(imap, "Drafts", msg_bytes, flags=["\\Draft"])
                except Exception:
                    create_folder(imap, "Drafts")
                    select_folder(imap, "Drafts")
                    _, append_data = append_message(imap, "Drafts", msg_bytes, flags=["\\Draft"])
                draft_uid = parse_append_uid(append_data)
            finally:
                safe_logout(imap)
            if draft_id:
                try:
                    imap2 = _imap_connect(account, domain, secret)
                    try:
                        select_folder(imap2, "Drafts")
                        delete_message_by_uid(imap2, str(draft_id))
                    finally:
                        safe_logout(imap2)
                except Exception:
                    _mail_logger.warning(
                        "draft deletion from IMAP failed after send, draft_id=%s",
                        draft_id,
                        exc_info=True,
                    )
                with flask_app.app_context():
                    try:
                        conn = _get_cache_conn(aid, dek, flask_app)
                        from app.modules.mail.services.cache_db import delete_messages_by_uids

                        delete_messages_by_uids(conn, "Drafts", [str(draft_id)])
                        conn.close()
                    except Exception:
                        _mail_logger.warning(
                            "draft cache cleanup failed after send, draft_id=%s",
                            draft_id,
                            exc_info=True,
                        )
        from app.shared.ui_events import push_ui_event

        push_ui_event(ctx["customer_id"], "mail", "draft_saved", {"account_id": aid})
        return ok(
            {
                "status": "draft",
                "draft_uid": draft_uid,
                "draft_id": draft_uid,
                "message_id": msg["Message-ID"],
            }
        )

    @mcp.tool(
        name="mail_delete_draft",
        title="Delete Email Draft",
        description="Delete a draft email from the Drafts folder by its draft ID.",
        annotations=ToolAnnotations(
            readOnlyHint=False, openWorldHint=False, destructiveHint=True, idempotentHint=True
        ),
    )
    @resilient_tool
    async def mail_delete_draft(
        draft_uid: Annotated[
            str, Field(description="Draft UID returned by mail_save_draft.draft_uid")
        ],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_write(flask_app, "mail", account_id)
        with flask_app.app_context():
            account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
            from app.modules.mail.services.imap_client import (
                delete_message_by_uid,
                safe_logout,
                select_folder,
            )

            client = _imap_connect(account, domain, secret)
            try:
                select_folder(client, "Drafts")
                delete_message_by_uid(client, str(draft_uid))
            finally:
                safe_logout(client)
        from app.shared.ui_events import push_ui_event

        push_ui_event(ctx["customer_id"], "mail", "draft_deleted", {"account_id": aid})
        return ok({"status": "deleted", "draft_uid": draft_uid})

    @mcp.tool(
        name="mail_get_raw_message",
        title="Get Raw Message",
        description="Get the raw RFC 822 source of an email message as plain text. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_get_raw_message(
        message_id: Annotated[
            int, Field(description="ID of the message to retrieve raw source for")
        ],
        account_id: _AccId = None,
    ) -> str:
        ctx, aid, dek = resolve_read(flask_app, "mail", account_id)
        with flask_app.app_context():
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                from app.modules.mail.services.cache_db import get_message

                row = get_message(conn, message_id)
                if not row:
                    return err("NOT_FOUND", "Message not found")
                d = _row_to_dict(row)
            finally:
                conn.close()
            account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
            from app.modules.mail.services.imap_client import (
                fetch_raw_message,
                safe_logout,
                select_folder,
            )

            client = _imap_connect(account, domain, secret)
            try:
                select_folder(client, d.get("folder", "INBOX"))
                raw = fetch_raw_message(client, d.get("uid"))
            finally:
                safe_logout(client)
        if raw is None:
            return err("NOT_FOUND", "Raw message not available")
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        return ok({"mime_type": "message/rfc822", "data": text, "filename": "message.eml"})

    @mcp.tool(
        name="mail_create_folder",
        title="Create Mail Folder",
        description="Create a new mail folder (IMAP CREATE). Idempotent: creating an existing mailbox returns success with created=false. An optional parent nests the folder using the server hierarchy delimiter.",
        annotations=ToolAnnotations(
            readOnlyHint=False, openWorldHint=False, destructiveHint=False, idempotentHint=True
        ),
    )
    @resilient_tool
    async def mail_create_folder(
        name: Annotated[str, Field(description="Mailbox/folder name to create")],
        parent: Annotated[
            str | None, Field(description="Optional parent folder for nesting")
        ] = None,
        account_id: _AccId = None,
    ) -> str:
        folder_name = (name or "").strip()
        if not folder_name:
            return err("VALIDATION_ERROR", "'name' is required")
        parent_name = (parent or "").strip() or None
        ctx, aid, dek = resolve_write(flask_app, "mail", account_id)
        import imaplib as _imaplib

        with flask_app.app_context():
            account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
            from app.modules.mail.services.cache_db import upsert_folder
            from app.modules.mail.services.imap_client import (
                create_folder as imap_create_folder,
            )
            from app.modules.mail.services.imap_client import (
                encode_mailbox_name,
                get_folder_delimiter,
                list_folders,
                safe_logout,
            )

            client = _imap_connect(account, domain, secret)
            full_name = folder_name
            try:
                existing = [f.lower() for f in list_folders(client)]
                if parent_name:
                    delim = get_folder_delimiter(client)
                    full_name = f"{parent_name}{delim}{folder_name}"
                created = False
                if full_name.lower() not in existing:
                    try:
                        status, _ = imap_create_folder(client, encode_mailbox_name(full_name))
                        created = status == "OK"
                    except _imaplib.IMAP4.error as exc:
                        return err("IMAP_ERROR", f"Failed to create folder: {exc}")
            finally:
                safe_logout(client)
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                upsert_folder(conn, full_name, 0)
            finally:
                conn.close()
            _enqueue_sync(flask_app, aid, folder=full_name, reason="folder_created", priority=5)
        from app.shared.ui_events import push_ui_event

        push_ui_event(
            ctx["customer_id"], "mail", "folder_created", {"account_id": aid, "folder": full_name}
        )
        return ok({"id": full_name, "name": full_name, "created": created})

    @mcp.tool(
        name="mail_rename_folder",
        title="Rename Mail Folder",
        description="Rename a mail folder (IMAP RENAME). System folders (INBOX, Sent, Drafts, Trash, Junk, Bookings) cannot be renamed.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def mail_rename_folder(
        folder_id: Annotated[str, Field(description="Current folder name")],
        name: Annotated[str, Field(description="New folder name")],
        account_id: _AccId = None,
    ) -> str:
        old_name = (folder_id or "").strip()
        new_name = (name or "").strip()
        if not new_name:
            return err("VALIDATION_ERROR", "'name' is required")
        from app.modules.mail.services.protection import is_system_folder

        if is_system_folder(old_name):
            return err("PROTECTED", "System folders cannot be renamed.")
        ctx, aid, dek = resolve_write(flask_app, "mail", account_id)
        import imaplib as _imaplib

        with flask_app.app_context():
            account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
            from app.modules.mail.services.cache_db import rename_folder_in_cache
            from app.modules.mail.services.imap_client import encode_mailbox_name, safe_logout
            from app.modules.mail.services.imap_client import rename_folder as imap_rename_folder

            client = _imap_connect(account, domain, secret)
            try:
                try:
                    status, _ = imap_rename_folder(
                        client, encode_mailbox_name(old_name), encode_mailbox_name(new_name)
                    )
                    if status != "OK":
                        return err("IMAP_ERROR", "Failed to rename folder")
                except _imaplib.IMAP4.error as exc:
                    return err("IMAP_ERROR", f"Failed to rename folder: {exc}")
            finally:
                safe_logout(client)
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                rename_folder_in_cache(conn, old_name, new_name)
            finally:
                conn.close()
            _enqueue_sync(flask_app, aid, folder=new_name, reason="folder_renamed", priority=5)
        from app.shared.ui_events import push_ui_event

        push_ui_event(
            ctx["customer_id"],
            "mail",
            "folder_renamed",
            {"account_id": aid, "old_folder": old_name, "new_folder": new_name},
        )
        return ok({"id": new_name, "name": new_name})

    @mcp.tool(
        name="mail_delete_folder",
        title="Delete Mail Folder",
        description="Delete a mail folder (IMAP DELETE). System folders and folders marked protected cannot be deleted.",
        annotations=ToolAnnotations(
            readOnlyHint=False, openWorldHint=False, destructiveHint=True, idempotentHint=True
        ),
    )
    @resilient_tool
    async def mail_delete_folder(
        folder_id: Annotated[str, Field(description="Folder name to delete")],
        account_id: _AccId = None,
    ) -> str:
        folder = (folder_id or "").strip()
        ctx, aid, dek = resolve_write(flask_app, "mail", account_id)
        import imaplib as _imaplib

        with flask_app.app_context():
            from app.modules.mail.services.protection import folder_is_protected
            from app.shared.models.core import CustomerSettings

            settings = CustomerSettings.query.filter_by(customer_id=ctx["customer_id"]).first()
            if folder_is_protected(settings, folder):
                return err("PROTECTED", "This folder is protected and cannot be deleted.")
            account, domain, secret = _get_account_and_secret(aid, dek, flask_app)
            from app.modules.mail.services.cache_db import delete_folder_in_cache
            from app.modules.mail.services.imap_client import delete_folder as imap_delete_folder
            from app.modules.mail.services.imap_client import encode_mailbox_name, safe_logout

            client = _imap_connect(account, domain, secret)
            try:
                try:
                    status, _ = imap_delete_folder(client, encode_mailbox_name(folder))
                    if status != "OK":
                        return err("IMAP_ERROR", "Failed to delete folder")
                except _imaplib.IMAP4.error as exc:
                    return err("IMAP_ERROR", f"Failed to delete folder: {exc}")
            finally:
                safe_logout(client)
            conn = _get_cache_conn(aid, dek, flask_app)
            try:
                delete_folder_in_cache(conn, folder)
            finally:
                conn.close()
        from app.shared.ui_events import push_ui_event

        push_ui_event(
            ctx["customer_id"], "mail", "folder_deleted", {"account_id": aid, "folder": folder}
        )
        return ok({"id": folder, "deleted": True})
