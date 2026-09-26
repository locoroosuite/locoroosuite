"""Chat module JSON API (session-authenticated, under /app/chat/api)."""

from __future__ import annotations

import json
import logging

from flask import Response, jsonify, request
from flask_babel import _

from app.modules.chat.controllers.helpers import (
    ChatApiError,
    chat_bp,
    chat_context,
    own_matrix_id,
    require_customer,
)
from app.modules.chat.services import cache_db
from app.modules.chat.services.cache_db import _decorate_room_display_name
from app.modules.chat.services.matrix import MatrixError
from app.modules.chat.services.provisioning import ensure_matrix_user
from app.modules.chat.services.sync import process_backfill, process_sync, sync_lock
from app.shared.db import db
from app.shared.models.core import CustomerAccount, Domain

logger = logging.getLogger(__name__)

_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _matrix_to_chat_error(exc: MatrixError, account_id: int) -> ChatApiError:
    logger.warning("chat matrix error account_id=%s code=%s", account_id, exc.code)
    status = 404 if getattr(exc, "status", None) == 404 else 502
    return ChatApiError(exc.code, exc.message, status)


def _room_dict(room: dict) -> dict:
    return {
        "room_id": room["room_id"],
        "name": room.get("name"),
        "display_name": room.get("display_name"),
        "topic": room.get("topic"),
        "is_direct": bool(room.get("is_direct")),
        "is_public": bool(room.get("is_public")),
        "membership": room.get("membership"),
        "notification_count": room.get("notification_count", 0),
        "highlight_count": room.get("highlight_count", 0),
        "member_count": room.get("member_count", 0),
        "last_event_ts": room.get("last_event_ts"),
        "last_event_preview": room.get("last_event_preview"),
    }


def _own_server_name(creds) -> str:
    own_id = creds.get("matrix_user_id") or ""
    return own_id.split(":", 1)[1] if ":" in own_id else ""


def _resolve_peer_by_email(account, domain, email: str, server_name: str):
    """Map an exact suite email to its Matrix identity, provisioning if missing.

    Returns (peer_account, matrix_user_id). Raises ChatApiError for unknown
    accounts or homeserver mismatches.
    """
    peer = CustomerAccount.query.filter_by(email_address=email, is_active=True).first()
    if not peer:
        raise ChatApiError(
            "NO_SUCH_ACCOUNT",
            _(
                "There is no account with the email address %(email)s. "
                "Check the address or create the account first.",
                email=email,
            ),
            404,
        )
    peer_domain = db.session.get(Domain, peer.domain_id)
    if not peer_domain or not peer_domain.matrix_host:
        raise ChatApiError(
            "CHAT_PEER_NOT_CONFIGURED",
            _(
                "Chat is not configured for the domain of %(email)s. "
                "An administrator must enable chat for that domain first.",
                email=email,
            ),
            400,
        )
    if (peer_domain.matrix_host, peer_domain.matrix_port) != (
        domain.matrix_host,
        domain.matrix_port,
    ):
        raise ChatApiError(
            "CHAT_CROSS_SERVER",
            _(
                "%(email)s belongs to a domain on a different chat server. "
                "Conversations across chat servers are not supported yet.",
                email=email,
            ),
            400,
        )
    try:
        result = ensure_matrix_user(peer, peer_domain, server_name=server_name)
    except MatrixError as exc:
        raise ChatApiError(exc.code, exc.message, 502) from exc
    return peer, result["matrix_user_id"]


