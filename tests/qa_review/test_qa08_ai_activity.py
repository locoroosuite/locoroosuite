"""ATTACK 8 (U27): AI activity + ui_change emission for POST /mail/drafts.

After an authenticated draft save:
- an ai_activity event (tool mail_save_draft) must arrive for THAT customer
- a ui_change draft_saved event must arrive carrying draft_uid /
  message_id / thread_id / snippet
- a second customer's recorder must see NOTHING
"""

import json

from tests.qa_review.conftest import fake_imap


class _Recorder:
    def __init__(self):
        self.events = []

    def __call__(self, user_id, event_type, data):
        self.events.append({"user_id": user_id, "type": event_type, "data": data})

    def for_user(self, user_id):
        return [e for e in self.events if e["user_id"] == user_id]

    def of_type(self, event_type, user_id=None):
        return [e for e in self.events if e["type"] == event_type and e["user_id"] == user_id]


def test_draft_save_emits_ai_activity_and_draft_saved(qa_mail_pair):
    env1, env2 = qa_mail_pair

    from app.api import openapi as _openapi
    from app.shared import events

    _openapi._coalesce_state.clear()
    rec = _Recorder()
    events.add_subscriber(rec)
    try:
        with fake_imap(env1.server):
            resp = env1.client.post(
                "/api/v1/mail/drafts",
                json={"to": ["a@example.com"], "subject": "trace me", "body_plain": "body"},
                headers={"Authorization": f"Bearer {env1.token}"},
            )
        assert resp.status_code == 201, resp.data
        payload = json.loads(resp.data)["data"]
    finally:
        events._subscribers.remove(rec)

    # 1) ai_activity: tool mail_save_draft for customer 1 only
    ai = rec.of_type("ai_activity", user_id=env1.user_id)
    assert ai, "no ai_activity emitted for the draft save"
    last_ai = ai[-1]["data"]
    assert last_ai["tool"] == "mail_save_draft"
    assert last_ai["status"] == "ok"
    assert isinstance(last_ai["duration_ms"], int)

    # 2) ui_change draft_saved with the full identifier set
    ui = [
        e
        for e in rec.of_type("ui_change", user_id=env1.user_id)
        if e["data"].get("action") == "draft_saved"
    ]
    assert ui, f"draft_saved ui_change missing; got {[e['data'] for e in rec.events]}"
    saved = ui[-1]["data"]
    assert saved["draft_uid"] == payload["draft_uid"]
    assert saved["message_id"] == payload["draft_id"]
    assert saved["thread_id"] == payload["thread_id"]
    assert saved["snippet"] == payload["snippet"]
    assert saved["thread_id"], "thread_id missing from draft_saved payload"
    assert saved["snippet"], "snippet missing from draft_saved payload"

    # 3) nothing ever reaches the second customer
    leaked = rec.for_user(env2.user_id)
    assert leaked == [], f"events leaked to second customer: {leaked}"


def test_draft_delete_emits_draft_deleted_for_owner_only(qa_mail_pair):
    env1, env2 = qa_mail_pair

    from app.api import openapi as _openapi
    from app.shared import events
    from tests.qa_review.conftest import seed_cache_message

    _openapi._coalesce_state.clear()
    rec = _Recorder()
    events.add_subscriber(rec)
    try:
        seed_cache_message(env1.cache_path, uid="42", folder="Drafts", message_id="<d@example.com>")
        env1.server.seed_message("Drafts", "42", b"raw")
        with fake_imap(env1.server):
            resp = env1.client.delete(
                "/api/v1/mail/drafts/42",
                headers={"Authorization": f"Bearer {env1.token}"},
            )
        assert resp.status_code == 200, resp.data
    finally:
        events._subscribers.remove(rec)

    deleted = [
        e
        for e in rec.of_type("ui_change", user_id=env1.user_id)
        if e["data"].get("action") == "draft_deleted"
    ]
    assert deleted
    assert rec.of_type("ai_activity", user_id=env1.user_id)[-1]["data"]["tool"] == (
        "mail_delete_draft"
    )
    assert rec.for_user(env2.user_id) == []
