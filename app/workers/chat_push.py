"""Headless chat push worker (HLD U25.61).

Serves users with at least one active push subscription and
``notify_chat_enabled``: while they have no chat tab open (no SSE
stream), this worker drives Matrix /sync for their accounts, persists
events into the encrypted chat cache, records delivery rows (U25.18),
and sends device notifications for incoming calls, messages, and room
invites — deduplicated per event id in ``chat_push_log``.

Skips accounts without cached chat credentials: headless provisioning
would create Matrix identities for every mail user, which U25.6 reserves
for the lazy on-first-open path.
"""

from __future__ import annotations

import logging
import threading
import time

from app.modules.chat.services import cache_db, push_log, receipts
from app.modules.chat.services.matrix import MatrixClient, MatrixError, homeserver_url
from app.modules.chat.services.streams import has_active_stream
from app.modules.chat.services.sync import process_sync, sync_lock
from app.shared import push, push_keys
from app.shared.db import db
from app.shared.models.core import CustomerAccount, CustomerSettings, Domain, PushSubscription

logger = logging.getLogger(__name__)

TICK_SECONDS = 10.0
RETRY_SCHEDULE = (10, 30, 60, 300)
_TOKEN_ERROR_CODES = {"M_UNKNOWN_TOKEN", "M_MISSING_TOKEN"}


def _invite_member_event(invite_state_events: list[dict], own_matrix_id: str) -> dict | None:
    """The m.room.member invite event for the syncing user, if present."""
    for event in invite_state_events or []:
        if (
            event.get("type") == "m.room.member"
            and event.get("state_key") == own_matrix_id
            and (event.get("content") or {}).get("membership") == "invite"
        ):
            return event
    return None


def plan_pushes(
    conn, own_matrix_id: str, changes: dict, response: dict, known_invite_rooms: set[str]
):
    """Derive push intents from one processed sync batch (HLD U25.61).

    Pure over (cache push-log state, changes, response); returns a list of
    intents: ``{"kind": "message"|"call"|"invite", "room_id", "event_ids",
    "sender", ...}``. Message intents aggregate per room; call intents carry
    ``call_id``/``video``. Every intent lists the event ids to mark pushed.
    """
    intents: list[dict] = []
    messages_by_room: dict[str, list[dict]] = changes.get("messages") or {}
    for room_id, messages in messages_by_room.items():
        fresh = [
            m
            for m in messages
            if m.get("type") == "m.room.message"
            and m.get("sender")
            and m["sender"] != own_matrix_id
            and not push_log.was_pushed(conn, m["event_id"])
        ]
        if not fresh:
            continue
        latest = max(fresh, key=lambda m: m.get("origin_server_ts") or 0)
        intents.append(
            {
                "kind": "message",
                "room_id": room_id,
                "count": len(fresh),
                "event_ids": [m["event_id"] for m in fresh],
                "sender": latest["sender"],
            }
        )
    calls_by_room: dict[str, list[dict]] = changes.get("calls") or {}
    for room_id, events in calls_by_room.items():
        for ev in events:
            if ev.get("type") != "m.call.invite" or not ev.get("call_id"):
                continue
            if not ev.get("sender") or ev["sender"] == own_matrix_id:
                continue
            if push_log.was_pushed(conn, ev["event_id"]):
                continue
            content = ev.get("content") or {}
            sdp = str((content.get("offer") or {}).get("sdp") or "")
            intents.append(
                {
                    "kind": "call",
                    "room_id": room_id,
                    "call_id": ev["call_id"],
                    "event_ids": [ev["event_id"]],
                    "sender": ev["sender"],
                    "video": bool(content.get("lr.video")) or "m=video" in sdp,
                }
            )
    invite_rooms = (response.get("rooms") or {}).get("invite") or {}
    for room_id, data in invite_rooms.items():
        if room_id in known_invite_rooms:
            continue
        events = (data.get("invite_state") or {}).get("events") or []
        member_event = _invite_member_event(events, own_matrix_id)
        key = (member_event or {}).get("event_id") or f"invite:{room_id}"
        if push_log.was_pushed(conn, key):
            continue
        intents.append(
            {
                "kind": "invite",
                "room_id": room_id,
                "event_ids": [key],
                "sender": (member_event or {}).get("sender") or "",
            }
        )
    return intents