@chat_bp.route("/chat/api/dm", methods=["POST"])
@require_customer
def create_dm():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise ChatApiError("VALIDATION", _("Enter the exact email address of the person"))
    account, _user_id, conn, client, creds = chat_context()
    try:
        domain = db.session.get(Domain, account.domain_id)
        _peer, peer_matrix_id = _resolve_peer_by_email(
            account, domain, email, _own_server_name(creds)
        )
        resp = client.create_room(is_direct=True, invite=[peer_matrix_id])
        room_id = resp.get("room_id")
        if not room_id:
            raise ChatApiError("MATRIX_BAD_RESPONSE", _("Chat server returned no room id"), 502)
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        cache_db.upsert_room(conn, room_id, is_direct=True, membership="join")
        display_name = client.get_displayname(peer_matrix_id) or email.split("@", 1)[0]
        cache_db.upsert_member(
            conn, room_id, peer_matrix_id, displayname=display_name, membership="invite"
        )
        cache_db.upsert_member(conn, room_id, own_id, membership="join")
        cache_db.update_room_fields(conn, room_id, {"member_count": 2})
        room = cache_db.get_room(conn, room_id) or {"room_id": room_id}
        _decorate_room_display_name(conn, room, own_id)
        return (
            jsonify(
                {
                    "room": _room_dict(room),
                    "peer": {"email": email, "matrix_user_id": peer_matrix_id},
                }
            ),
            201,
        )
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/state", methods=["GET"])
@require_customer
def state():
    _account, _user_id, conn, _client, creds = chat_context()
    try:
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        rooms = [_room_dict(r) for r in cache_db.list_rooms(conn, own_id)]
        return jsonify({"identity": {"matrix_user_id": own_id}, "rooms": rooms})
    finally:
        conn.close()


