"""MCP tools for chat (Matrix): mirror of the REST API in app/api/controllers/chat.py."""

from __future__ import annotations

from typing import Annotated, Any

from flask import Flask
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from app.mcp.auth import McpAuthError
from app.mcp.errors import resilient_tool
from app.mcp.helpers import err, ok, resolve_read, resolve_write
from app.modules.chat.services import cache_db
from app.modules.chat.services.cache import get_cache_path
from app.modules.chat.services.cache_db import _decorate_room_display_name
from app.modules.chat.services.matrix import MatrixClient, MatrixError, homeserver_url
from app.shared.db import db
from app.shared.models.core import CustomerAccount, Domain

_AccId = Annotated[int | None, Field(description="Account ID (uses default account if omitted)")]


def _room_dict(room: dict) -> dict:
    return {
        "room_id": row_get(room, "room_id"),
        "name": row_get(room, "name"),
        "display_name": row_get(room, "display_name", ""),
        "topic": row_get(room, "topic"),
        "is_direct": bool(row_get(room, "is_direct")),
        "is_public": bool(row_get(room, "is_public")),
        "membership": row_get(room, "membership", "join"),
        "notification_count": row_get(room, "notification_count", 0),
        "highlight_count": row_get(room, "highlight_count", 0),
        "member_count": row_get(room, "member_count", 0),
        "last_event_ts": row_get(room, "last_event_ts"),
        "last_event_preview": row_get(room, "last_event_preview"),
    }


def row_get(row: dict, key: str, default: Any = None) -> Any:
    value = row.get(key, default) if isinstance(row, dict) else default
    return default if value is None else value


def _open_chat(account_id: int, dek: str):
    account = db.session.get(CustomerAccount, account_id)
    if not account:
        raise McpAuthError("NOT_FOUND", f"Account {account_id} not found")
    domain = db.session.get(Domain, account.domain_id)
    if not domain:
        raise McpAuthError("NOT_FOUND", f"Domain for account {account_id} not found")
    conn = cache_db.open_cache(get_cache_path(account), dek)
    creds = cache_db.get_credentials(conn)
    if not creds:
        conn.close()
        raise McpAuthError(
            "CHAT_NOT_PROVISIONED",
            "Chat is not set up for this account yet. Open the Chat page in the web app once to provision it.",
        )
    client = MatrixClient(homeserver_url(domain), creds["access_token"], creds["matrix_user_id"])
    return conn, client, creds["matrix_user_id"]


def _matrix_err(exc: MatrixError) -> str:
    return err(exc.code, exc.message)