class ChatPushWorker:
    def __init__(self, app, interval: float = TICK_SECONDS):
        self.app = app
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._next_run: dict[int, float] = {}  # account_id -> monotonic deadline
        self._retry_attempts: dict[int, int] = {}

    def start(self):
        if not self._thread.is_alive():
            logger.info("chat push worker starting")
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        if self.app.config.get("TESTING"):
            # Tests drive tick() manually; a background loop racing the
            # shared app DB would make push-count assertions flaky.
            logger.info("chat push worker idle (TESTING)")
            return
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("chat push worker tick failed")
            self._stop.wait(self.interval)

    def tick(self):
        with self.app.app_context():
            for user_id in self._eligible_users():
                if has_active_stream(user_id):
                    continue  # their chat tab's SSE loop is already live
                accounts = CustomerAccount.query.filter_by(
                    customer_id=user_id, is_active=True
                ).all()
                for account in accounts:
                    if time.monotonic() < self._next_run.get(account.id, 0.0):
                        continue
                    ok = True
                    try:
                        self._process_account(account)
                    except MatrixError as exc:
                        ok = False
                        logger.warning(
                            "chat push sync error account_id=%s customer_id=%s code=%s",
                            account.id,
                            account.customer_id,
                            exc.code,
                        )
                    except Exception:
                        ok = False
                        logger.exception(
                            "chat push failed account_id=%s customer_id=%s",
                            account.id,
                            account.customer_id,
                        )
                    if ok:
                        self._retry_attempts.pop(account.id, None)
                    else:
                        delay = self._register_backoff(account.id)
                        logger.warning(
                            "chat push backing off account_id=%s retry_in=%ss",
                            account.id,
                            delay,
                        )

    def _eligible_users(self) -> list[int]:
        """Users with chat notifications on and at least one active subscription."""
        subscribed = {
            row[0]
            for row in db.session.query(PushSubscription.user_id)
            .filter_by(disabled_at=None)
            .distinct()
            .all()
        }
        if not subscribed:
            return []
        rows = CustomerSettings.query.filter(
            CustomerSettings.customer_id.in_(subscribed),
            CustomerSettings.notify_chat_enabled.is_(True),
        ).all()
        return [s.customer_id for s in rows]

    def _register_backoff(self, account_id: int) -> int:
        attempt = self._retry_attempts.get(account_id, 0) + 1
        self._retry_attempts[account_id] = attempt
        delay = RETRY_SCHEDULE[min(attempt - 1, len(RETRY_SCHEDULE) - 1)]
        self._next_run[account_id] = time.monotonic() + delay
        return delay

    def _process_account(self, account: CustomerAccount):
        domain = db.session.get(Domain, account.domain_id)
        if not domain or not domain.is_active or not domain.matrix_host:
            return
        dek = push_keys.ensure_push_key(account.customer_id)
        if not dek:
            return
        from app.modules.chat.services.cache import get_cache_path

        conn = cache_db.open_cache(get_cache_path(account), dek)
        try:
            creds = cache_db.get_credentials(conn)
            if not creds or not creds.get("access_token"):
                # Chat identity not provisioned yet — lazy path (U25.6).
                return
            own_id = creds.get("matrix_user_id") or ""
            client = MatrixClient(homeserver_url(domain), creds["access_token"], own_id)
            since_state = cache_db.get_sync_state(conn)
            since = since_state.get("since_token") if since_state else None
            try:
                resp = client.sync(since=since, timeout_ms=0)
            except MatrixError as exc:
                if exc.code not in _TOKEN_ERROR_CODES:
                    raise
                logger.info(
                    "chat push token invalid account_id=%s; self-healing via provisioning",
                    account.id,
                )
                conn.close()
                from app.modules.chat.services.provisioning import ensure_chat_client

                conn, client, creds = ensure_chat_client(account, domain, account.customer_id)
                own_id = creds.get("matrix_user_id") or own_id
                resp = client.sync(since=since, timeout_ms=0)

            known_invite_rooms = {
                room["room_id"]
                for room in cache_db.list_rooms(conn, own_id)
                if room.get("membership") == "invite"
            }
            lock = sync_lock(account.customer_id)
            with lock:
                changes = process_sync(conn, own_id, resp)
                # Recipient side (U25.18): ingested foreign DM messages are
                # delivered even when the user was offline.
                receipts.record_deliveries(conn, own_id, changes)
            intents = plan_pushes(conn, own_id, changes, resp, known_invite_rooms)
            self._dispatch_pushes(account.customer_id, conn, own_id, intents)
            push_log.prune_push_log(conn)
        finally:
            conn.close()

    def _dispatch_pushes(self, user_id: int, conn, own_id: str, intents: list[dict]):
        if not intents:
            return
        settings = db.session.get(CustomerSettings, user_id)
        detailed = bool(settings and settings.push_detailed)
        for intent in intents:
            room_id = intent["room_id"]
            sender = intent.get("sender") or ""
            sender_name = (
                cache_db.member_display_name(conn, room_id, sender) if detailed and sender else ""
            )
            room_name = cache_db.room_display_name(conn, room_id, own_id) if detailed else ""
            kind = intent["kind"]
            if kind == "message":
                sent = push.send_chat_message_push(
                    self.app, user_id, room_id, intent["count"], sender_name, room_name
                )
            elif kind == "call":
                sent = push.send_chat_call_push(
                    self.app, user_id, intent["call_id"], sender_name, intent["video"]
                )
            else:
                sent = push.send_chat_invite_push(self.app, user_id, room_id, sender_name)
            # Mark pushed regardless of delivery state (calendar-worker
            # precedent): a re-subscribed device must not re-ring old events.
            for event_id in intent["event_ids"]:
                push_log.mark_pushed(conn, event_id, kind)
            logger.info(
                "chat push kind=%s user_id=%s room_id=%s sent=%s events=%s",
                kind,
                user_id,
                room_id,
                sent,
                len(intent["event_ids"]),
            )