@chat_bp.route("/chat/api/sync-now", methods=["POST"])
@require_customer
def sync_now():
    account, user_id, conn, client, creds = chat_context()
    try:
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        since_state = cache_db.get_sync_state(conn)
        since = since_state.get("since_token") if since_state else None
        resp = client.sync(since=since, timeout_ms=0)
        with sync_lock(user_id):
            changes = process_sync(conn, own_id, resp)
        rooms = [_room_dict(r) for r in cache_db.list_rooms(conn, own_id)]
        return jsonify({"rooms": rooms, "messages": changes.get("messages", {})})
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/rooms/<room_id>/messages", methods=["GET"])
@require_customer
def room_messages(room_id: str):
    account, user_id, conn, client, creds = chat_context()
    try:
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        room = cache_db.get_room(conn, room_id)
        if not room:
            raise ChatApiError("ROOM_NOT_FOUND", _("Room not found"), 404)
        limit = min(int(request.args.get("limit", 50) or 50), 200)
        before_ts = request.args.get("before_ts", type=int)
        rows = cache_db.list_messages(conn, room_id, limit=limit, before_ts=before_ts)
        if not rows and room.get("prev_batch"):
            resp = client.room_messages(room_id, room["prev_batch"], limit=limit)
            with sync_lock(user_id):
                process_backfill(conn, own_id, room_id, resp)
            rows = cache_db.list_messages(conn, room_id, limit=limit)
        reactions = cache_db.list_reactions(conn, room_id)
        messages = [
            cache_db.message_to_api(r, reactions.get(r["event_id"]), own_user_id=own_id)
            for r in rows
        ]
        return jsonify({"messages": messages, "has_more": len(rows) >= limit})
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/rooms", methods=["POST"])
@require_customer
def create_room():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    topic = (data.get("topic") or "").strip() or None
    invite = data.get("invite") or []
    is_direct = bool(data.get("is_direct"))
    is_public = bool(data.get("is_public"))
    if is_direct and not invite:
        raise ChatApiError("VALIDATION", _("A direct message needs at least one invitee"))
    if not is_direct and not name:
        raise ChatApiError("VALIDATION", _("Room name is required"))
    if not isinstance(invite, list) or not all(isinstance(u, str) for u in invite):
        raise ChatApiError("VALIDATION", "invite must be a list of user ids")
    account, _user_id, conn, client, creds = chat_context()
    try:
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        resp = client.create_room(
            name=None if is_direct else name,
            topic=topic,
            is_public=is_public,
            is_direct=is_direct,
            invite=invite or None,
        )
        room_id = resp.get("room_id")
        if not room_id:
            raise ChatApiError("MATRIX_BAD_RESPONSE", _("Chat server returned no room id"), 502)
        cache_db.upsert_room(
            conn,
            room_id,
            name=None if is_direct else name,
            topic=topic,
            is_direct=is_direct,
            is_public=is_public,
            membership="join",
        )
        for user in invite:
            cache_db.upsert_member(
                conn, room_id, user, membership="invite" if user != own_id else "join"
            )
        cache_db.update_room_fields(conn, room_id, {"member_count": len(invite) + 1})
        room = cache_db.get_room(conn, room_id) or {"room_id": room_id}
        _decorate_room_display_name(conn, room, own_id)
        return jsonify({"room": _room_dict(room)}), 201
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/rooms/<room_id>/join", methods=["POST"])
@require_customer
def join_room(room_id: str):
    account, _user_id, conn, client, _creds = chat_context()
    try:
        client.join_room(room_id)
        cache_db.upsert_room(conn, room_id, membership="join")
        return jsonify({"ok": True})
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/rooms/<room_id>/leave", methods=["POST"])
@require_customer
def leave_room(room_id: str):
    account, _user_id, conn, client, _creds = chat_context()
    try:
        client.leave_room(room_id)
        try:
            client.forget_room(room_id)
        except MatrixError:
            logger.info("forget room %s failed (non-fatal)", room_id)
        cache_db.delete_room(conn, room_id)
        return jsonify({"ok": True})
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/rooms/<room_id>/invite", methods=["POST"])
@require_customer
def invite_to_room(room_id: str):
    data = request.get_json(silent=True) or {}
    user_to_invite = (data.get("user_id") or "").strip()
    invite_email = (data.get("email") or "").strip().lower()
    if not user_to_invite and not invite_email:
        raise ChatApiError("VALIDATION", _("email (or user_id) is required"))
    account, _user_id, conn, client, creds = chat_context()
    try:
        if invite_email:
            domain = db.session.get(Domain, account.domain_id)
            _peer, user_to_invite = _resolve_peer_by_email(
                account, domain, invite_email, _own_server_name(creds)
            )
        client.invite(room_id, user_to_invite)
        cache_db.upsert_member(conn, room_id, user_to_invite, membership="invite")
        return jsonify({"ok": True, "user_id": user_to_invite})
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/rooms/<room_id>/send", methods=["POST"])
@require_customer
def send_message(room_id: str):
    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    formatted = (data.get("formatted_body") or "").strip() or None
    if not body:
        raise ChatApiError("VALIDATION", _("Message body is required"))
    account, _user_id, conn, client, creds = chat_context()
    try:
        room = cache_db.get_room(conn, room_id)
        if not room or room.get("membership") != "join":
            raise ChatApiError("ROOM_NOT_FOUND", _("Room not found"), 404)
        resp = client.send_message(room_id, body, formatted)
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        content: dict = {"msgtype": "m.text", "body": body}
        if formatted:
            content["formatted_body"] = formatted
        cache_db.insert_message(
            conn,
            event_id=resp.get("event_id", ""),
            room_id=room_id,
            sender=own_id,
            type="m.room.message",
            body=body,
            content_json=json.dumps(content),
            origin_server_ts=resp.get("ts", 0) or 0,
        )
        return jsonify({"event_id": resp.get("event_id")}), 201
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/rooms/<room_id>/upload", methods=["POST"])
@require_customer
def upload_to_room(room_id: str):
    file = request.files.get("file")
    if file is None:
        raise ChatApiError("VALIDATION", _("file is required"))
    data = file.read()
    if not data:
        raise ChatApiError("VALIDATION", _("Uploaded file is empty"))
    if len(data) > _MAX_UPLOAD_BYTES:
        raise ChatApiError("VALIDATION", _("File exceeds the 50 MB limit"), 413)
    account, _user_id, conn, client, creds = chat_context()
    try:
        room = cache_db.get_room(conn, room_id)
        if not room or room.get("membership") != "join":
            raise ChatApiError("ROOM_NOT_FOUND", _("Room not found"), 404)
        mimetype = file.mimetype or "application/octet-stream"
        upload = client.upload_media(data, file.filename or "upload", mimetype)
        content_uri = upload.get("content_uri")
        if not content_uri:
            raise ChatApiError("MATRIX_BAD_RESPONSE", _("Chat server returned no content URI"), 502)
        msgtype = _msgtype_for_mimetype(mimetype)
        body = file.filename or "upload"
        content = {
            "msgtype": msgtype,
            "body": body,
            "url": content_uri,
            "info": {"mimetype": mimetype, "size": len(data)},
        }
        resp = client.send_event(room_id, "m.room.message", content)
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        cache_db.insert_message(
            conn,
            event_id=resp.get("event_id", ""),
            room_id=room_id,
            sender=own_id,
            type="m.room.message",
            body=body,
            content_json=json.dumps(content),
            origin_server_ts=resp.get("ts", 0) or 0,
        )
        return jsonify({"event_id": resp.get("event_id"), "content_uri": content_uri}), 201
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


