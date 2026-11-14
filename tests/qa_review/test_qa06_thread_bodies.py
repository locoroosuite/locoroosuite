"""ATTACK 6 (issue #8): mail_get_thread payload controls.

include_bodies=false must strip bodies; body_format=plain/html must drop
the other representation; anything else must be a loud 400.
"""

import json

from tests.qa_review.conftest import seed_cache_message


def _hdr(env):
    return {"Authorization": f"Bearer {env.token}"}


class TestThreadBodyControls:
    def _seed_thread(self, env):
        seed_cache_message(
            env.cache_path,
            uid="100",
            folder="INBOX",
            subject="original",
            message_id="<t1@example.com>",
            thread_id="big-thread",
            body="plain body 1",
            body_html="<p>huge quoted html 1</p>",
        )
        seed_cache_message(
            env.cache_path,
            uid="101",
            folder="INBOX",
            subject="Re: original",
            message_id="<t2@example.com>",
            in_reply_to="<t1@example.com>",
            ref_list="<t1@example.com>",
            thread_id="big-thread",
            body="plain body 2",
            body_html="<p>huge quoted html 2</p>",
        )

    def test_include_bodies_false_strips_bodies(self, qa_mail):
        env = qa_mail
        self._seed_thread(env)
        resp = env.client.get(
            "/api/v1/mail/threads/big-thread?include_bodies=false", headers=_hdr(env)
        )
        assert resp.status_code == 200
        items = json.loads(resp.data)["data"]
        assert len(items) == 2
        for item in items:
            assert "body_plain" not in item, item.keys()
            assert "body_html" not in item, item.keys()
            # headers + snippet survive
            assert item["snippet"]
            assert item["subject"]

    def test_body_format_plain_drops_html(self, qa_mail):
        env = qa_mail
        self._seed_thread(env)
        resp = env.client.get(
            "/api/v1/mail/threads/big-thread?body_format=plain", headers=_hdr(env)
        )
        assert resp.status_code == 200
        items = json.loads(resp.data)["data"]
        for item in items:
            assert "body_html" not in item
            suffix = 1 if item["imap_uid"] == "100" else 2
            assert item.get("body_plain") == f"plain body {suffix}"

    def test_body_format_html_drops_plain(self, qa_mail):
        env = qa_mail
        self._seed_thread(env)
        resp = env.client.get("/api/v1/mail/threads/big-thread?body_format=html", headers=_hdr(env))
        assert resp.status_code == 200
        items = json.loads(resp.data)["data"]
        for item in items:
            assert "body_plain" not in item
            assert item["body_html"].startswith("<p>huge")

    def test_body_format_bogus_is_400_even_with_include_bodies_false(self, qa_mail):
        env = qa_mail
        self._seed_thread(env)
        # NB: "body_format=" (empty) is treated as unset -> "both", which is
        # standard query-string convention; only non-empty garbage is a 400.
        for query in (
            "body_format=bogus",
            "include_bodies=false&body_format=bogus",
            "body_format=plain%2Bhtml",
            "body_format=json",
        ):
            resp = env.client.get(f"/api/v1/mail/threads/big-thread?{query}", headers=_hdr(env))
            assert resp.status_code == 400, query
            body = json.loads(resp.data)
            assert body["error"]["code"] == "VALIDATION_ERROR", query

    def test_body_format_case_insensitive(self, qa_mail):
        env = qa_mail
        self._seed_thread(env)
        resp = env.client.get(
            "/api/v1/mail/threads/big-thread?body_format=PLAIN&include_bodies=FALSE",
            headers=_hdr(env),
        )
        assert resp.status_code == 200
        items = json.loads(resp.data)["data"]
        # include_bodies=FALSE strips; body_format accepted case-insensitively
        for item in items:
            assert "body_plain" not in item
            assert "body_html" not in item