def register(mcp: FastMCP, flask_app: Flask) -> None:
    @mcp.tool(
        name="chat_list_rooms",
        title="List Chat Rooms",
        description="List cached chat rooms and direct messages for the authenticated account. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def chat_list_rooms(account_id: _AccId = None) -> str:
        _ctx, aid, dek = resolve_read(flask_app, "chat", account_id)
        with flask_app.app_context():
            conn, _client, own_id = _open_chat(aid, dek)
            try:
                rooms = [_room_dict(r) for r in cache_db.list_rooms(conn, own_id)]
            finally:
                conn.close()
        return ok(rooms)

    @mcp.tool(
        name="chat_create_room",
        title="Create Chat Room",
        description="Create a chat room (or direct message with is_direct=true and invite).",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def chat_create_room(
        name: Annotated[
            str | None, Field(description="Room name (not required for direct messages)")
        ] = None,
        topic: Annotated[str | None, Field(description="Room topic")] = None,
        is_public: Annotated[bool, Field(description="Whether the room is public")] = False,
        is_direct: Annotated[bool, Field(description="Create a direct message room")] = False,
        invite: Annotated[list[str] | None, Field(description="Matrix user IDs to invite")] = None,
        account_id: _AccId = None,
    ) -> str:
        _ctx, aid, dek = resolve_write(flask_app, "chat", account_id)
        invite = invite or []
        if is_direct and not invite:
            return err("VALIDATION", "A direct message needs at least one invitee")
        if not is_direct and not name:
            return err("VALIDATION", "Room name is required")
        with flask_app.app_context():
            conn, client, own_id = _open_chat(aid, dek)
            try:
                try:
                    resp = client.create_room(
                        name=None if is_direct else name,
                        topic=topic,
                        is_public=is_public,
                        is_direct=is_direct,
                        invite=invite or None,
                    )
                except MatrixError as exc:
                    return _matrix_err(exc)
                room_id = resp.get("room_id")
                if not room_id:
                    return err("MATRIX_BAD_RESPONSE", "Chat server returned no room id")
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
                room = cache_db.get_room(conn, room_id) or {"room_id": room_id}
                _decorate_room_display_name(conn, room, own_id)
                return ok(_room_dict(room))
            finally:
                conn.close()

    @mcp.tool(
        name="chat_list_messages",
        title="List Chat Messages",
        description="List cached messages for a chat room. Read-only.",
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def chat_list_messages(
        room_id: Annotated[str, Field(description="Matrix room ID")],
        limit: Annotated[int, Field(description="Max messages to return", ge=1, le=200)] = 50,
        account_id: _AccId = None,
    ) -> str:
        _ctx, aid, dek = resolve_read(flask_app, "chat", account_id)
        with flask_app.app_context():
            conn, _client, own_id = _open_chat(aid, dek)
            try:
                if not cache_db.get_room(conn, room_id):
                    return err("ROOM_NOT_FOUND", f"Room {room_id} not found")
                rows = cache_db.list_messages(conn, room_id, limit=limit)
                reactions = cache_db.list_reactions(conn, room_id)
                items = [
                    cache_db.message_to_api(r, reactions.get(r["event_id"]), own_user_id=own_id)
                    for r in rows
                ]
            finally:
                conn.close()
        return ok(items)

    @mcp.tool(
        name="chat_send_message",
        title="Send Chat Message",
        description="Send a text message to a chat room.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def chat_send_message(
        room_id: Annotated[str, Field(description="Matrix room ID")],
        body: Annotated[str, Field(description="Message text")],
        account_id: _AccId = None,
    ) -> str:
        import json as _json

        _ctx, aid, dek = resolve_write(flask_app, "chat", account_id)
        with flask_app.app_context():
            conn, client, own_id = _open_chat(aid, dek)
            try:
                room = cache_db.get_room(conn, room_id)
                if not room or room.get("membership") != "join":
                    return err("ROOM_NOT_FOUND", f"Room {room_id} not found")
                try:
                    resp = client.send_message(room_id, body)
                except MatrixError as exc:
                    return _matrix_err(exc)
                content = {"msgtype": "m.text", "body": body}
                cache_db.insert_message(
                    conn,
                    event_id=resp.get("event_id", ""),
                    room_id=room_id,
                    sender=own_id,
                    type="m.room.message",
                    body=body,
                    content_json=_json.dumps(content),
                    origin_server_ts=resp.get("ts", 0) or 0,
                )
                return ok({"event_id": resp.get("event_id")})
            finally:
                conn.close()

    @mcp.tool(
        name="chat_edit_message",
        title="Edit Chat Message",
        description="Edit one of your own chat messages.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def chat_edit_message(
        event_id: Annotated[str, Field(description="Matrix event ID of the message")],
        body: Annotated[str, Field(description="New message text")],
        account_id: _AccId = None,
    ) -> str:
        import json as _json

        _ctx, aid, dek = resolve_write(flask_app, "chat", account_id)
        with flask_app.app_context():
            conn, client, own_id = _open_chat(aid, dek)
            try:
                row = cache_db.get_message(conn, event_id)
                if not row or row["type"] != "m.room.message":
                    return err("MESSAGE_NOT_FOUND", f"Message {event_id} not found")
                if row["sender"] != own_id:
                    return err("FORBIDDEN", "Only your own messages can be edited")
                try:
                    resp = client.send_edit(row["room_id"], event_id, body)
                except MatrixError as exc:
                    return _matrix_err(exc)
                cache_db.update_message_content(
                    conn, event_id, _json.dumps({"msgtype": "m.text", "body": body}), body
                )
                return ok({"event_id": resp.get("event_id")})
            finally:
                conn.close()

    @mcp.tool(
        name="chat_delete_message",
        title="Delete Chat Message",
        description="Delete (redact) a chat message you sent.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=True),
    )
    @resilient_tool
    async def chat_delete_message(
        event_id: Annotated[str, Field(description="Matrix event ID of the message")],
        account_id: _AccId = None,
    ) -> str:
        _ctx, aid, dek = resolve_write(flask_app, "chat", account_id)
        with flask_app.app_context():
            conn, client, _own_id = _open_chat(aid, dek)
            try:
                row = cache_db.get_message(conn, event_id)
                if not row:
                    return err("MESSAGE_NOT_FOUND", f"Message {event_id} not found")
                try:
                    client.redact(row["room_id"], event_id)
                except MatrixError as exc:
                    return _matrix_err(exc)
                if row["type"] == "m.reaction":
                    cache_db.delete_message(conn, event_id)
                else:
                    cache_db.mark_redacted(conn, event_id)
                return ok({"deleted": True})
            finally:
                conn.close()

    @mcp.tool(
        name="chat_react",
        title="React to Chat Message",
        description="Add an emoji reaction to a chat message.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def chat_react(
        event_id: Annotated[str, Field(description="Matrix event ID of the message")],
        key: Annotated[str, Field(description="Reaction key (emoji)")],
        account_id: _AccId = None,
    ) -> str:
        import json as _json

        _ctx, aid, dek = resolve_write(flask_app, "chat", account_id)
        with flask_app.app_context():
            conn, client, own_id = _open_chat(aid, dek)
            try:
                row = cache_db.get_message(conn, event_id)
                if not row:
                    return err("MESSAGE_NOT_FOUND", f"Message {event_id} not found")
                try:
                    resp = client.send_reaction(row["room_id"], event_id, key)
                except MatrixError as exc:
                    return _matrix_err(exc)
                cache_db.insert_message(
                    conn,
                    event_id=resp.get("event_id", ""),
                    room_id=row["room_id"],
                    sender=own_id,
                    type="m.reaction",
                    body=key,
                    content_json=_json.dumps(
                        {
                            "m.relates_to": {
                                "rel_type": "m.annotation",
                                "event_id": event_id,
                                "key": key,
                            }
                        }
                    ),
                    relates_to=event_id,
                    rel_type="m.annotation",
                    origin_server_ts=resp.get("ts", 0) or 0,
                )
                return ok({"event_id": resp.get("event_id")})
            finally:
                conn.close()

    @mcp.tool(
        name="chat_mark_room_read",
        title="Mark Chat Room Read",
        description="Send a read receipt for a chat room up to the given event.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def chat_mark_room_read(
        room_id: Annotated[str, Field(description="Matrix room ID")],
        event_id: Annotated[str, Field(description="Event ID to mark read up to")],
        account_id: _AccId = None,
    ) -> str:
        _ctx, aid, dek = resolve_write(flask_app, "chat", account_id)
        with flask_app.app_context():
            conn, client, _own_id = _open_chat(aid, dek)
            try:
                try:
                    client.send_receipt(room_id, event_id)
                    client.set_read_marker(room_id, event_id)
                except MatrixError as exc:
                    return _matrix_err(exc)
                cache_db.set_read_state(
                    conn, room_id, read_event_id=event_id, fully_read_event_id=event_id
                )
                cache_db.update_room_fields(
                    conn, room_id, {"notification_count": 0, "highlight_count": 0}
                )
                return ok({"read": True})
            finally:
                conn.close()

    @mcp.tool(
        name="chat_invite_user",
        title="Invite User to Chat Room",
        description="Invite a Matrix user to a chat room.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=False),
    )
    @resilient_tool
    async def chat_invite_user(
        room_id: Annotated[str, Field(description="Matrix room ID")],
        user_id: Annotated[str, Field(description="Matrix user ID to invite")],
        account_id: _AccId = None,
    ) -> str:
        _ctx, aid, dek = resolve_write(flask_app, "chat", account_id)
        with flask_app.app_context():
            conn, client, _own_id = _open_chat(aid, dek)
            try:
                try:
                    client.invite(room_id, user_id)
                except MatrixError as exc:
                    return _matrix_err(exc)
                cache_db.upsert_member(conn, room_id, user_id, membership="invite")
                return ok({"invited": user_id})
            finally:
                conn.close()

    @mcp.tool(
        name="chat_leave_room",
        title="Leave Chat Room",
        description="Leave (and forget) a chat room.",
        annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=False, destructiveHint=True),
    )
    @resilient_tool
    async def chat_leave_room(
        room_id: Annotated[str, Field(description="Matrix room ID")],
        account_id: _AccId = None,
    ) -> str:
        _ctx, aid, dek = resolve_write(flask_app, "chat", account_id)
        with flask_app.app_context():
            conn, client, _own_id = _open_chat(aid, dek)
            try:
                try:
                    client.leave_room(room_id)
                except MatrixError as exc:
                    return _matrix_err(exc)
                cache_db.delete_room(conn, room_id)
                return ok({"left": room_id})
            finally:
                conn.close()