def _msgtype_for_mimetype(mimetype: str) -> str:
    if mimetype.startswith("image/"):
        return "m.image"
    if mimetype.startswith("video/"):
        return "m.video"
    if mimetype.startswith("audio/"):
        return "m.audio"
    return "m.file"


@chat_bp.route("/chat/api/rooms/<room_id>/members", methods=["GET"])
@require_customer
def room_members(room_id: str):
    _account, _user_id, conn, _client, _creds = chat_context()
    try:
        if not cache_db.get_room(conn, room_id):
            raise ChatApiError("ROOM_NOT_FOUND", _("Room not found"), 404)
        members = cache_db.list_members(conn, room_id)
        return jsonify({"members": members})
    finally:
        conn.close()


@chat_bp.route("/chat/api/rooms/<room_id>/read", methods=["POST"])
@require_customer
def mark_read(room_id: str):
    data = request.get_json(silent=True) or {}
    event_id = (data.get("event_id") or "").strip()
    if not event_id:
        raise ChatApiError("VALIDATION", _("event_id is required"))
    _account, _user_id, conn, client, _creds = chat_context()
    try:
        try:
            client.send_receipt(room_id, event_id)
            client.set_read_marker(room_id, event_id)
        except MatrixError as exc:
            logger.info("read marker push failed room=%s code=%s (non-fatal)", room_id, exc.code)
        cache_db.set_read_state(conn, room_id, read_event_id=event_id, fully_read_event_id=event_id)
        cache_db.update_room_fields(conn, room_id, {"notification_count": 0, "highlight_count": 0})
        db.session.commit()
        return jsonify({"ok": True})
    finally:
        conn.close()


