"""1:1 call signaling endpoints (HLD U25.19-U25.23).

Session-authenticated module endpoints under /app/chat/api — not /api/v1,
so no REST/MCP parity applies (call control is browser-only, U25.23).
The browser never talks to Synapse directly (U25.9): every m.call.* event
is sent through here and proxied to the homeserver via MatrixClient.
"""

from __future__ import annotations

import json
import time

from flask import jsonify, request
from flask_babel import _

from app.modules.chat.controllers.helpers import (
    ChatApiError,
    chat_bp,
    chat_context,
    current_account,
    matrix_to_chat_error,
    own_matrix_id,
    require_customer,
)
from app.modules.chat.services import cache_db
from app.modules.chat.services.matrix import MatrixError
from app.modules.chat.services.turn import ice_servers_for_domain

_MAX_SDP_BYTES = 64 * 1024
_MAX_CANDIDATES = 50
_INVITE_LIFETIME_MS = 60000

_HANGUP_REASONS = {
    "ice_timeout",
    "ice_failed",
    "invite_timeout",
    "user_hangup",
    "replaced",
    "user_media_error",
    "unknown_error",
}


def _joined_dm_room(conn, room_id: str) -> dict:
    room = cache_db.get_room(conn, room_id)
    if not room or room.get("membership") != "join":
        raise ChatApiError("ROOM_NOT_FOUND", _("Room not found"), 404)
    if not room.get("is_direct"):
        raise ChatApiError("CALLS_DM_ONLY", _("Calls are only available in direct messages"), 400)
    return room


def _existing_call(conn, room_id: str, call_id: str) -> None:
    if not call_id:
        raise ChatApiError("VALIDATION", _("call_id is required"))
    if not cache_db.call_exists(conn, room_id, call_id):
        raise ChatApiError("CALL_NOT_FOUND", _("Call not found"), 404)


def _send_call_event(conn, client, own_id: str, room_id: str, event_type: str, content: dict) -> dict:
    resp = client.send_event(room_id, event_type, content, txn_prefix="lrcall")
    event_id = resp.get("event_id")
    if not event_id:
        raise ChatApiError("MATRIX_BAD_RESPONSE", _("Chat server returned no event id"), 502)
    cache_db.insert_call_event(
        conn,
        event_id=event_id,
        room_id=room_id,
        sender=own_id,
        call_id=str(content.get("call_id", "")),
        event_type=event_type,
        content_json=json.dumps(content),
        origin_server_ts=resp.get("ts") or int(time.time() * 1000),
    )
    return resp


def _sdp_from(data: dict, field: str) -> str:
    payload = data.get(field)
    if not isinstance(payload, dict) or not isinstance(payload.get("sdp"), str):
        raise ChatApiError("VALIDATION", _("An SDP {field} is required", field=field))
    sdp = payload["sdp"]
    if not sdp.strip():
        raise ChatApiError("VALIDATION", _("An SDP {field} is required", field=field))
    if len(sdp.encode()) > _MAX_SDP_BYTES:
        raise ChatApiError("VALIDATION", _("The call payload is too large"), 413)
    return sdp


@chat_bp.route("/chat/api/turn", methods=["GET"])
@require_customer
def turn_config():
    """ICE servers with ephemeral REST credentials (HLD U25.21)."""
    account, _user_id, domain = current_account()
    return jsonify(ice_servers_for_domain(domain, account.email_address))


@chat_bp.route("/chat/api/rooms/<room_id>/call", methods=["POST"])
@require_customer
def start_call(room_id: str):
    """Send m.call.invite with the browser's SDP offer (call_id minted here)."""
    data = request.get_json(silent=True) or {}
    video = bool(data.get("video"))
    sdp = _sdp_from(data, "offer")

    account, _user_id, conn, client, creds = chat_context()
    try:
        _joined_dm_room(conn, room_id)
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        call_id = f"c{time.time_ns()}"
        content = {
            "call_id": call_id,
            "lifetime": _INVITE_LIFETIME_MS,
            "version": "1",
            "offer": {"type": "offer", "sdp": sdp},
        }
        # `video` is not part of the v1 wire format (Element infers it from
        # the SDP); kept on the content for our own clients' UI hint.
        if video:
            content["lr.video"] = True
        resp = _send_call_event(conn, client, own_id, room_id, "m.call.invite", content)
        return jsonify({"call_id": call_id, "event_id": resp.get("event_id")}), 201
    except MatrixError as exc:
        raise matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/rooms/<room_id>/call/<call_id>/answer", methods=["POST"])
@require_customer
def answer_call(room_id: str, call_id: str):
    data = request.get_json(silent=True) or {}
    sdp = _sdp_from(data, "answer")

    account, _user_id, conn, client, creds = chat_context()
    try:
        _joined_dm_room(conn, room_id)
        _existing_call(conn, room_id, call_id)
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        content = {"call_id": call_id, "answer": {"type": "answer", "sdp": sdp}, "version": "1"}
        resp = _send_call_event(conn, client, own_id, room_id, "m.call.answer", content)
        return jsonify({"event_id": resp.get("event_id")}), 201
    except MatrixError as exc:
        raise matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/rooms/<room_id>/call/<call_id>/candidates", methods=["POST"])
@require_customer
def send_candidates(room_id: str, call_id: str):
    data = request.get_json(silent=True) or {}
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ChatApiError(
            "VALIDATION", _("candidates must be a non-empty list of ICE candidates")
        )
    if len(candidates) > _MAX_CANDIDATES:
        raise ChatApiError("VALIDATION", _("Too many ICE candidates in one request"), 413)
    if not all(isinstance(c, dict) for c in candidates):
        raise ChatApiError(
            "VALIDATION", _("candidates must be a non-empty list of ICE candidates")
        )

    account, _user_id, conn, client, creds = chat_context()
    try:
        _joined_dm_room(conn, room_id)
        _existing_call(conn, room_id, call_id)
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        content = {"call_id": call_id, "candidates": candidates, "version": "1"}
        resp = _send_call_event(conn, client, own_id, room_id, "m.call.candidates", content)
        return jsonify({"event_id": resp.get("event_id")}), 201
    except MatrixError as exc:
        raise matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()


@chat_bp.route("/chat/api/rooms/<room_id>/call/<call_id>/hangup", methods=["POST"])
@require_customer
def hangup_call(room_id: str, call_id: str):
    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip()
    if reason and reason not in _HANGUP_REASONS:
        raise ChatApiError("VALIDATION", "reason must be a known hangup reason")

    account, _user_id, conn, client, creds = chat_context()
    try:
        _joined_dm_room(conn, room_id)
        _existing_call(conn, room_id, call_id)
        own_id = creds.get("matrix_user_id") or own_matrix_id(conn)
        content: dict = {"call_id": call_id, "version": "1"}
        if reason:
            content["reason"] = reason
        resp = _send_call_event(conn, client, own_id, room_id, "m.call.hangup", content)
        return jsonify({"event_id": resp.get("event_id")}), 201
    except MatrixError as exc:
        raise matrix_to_chat_error(exc, account.id) from exc
    finally:
        conn.close()
