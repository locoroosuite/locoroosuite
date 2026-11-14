"""ATTACK 5 (issue #7): response envelope contract.

The REST API MUST keep its {data: ...} envelope (documented there), while
the MCP helpers MUST produce flattened JSON with no top-level "data" key.
Both sides are attacked: every mail GET endpoint via the API, and the
ok()/ok_items()/ok_paginated() helpers unit-tested directly.
"""

import json

from tests.qa_review.conftest import fake_imap, seed_cache_message


def _hdr(env):
    return {"Authorization": f"Bearer {env.token}"}


class TestRestEnvelopeIntact:
    def test_every_mail_get_endpoint_keeps_data_envelope(self, qa_mail):
        env = qa_mail
        seed_cache_message(
            env.cache_path,
            uid="100",
            folder="INBOX",
            subject="envelope probe",
            message_id="<env@example.com>",
            thread_id="thread-env",
            body_html="<p>big html</p>",
        )
        env.server.seed_message("INBOX", "100", b"raw message source")

        with fake_imap(env.server):
            requests = {
                "folders": env.client.get("/api/v1/mail/folders", headers=_hdr(env)),
                "messages": env.client.get(
                    "/api/v1/mail/folders/INBOX/messages", headers=_hdr(env)
                ),
                "message": env.client.get("/api/v1/mail/messages/1", headers=_hdr(env)),
                "thread": env.client.get("/api/v1/mail/threads/thread-env", headers=_hdr(env)),
                "search": env.client.get("/api/v1/mail/search?q=envelope", headers=_hdr(env)),
                "raw": env.client.get("/api/v1/mail/messages/1/raw", headers=_hdr(env)),
            }

        for name, resp in requests.items():
            assert resp.status_code == 200, (name, resp.status_code, resp.data)
            body = json.loads(resp.data)
            assert "data" in body, f"{name}: REST envelope lost its data key: {body}"
            assert isinstance(body["data"], (list, dict)), name

        # raw endpoint: data is the RFC822 source text
        raw_body = json.loads(requests["raw"].data)
        assert isinstance(raw_body["data"]["data"], str)

    def test_error_envelope_shape(self, qa_mail):
        env = qa_mail
        resp = env.client.get("/api/v1/mail/messages/99999", headers=_hdr(env))
        assert resp.status_code == 404
        body = json.loads(resp.data)
        assert set(body) == {"error"}
        assert set(body["error"]) >= {"code", "message"}
        assert "data" not in body


class TestMcpHelpersFlattened:
    def test_ok_flattens_no_data_key(self):
        from app.mcp.helpers import ok

        payload = {"subject": "hello", "body_plain": "x", "nested": {"a": 1}}
        parsed = json.loads(ok(payload))
        assert parsed == payload
        assert "data" not in parsed

        # single-resource style: fields at the top level, exactly what the
        # MCP issue #7 asked for (result.subject works directly)
        assert parsed["subject"] == "hello"

    def test_ok_none_gives_empty_object(self):
        from app.mcp.helpers import ok

        parsed = json.loads(ok())
        assert parsed == {}
        assert "data" not in parsed

    def test_ok_items_uses_items_key(self):
        from app.mcp.helpers import ok_items

        parsed = json.loads(ok_items([{"id": 1}, {"id": 2}]))
        assert parsed == {"items": [{"id": 1}, {"id": 2}]}
        assert "data" not in parsed

    def test_ok_paginated_items_plus_pagination(self):
        from app.mcp.helpers import ok_paginated

        parsed = json.loads(ok_paginated([1, 2], next_cursor="9", has_more=True))
        assert parsed["items"] == [1, 2]
        assert parsed["pagination"] == {"next_cursor": "9", "has_more": True}
        assert "data" not in parsed

    def test_err_shape(self):
        from app.mcp.helpers import err

        parsed = json.loads(err("DRAFT_NOT_FOUND", "no draft 1194"))
        assert parsed == {"error": {"code": "DRAFT_NOT_FOUND", "message": "no draft 1194"}}
