"""ATTACK 4 (issue #6): reply threading.

Send with reply_to_message_id of a parent that already has References:
- References on the reply = parent refs + parent Message-ID
- In-Reply-To on the reply = parent Message-ID
- BOTH the SMTP copy and the IMAP "Sent" copy carry them
- the sent reply groups into the parent's thread_id
"""

import email
import json
import re

from tests.qa_review.conftest import fake_imap, fake_smtp, seed_cache_message

PARENT_MID = "<parent-1792@example.com>"
PARENT_REFS = "<root-a@example.com> <root-b@example.com>"


def _hdr(env):
    return {"Authorization": f"Bearer {env.token}"}


def _unfold(value):
    """RFC 2822 unfold: remove folding CRLFs (leading whitespace stays)."""
    return re.sub(r"\r?\n", "", value or "")


def _parse(raw: bytes):
    return email.message_from_bytes(raw)


class TestReplyThreading:
    def test_reply_headers_on_smtp_and_sent_copy(self, qa_mail):
        env = qa_mail
        parent_id = seed_cache_message(
            env.cache_path,
            uid="100",
            folder="INBOX",
            subject="NEW ROLE - Software Development Manager",
            message_id=PARENT_MID,
            in_reply_to="<root-b@example.com>",
            ref_list=PARENT_REFS,
            thread_id=None,  # let compute_thread_id derive: refs[0]
        )

        sent = []
        with fake_imap(env.server), fake_smtp(sent):
            resp = env.client.post(
                "/api/v1/mail/messages",
                json={
                    "to": ["gemma@example.com"],
                    "subject": "Re: NEW ROLE - Software Development Manager",
                    "body_plain": "reply body",
                    "reply_to_message_id": str(parent_id),
                },
                headers=_hdr(env),
            )
        assert resp.status_code == 201, resp.data
        data = json.loads(resp.data)["data"]
        assert data["status"] == "sent"

        # 1) the SMTP copy that actually went out
        assert len(sent) == 1
        smtp_msg = _parse(sent[0]["raw"])
        assert _unfold(smtp_msg["In-Reply-To"]) == PARENT_MID
        assert _unfold(smtp_msg["References"]) == f"{PARENT_REFS} {PARENT_MID}"

        # 2) the copy appended to the Sent folder on the IMAP server
        assert len(env.server.messages["Sent"]) == 1
        sent_uid = env.server.uids("Sent")[0]
        sent_msg = _parse(env.server.raw_for("Sent", sent_uid))
        assert _unfold(sent_msg["In-Reply-To"]) == PARENT_MID
        assert _unfold(sent_msg["References"]) == f"{PARENT_REFS} {PARENT_MID}"

        # 3) thread continuation: cached sent copy shares the parent thread
        assert data["thread_id"] == "root-a@example.com", data.get("thread_id")

    def test_reply_with_unknown_message_id_is_loud_400(self, qa_mail):
        env = qa_mail
        with fake_imap(env.server), fake_smtp([]):
            resp = env.client.post(
                "/api/v1/mail/messages",
                json={
                    "to": ["g@example.com"],
                    "subject": "bad reply",
                    "body_plain": "x",
                    "reply_to_message_id": "424242",
                },
                headers=_hdr(env),
            )
        assert resp.status_code == 400, resp.data
        assert json.loads(resp.data)["error"]["code"] == "VALIDATION_ERROR"

    def test_raw_in_reply_to_string_also_threads(self, qa_mail):
        env = qa_mail
        sent = []
        with fake_imap(env.server), fake_smtp(sent):
            resp = env.client.post(
                "/api/v1/mail/messages",
                json={
                    "to": ["g@example.com"],
                    "subject": "reply by raw mid",
                    "body_plain": "x",
                    "in_reply_to": "bare-mid@example.com",  # no angle brackets
                },
                headers=_hdr(env),
            )
        assert resp.status_code == 201, resp.data
        assert len(sent) == 1
        msg = _parse(sent[0]["raw"])
        assert msg["In-Reply-To"] == "<bare-mid@example.com>"
        assert msg["References"] == "<bare-mid@example.com>"
