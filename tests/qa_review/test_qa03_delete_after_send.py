"""ATTACK 3 (issue #4): send with draft_id — delete-after-send honesty.

- (a) valid draft id: sent + draft_deleted=true, draft really gone
- (b) unknown id:      sent + draft_deleted=false, honest error, no crash
- (c) INBOX message id: sent + INBOX message MUST SURVIVE
The mail must go out in all three cases.
"""

import json

from tests.qa_review.conftest import drafts_in_cache, fake_imap, fake_smtp, seed_cache_message

SEND_URL = "/api/v1/mail/messages"


def _hdr(env):
    return {"Authorization": f"Bearer {env.token}"}


def _send(env, payload):
    sent = []
    with fake_imap(env.server), fake_smtp(sent):
        resp = env.client.post(SEND_URL, json=payload, headers=_hdr(env))
    return resp, sent


def _seed_draft(env, uid, subject="draft to send"):
    cache_id = seed_cache_message(
        env.cache_path,
        uid=str(uid),
        folder="Drafts",
        subject=subject,
        message_id=f"<draft-{uid}@example.com>",
    )
    env.server.seed_message("Drafts", str(uid), b"draft raw")
    return cache_id


class TestSendDeleteAfterSend:
    def test_valid_draft_id_sent_and_deleted(self, qa_mail):
        env = qa_mail
        draft_cache_id = _seed_draft(env, "42")
        inbox_id = seed_cache_message(env.cache_path, uid="100", folder="INBOX")

        resp, sent = _send(
            env,
            {
                "to": ["b@example.com"],
                "subject": "from draft",
                "body_plain": "hello",
                "draft_id": str(draft_cache_id),
            },
        )
        assert resp.status_code == 201, resp.data
        data = json.loads(resp.data)["data"]
        assert data["status"] == "sent"
        assert data["draft_deleted"] is True
        assert "draft_error" not in data
        # mail actually went out, sent copy appended
        assert len(sent) == 1
        assert sent[0]["recipients"] == ["b@example.com"]
        assert len(env.server.messages["Sent"]) == 1
        # draft really gone — server AND cache
        assert not env.server.uid_present("Drafts", "42")
        assert drafts_in_cache(env.cache_path) == []
        # INBOX untouched
        got = env.client.get(f"/api/v1/mail/messages/{inbox_id}", headers=_hdr(env))
        assert got.status_code == 200

    def test_unknown_draft_id_still_sends_reports_not_deleted(self, qa_mail):
        env = qa_mail
        resp, sent = _send(
            env,
            {
                "to": ["b@example.com"],
                "subject": "unknown draft",
                "body_plain": "hello",
                "draft_id": "1195",
            },
        )
        assert resp.status_code == 201, resp.data
        data = json.loads(resp.data)["data"]
        assert data["status"] == "sent"
        assert data["draft_deleted"] is False
        assert data["draft_error"] == "DRAFT_NOT_FOUND"
        assert len(sent) == 1
        assert len(env.server.messages["Sent"]) == 1

    def test_inbox_message_id_must_not_delete_inbox(self, qa_mail):
        env = qa_mail
        inbox_id = seed_cache_message(
            env.cache_path,
            uid="100",
            folder="INBOX",
            subject="real inbox mail",
            message_id="<inbox-mail@example.com>",
        )
        _seed_draft(env, "700", subject="unrelated draft")

        resp, _sent = _send(
            env,
            {
                "to": ["b@example.com"],
                "subject": "oops wrong id",
                "body_plain": "hello",
                "draft_id": str(inbox_id),
            },
        )
        assert resp.status_code == 201, resp.data
        data = json.loads(resp.data)["data"]
        assert data["status"] == "sent"
        # The inbox message is not a draft: not deleted, honestly reported.
        assert data["draft_deleted"] is False
        assert data["draft_error"] == "DRAFT_NOT_FOUND"
        # ...and it SURVIVES in the cache and on the server.
        got = env.client.get(f"/api/v1/mail/messages/{inbox_id}", headers=_hdr(env))
        assert got.status_code == 200
        assert json.loads(got.data)["data"]["subject"] == "real inbox mail"
        # the unrelated draft survives too — nothing was falsely deleted
        assert env.server.uid_present("Drafts", "700")
        assert {r["uid"] for r in drafts_in_cache(env.cache_path)} == {"700"}

    def test_draft_uid_field_also_works(self, qa_mail):
        env = qa_mail
        _seed_draft(env, "55")
        resp, sent = _send(
            env,
            {
                "to": ["b@example.com"],
                "subject": "by uid",
                "body_plain": "hello",
                "draft_uid": "55",
            },
        )
        assert resp.status_code == 201, resp.data
        data = json.loads(resp.data)["data"]
        assert data["draft_deleted"] is True
        assert len(sent) == 1
        assert not env.server.uid_present("Drafts", "55")