@chat_bp.route("/chat/api/rooms/<room_id>/typing", methods=["POST"])
@require_customer
def set_typing(room_id: str):
    data = request.get_json(silent=True) or {}
    is_typing = bool(data.get("typing"))
    account, _user_id, conn, client, creds = chat_context()
    try:
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        client.typing(room_id, own_id, is_typing)
        return jsonify({"ok": True})
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/rooms/<room_id>/settings", methods=["PUT"])
@require_customer
def update_room_settings(room_id: str):
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    topic = (data.get("topic") or "").strip()
    if not name and not topic:
        raise ChatApiError("VALIDATION", _("Nothing to update"))
    account, _user_id, conn, client, _creds = chat_context()
    try:
        if name:
            client.set_room_name(room_id, name)
            cache_db.update_room_fields(conn, room_id, {"name": name})
        if topic:
            client.set_room_topic(room_id, topic)
            cache_db.update_room_fields(conn, room_id, {"topic": topic})
        return jsonify({"ok": True})
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/messages/<event_id>/react", methods=["POST"])
@require_customer
def react_to_message(event_id: str):
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    if not key:
        raise ChatApiError("VALIDATION", _("key (emoji) is required"))
    account, _user_id, conn, client, creds = chat_context()
    try:
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        row = cache_db.get_message(conn, event_id)
        if not row:
            raise ChatApiError("MESSAGE_NOT_FOUND", _("Message not found"), 404)
        reactions = cache_db.list_reactions(conn, row["room_id"]).get(event_id, [])
        mine = next((r for r in reactions if r["sender"] == own_id and r["key"] == key), None)
        if mine:
            mine_row = conn.execute(
                "SELECT event_id FROM chat_messages WHERE room_id = ? AND type = 'm.reaction' "
                "AND relates_to = ? AND body = ? AND sender = ? LIMIT 1",
                (row["room_id"], event_id, key, own_id),
            ).fetchone()
            if mine_row:
                client.redact(row["room_id"], mine_row["event_id"])
                cache_db.delete_message(conn, mine_row["event_id"])
                return jsonify({"ok": True, "removed": True})
        resp = client.send_reaction(row["room_id"], event_id, key)
        cache_db.insert_message(
            conn,
            event_id=resp.get("event_id", ""),
            room_id=row["room_id"],
            sender=own_id,
            type="m.reaction",
            body=key,
            content_json=json.dumps(
                {"m.relates_to": {"rel_type": "m.annotation", "event_id": event_id, "key": key}}
            ),
            relates_to=event_id,
            rel_type="m.annotation",
            origin_server_ts=resp.get("ts", 0) or 0,
        )
        return jsonify({"ok": True, "removed": False}), 201
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/messages/<event_id>/edit", methods=["POST"])
@require_customer
def edit_message(event_id: str):
    data = request.get_json(silent=True) or {}
    new_body = (data.get("body") or "").strip()
    if not new_body:
        raise ChatApiError("VALIDATION", "body is required")
    account, _user_id, conn, client, creds = chat_context()
    try:
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        row = cache_db.get_message(conn, event_id)
        if not row or row["type"] != "m.room.message":
            raise ChatApiError("MESSAGE_NOT_FOUND", _("Message not found"), 404)
        if row["sender"] != own_id:
            raise ChatApiError("FORBIDDEN", _("Only your own messages can be edited"), 403)
        resp = client.send_edit(row["room_id"], event_id, new_body)
        cache_db.update_message_content(
            conn, event_id, json.dumps({"msgtype": "m.text", "body": new_body}), new_body
        )
        return jsonify({"event_id": resp.get("event_id")})
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/messages/<event_id>/redact", methods=["POST"])
@require_customer
def redact_message(event_id: str):
    account, _user_id, conn, client, _creds = chat_context()
    try:
        row = cache_db.get_message(conn, event_id)
        if not row:
            raise ChatApiError("MESSAGE_NOT_FOUND", _("Message not found"), 404)
        client.redact(row["room_id"], event_id)
        if row["type"] == "m.reaction":
            cache_db.delete_message(conn, event_id)
        else:
            cache_db.mark_redacted(conn, event_id)
        return jsonify({"ok": True})
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/users", methods=["GET"])
@require_customer
def search_users():
    term = (request.args.get("q") or "").strip()
    if len(term) < 2:
        return jsonify({"users": []})
    account, _user_id, conn, client, creds = chat_context()
    try:
        own_id = creds.get("matrix_user_id")
        resp = client.user_directory_search(term, limit=10)
        users = [
            {
                "user_id": u.get("user_id"),
                "display_name": u.get("display_name"),
                "avatar_url": u.get("avatar_url"),
            }
            for u in resp.get("results", [])
            if u.get("user_id") != own_id
        ]
        return jsonify({"users": users})
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/media/<server_name>/<media_id>", methods=["GET"])
@require_customer
def download_media(server_name: str, media_id: str):
    account, _user_id, conn, client, _creds = chat_context()
    try:
        upstream = client.download_media(server_name, media_id)
        content_type = upstream.headers.get("Content-Type", "application/octet-stream")
        return Response(
            upstream.iter_content(chunk_size=64 * 1024),
            content_type=content_type,
            headers={"Content-Security-Policy": "default-src 'none'; sandbox"},
        )
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/thumbnail/<server_name>/<media_id>", methods=["GET"])
@require_customer
def download_thumbnail(server_name: str, media_id: str):
    account, _user_id, conn, client, _creds = chat_context()
    try:
        upstream = client.thumbnail_media(
            server_name,
            media_id,
            request.args.get("w", 320, type=int),
            request.args.get("h", 240, type=int),
        )
        content_type = upstream.headers.get("Content-Type", "image/jpeg")
        return Response(upstream.iter_content(chunk_size=64 * 1024), content_type=content_type)
    except MatrixError as exc:
        raise _matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()
