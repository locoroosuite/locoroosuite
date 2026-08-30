from __future__ import annotations

import json
import logging
from functools import wraps

from flask import g

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
from app.api.schemas.chat import (
    ChatEventIdPath,
    ChatEventIdResponse,
    ChatMessageListResponse,
    ChatRoomIdPath,
    ChatRoomListResponse,
    ChatRoomResponse,
    CreateChatRoomBody,
    EditChatMessageBody,
    InviteChatUserBody,
    ListMessagesQuery,
    ReactChatMessageBody,
    ReadChatRoomBody,
    SendChatMessageBody,
)
from app.api.schemas.common import AccountIdQuery, EmptyResponse, ErrorResponse
from app.modules.chat.services import cache_db
from app.modules.chat.services.cache import get_cache_path
from app.modules.chat.services.cache_db import _decorate_room_display_name
from app.modules.chat.services.matrix import MatrixClient, MatrixError, homeserver_url
from app.shared.db import db
from app.shared.models.core import CustomerAccount, Domain

logger = logging.getLogger(__name__)

bp = create_api_blueprint("chat", "Chat (Matrix) management")


def _handle_api_errors(f):
    """Convert raised ApiError into the structured error response (repo convention)."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ApiError as e:
            return api_error(e.code, e.message, e.status)

    return wrapper


def _room_dict(room: dict) -> dict:
    return {
        "room_id": room["room_id"],
        "name": room.get("name"),
        "display_name": room.get("display_name", ""),
        "topic": room.get("topic"),
        "is_direct": bool(room.get("is_direct")),
        "is_public": bool(room.get("is_public")),
        "membership": room.get("membership", "join"),
        "notification_count": room.get("notification_count", 0),
        "highlight_count": room.get("highlight_count", 0),
        "member_count": room.get("member_count", 0),
        "last_event_ts": room.get("last_event_ts"),
        "last_event_preview": room.get("last_event_preview"),
    }


def _get_account_and_client(account_id: int, dek: str):
    account = db.session.get(CustomerAccount, account_id)
    if not account:
        raise ApiError("NOT_FOUND", "Account not found", 404)
    domain = db.session.get(Domain, account.domain_id)
    if not domain:
        raise ApiError("NOT_FOUND", "Account domain not found", 404)
    conn = cache_db.open_cache(get_cache_path(account), dek)
    creds = cache_db.get_credentials(conn)
    if not creds:
        conn.close()
        raise ApiError(
            "CHAT_NOT_PROVISIONED",
            "Chat is not set up for this account yet. Open the Chat page in the web app once to provision it.",
            409,
        )
    try:
        base_url = homeserver_url(domain)
    except MatrixError as exc:
        conn.close()
        raise ApiError(
            "MATRIX_NOT_CONFIGURED",
            "No Matrix homeserver configured for this domain. Ask the administrator to set it up.",
            503,
        ) from exc
    client = MatrixClient(base_url, creds["access_token"], creds["matrix_user_id"])
    return account, conn, client, creds["matrix_user_id"]


def _matrix_error(exc: MatrixError) -> ApiError:
    logger.warning("chat api matrix error code=%s status=%s", exc.code, exc.status)
    status = 502
    if getattr(exc, "status", None) == 404:
        status = 404
    return ApiError(exc.code, exc.message, status)


@bp.get(
    "/chat/rooms",
    summary="List chat rooms",
    description="Returns all cached chat rooms for the account. Requires `chat:read` scope.",
    responses={"200": ChatRoomListResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["chat:read"])
@require_scope("chat", "read")
@_handle_api_errors
def api_list_rooms(query: AccountIdQuery):
    account_id = get_api_account_id()
    _account, conn, _client, own_id = _get_account_and_client(account_id, g.api_context["dek"])
    try:
        rooms = [_room_dict(r) for r in cache_db.list_rooms(conn, own_id)]
        return api_response(rooms)
    finally:
        conn.close()


@bp.post(
    "/chat/rooms",
    summary="Create a chat room or DM",
    description="Creates a Matrix room (or direct message). Requires `chat:write` scope.",
    responses={"200": ChatRoomResponse, "400": ErrorResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["chat:write"])
@require_scope("chat", "write")
@_handle_api_errors
def api_create_room(body: CreateChatRoomBody, query: AccountIdQuery):
    account_id = get_api_account_id()
    if body.is_direct and not body.invite:
        raise ApiError("VALIDATION", "A direct message needs at least one invitee")
    if not body.is_direct and not body.name:
        raise ApiError("VALIDATION", "Room name is required")
    _account, conn, client, own_id = _get_account_and_client(account_id, g.api_context["dek"])
    try:
        try:
            resp = client.create_room(
                name=None if body.is_direct else body.name,
                topic=body.topic,
                is_public=body.is_public,
                is_direct=body.is_direct,
                invite=body.invite or None,
            )
        except MatrixError as exc:
            raise _matrix_error(exc) from exc
        room_id = resp.get("room_id")
        if not room_id:
            raise ApiError("MATRIX_BAD_RESPONSE", "Chat server returned no room id", 502)
        cache_db.upsert_room(
            conn,
            room_id,
            name=None if body.is_direct else body.name,
            topic=body.topic,
            is_direct=body.is_direct,
            is_public=body.is_public,
            membership="join",
        )
        for user in body.invite:
            cache_db.upsert_member(
                conn, room_id, user, membership="invite" if user != own_id else "join"
            )
        room = cache_db.get_room(conn, room_id) or {"room_id": room_id}
        _decorate_room_display_name(conn, room, own_id)
        return api_response(_room_dict(room))
    finally:
        conn.close()


@bp.post(
    "/chat/rooms/<string:room_id>/join",
    summary="Join a chat room",
    description="Joins a room by ID or alias. Requires `chat:write` scope.",
    responses={"200": EmptyResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["chat:write"])
@require_scope("chat", "write")
@_handle_api_errors
def api_join_room(path: ChatRoomIdPath, query: AccountIdQuery):
    account_id = get_api_account_id()
    _account, conn, client, _own_id = _get_account_and_client(account_id, g.api_context["dek"])
    try:
        try:
            client.join_room(path.room_id)
        except MatrixError as exc:
            raise _matrix_error(exc) from exc
        cache_db.upsert_room(conn, path.room_id, membership="join")
        return api_response({"ok": True})
    finally:
        conn.close()


@bp.post(
    "/chat/rooms/<string:room_id>/leave",
    summary="Leave a chat room",
    description="Leaves and forgets a room. Requires `chat:write` scope.",
    responses={"200": EmptyResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["chat:write"])
@require_scope("chat", "write")
@_handle_api_errors
def api_leave_room(path: ChatRoomIdPath, query: AccountIdQuery):
    account_id = get_api_account_id()
    _account, conn, client, _own_id = _get_account_and_client(account_id, g.api_context["dek"])
    try:
        try:
            client.leave_room(path.room_id)
        except MatrixError as exc:
            raise _matrix_error(exc) from exc
        cache_db.delete_room(conn, path.room_id)
        return api_response({"ok": True})
    finally:
        conn.close()


@bp.post(
    "/chat/rooms/<string:room_id>/invite",
    summary="Invite a user to a chat room",
    description="Invites a Matrix user to a room. Requires `chat:write` scope.",
    responses={"200": EmptyResponse, "400": ErrorResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["chat:write"])
@require_scope("chat", "write")
@_handle_api_errors
def api_invite_to_room(path: ChatRoomIdPath, body: InviteChatUserBody, query: AccountIdQuery):
    account_id = get_api_account_id()
    _account, conn, client, _own_id = _get_account_and_client(account_id, g.api_context["dek"])
    try:
        try:
            client.invite(path.room_id, body.user_id)
        except MatrixError as exc:
            raise _matrix_error(exc) from exc
        cache_db.upsert_member(conn, path.room_id, body.user_id, membership="invite")
        return api_response({"ok": True})
    finally:
        conn.close()


@bp.get(
    "/chat/rooms/<string:room_id>/messages",
    summary="List chat messages",
    description="Returns cached messages for a room (with server backfill on first access). Requires `chat:read` scope.",
    responses={"200": ChatMessageListResponse, "404": ErrorResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["chat:read"])
@require_scope("chat", "read")
@_handle_api_errors
def api_list_messages(path: ChatRoomIdPath, query: ListMessagesQuery):
    account_id = get_api_account_id()
    _account, conn, client, own_id = _get_account_and_client(account_id, g.api_context["dek"])
    try:
        room = cache_db.get_room(conn, path.room_id)
        if not room:
            raise ApiError("ROOM_NOT_FOUND", "Room not found", 404)
        rows = cache_db.list_messages(
            conn, path.room_id, limit=query.limit, before_ts=query.before_ts
        )
        if not rows and room.get("prev_batch") and not query.before_ts:
            try:
                resp = client.room_messages(path.room_id, room["prev_batch"], limit=query.limit)
            except MatrixError as exc:
                raise _matrix_error(exc) from exc
            from app.modules.chat.services.sync import process_backfill

            process_backfill(conn, own_id, path.room_id, resp)
            rows = cache_db.list_messages(conn, path.room_id, limit=query.limit)
        reactions = cache_db.list_reactions(conn, path.room_id)
        items = [
            cache_db.message_to_api(r, reactions.get(r["event_id"]), own_user_id=own_id)
            for r in rows
        ]
        has_more = len(rows) >= query.limit
        return api_paginated(items, has_more=has_more)
    finally:
        conn.close()


@bp.post(
    "/chat/rooms/<string:room_id>/messages",
    summary="Send a chat message",
    description="Sends a text message to a room. Requires `chat:write` scope.",
    responses={"200": ChatEventIdResponse, "400": ErrorResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["chat:write"])
@require_scope("chat", "write")
@_handle_api_errors
def api_send_message(path: ChatRoomIdPath, body: SendChatMessageBody, query: AccountIdQuery):
    account_id = get_api_account_id()
    _account, conn, client, own_id = _get_account_and_client(account_id, g.api_context["dek"])
    try:
        room = cache_db.get_room(conn, path.room_id)
        if not room or room.get("membership") != "join":
            raise ApiError("ROOM_NOT_FOUND", "Room not found", 404)
        try:
            resp = client.send_message(path.room_id, body.body)
        except MatrixError as exc:
            raise _matrix_error(exc) from exc
        content = {"msgtype": "m.text", "body": body.body}
        cache_db.insert_message(
            conn,
            event_id=resp.get("event_id", ""),
            room_id=path.room_id,
            sender=own_id,
            type="m.room.message",
            body=body.body,
            content_json=json.dumps(content),
            origin_server_ts=resp.get("ts", 0) or 0,
        )
        return api_response({"event_id": resp.get("event_id")})
    finally:
        conn.close()


@bp.post(
    "/chat/rooms/<string:room_id>/read",
    summary="Mark a chat room read",
    description="Sends a read receipt and fully-read marker. Requires `chat:write` scope.",
    responses={"200": EmptyResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["chat:write"])
@require_scope("chat", "write")
@_handle_api_errors
def api_mark_room_read(path: ChatRoomIdPath, body: ReadChatRoomBody, query: AccountIdQuery):
    account_id = get_api_account_id()
    _account, conn, client, _own_id = _get_account_and_client(account_id, g.api_context["dek"])
    try:
        try:
            client.send_receipt(path.room_id, body.event_id)
            client.set_read_marker(path.room_id, body.event_id)
        except MatrixError as exc:
            logger.info(
                "read marker push failed room=%s code=%s (non-fatal)", path.room_id, exc.code
            )
        cache_db.set_read_state(
            conn, path.room_id, read_event_id=body.event_id, fully_read_event_id=body.event_id
        )
        cache_db.update_room_fields(
            conn, path.room_id, {"notification_count": 0, "highlight_count": 0}
        )
        return api_response({"ok": True})
    finally:
        conn.close()


@bp.post(
    "/chat/messages/<string:event_id>/react",
    summary="React to a chat message",
    description="Adds (or toggles off) an emoji reaction. Requires `chat:write` scope.",
    responses={"200": EmptyResponse, "404": ErrorResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["chat:write"])
@require_scope("chat", "write")
@_handle_api_errors
def api_react_to_message(path: ChatEventIdPath, body: ReactChatMessageBody, query: AccountIdQuery):
    account_id = get_api_account_id()
    _account, conn, client, own_id = _get_account_and_client(account_id, g.api_context["dek"])
    try:
        row = cache_db.get_message(conn, path.event_id)
        if not row:
            raise ApiError("MESSAGE_NOT_FOUND", "Message not found", 404)
        try:
            resp = client.send_reaction(row["room_id"], path.event_id, body.key)
        except MatrixError as exc:
            raise _matrix_error(exc) from exc
        cache_db.insert_message(
            conn,
            event_id=resp.get("event_id", ""),
            room_id=row["room_id"],
            sender=own_id,
            type="m.reaction",
            body=body.key,
            content_json=json.dumps(
                {
                    "m.relates_to": {
                        "rel_type": "m.annotation",
                        "event_id": path.event_id,
                        "key": body.key,
                    }
                }
            ),
            relates_to=path.event_id,
            rel_type="m.annotation",
            origin_server_ts=resp.get("ts", 0) or 0,
        )
        return api_response({"ok": True})
    finally:
        conn.close()


@bp.post(
    "/chat/messages/<string:event_id>/edit",
    summary="Edit a chat message",
    description="Edits one of your own messages. Requires `chat:write` scope.",
    responses={
        "200": ChatEventIdResponse,
        "403": ErrorResponse,
        "404": ErrorResponse,
        "401": ErrorResponse,
    },
)
@require_api_token(scopes=["chat:write"])
@require_scope("chat", "write")
@_handle_api_errors
def api_edit_message(path: ChatEventIdPath, body: EditChatMessageBody, query: AccountIdQuery):
    account_id = get_api_account_id()
    _account, conn, client, own_id = _get_account_and_client(account_id, g.api_context["dek"])
    try:
        row = cache_db.get_message(conn, path.event_id)
        if not row or row["type"] != "m.room.message":
            raise ApiError("MESSAGE_NOT_FOUND", "Message not found", 404)
        if row["sender"] != own_id:
            raise ApiError("FORBIDDEN", "Only your own messages can be edited", 403)
        try:
            resp = client.send_edit(row["room_id"], path.event_id, body.body)
        except MatrixError as exc:
            raise _matrix_error(exc) from exc
        cache_db.update_message_content(
            conn, path.event_id, json.dumps({"msgtype": "m.text", "body": body.body}), body.body
        )
        return api_response({"event_id": resp.get("event_id")})
    finally:
        conn.close()


@bp.post(
    "/chat/messages/<string:event_id>/redact",
    summary="Delete a chat message",
    description="Redacts (deletes) a message. Requires `chat:write` scope.",
    responses={"200": EmptyResponse, "404": ErrorResponse, "401": ErrorResponse},
)
@require_api_token(scopes=["chat:write"])
@require_scope("chat", "write")
@_handle_api_errors
def api_redact_message(path: ChatEventIdPath, query: AccountIdQuery):
    account_id = get_api_account_id()
    _account, conn, client, _own_id = _get_account_and_client(account_id, g.api_context["dek"])
    try:
        row = cache_db.get_message(conn, path.event_id)
        if not row:
            raise ApiError("MESSAGE_NOT_FOUND", "Message not found", 404)
        try:
            client.redact(row["room_id"], path.event_id)
        except MatrixError as exc:
            raise _matrix_error(exc) from exc
        if row["type"] == "m.reaction":
            cache_db.delete_message(conn, path.event_id)
        else:
            cache_db.mark_redacted(conn, path.event_id)
        return api_response({"ok": True})
    finally:
        conn.close()
