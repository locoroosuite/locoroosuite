"""ATTACK 2 (issues #2/#3): delete_draft honesty torture.

- double delete: second must be a loud 404, never a phantom success
- delete by the OLD cache id after a replace: 404 / resolves to nothing
- IMAP raising mid-delete: structured JSON, never a raw HTML 500
- server that "deletes" but keeps the UID: DRAFT_DELETE_FAILED 502
"""

import json
from unittest.mock import patch

from tests.qa_review.conftest import drafts_in_cache, fake_imap, seed_cache_message


def _hdr(env):
    return {"Authorization": f"Bearer {env.token}"}


def _delete(env, ident):
    with fake_imap(env.server):
        return env.client.delete(f"/api/v1/mail/drafts/{ident}", headers=_hdr(env))


def _save(env, payload):
    with fake_imap(env.server):
        return env.client.post("/api/v1/mail/drafts", json=payload, headers=_hdr(env))


class TestDeleteTwice:
    def test_second_delete_is_404_not_phantom_success(self, qa_mail):
        env = qa_mail
        first = _save(env, {"to": ["a@example.com"], "subject": "doomed", "body_plain": "x"})
        assert first.status_code == 201
        data = json.loads(first.data)["data"]
        uid, cache_id = data["draft_uid"], data["draft_id"]

        resp1 = _delete(env, uid)
        assert resp1.status_code == 200, resp1.data
        assert json.loads(resp1.data)["data"]["status"] == "deleted"
        assert not env.server.uid_present("Drafts", uid)

        # Delete again by uid...
        resp2 = _delete(env, uid)
        assert resp2.status_code == 404, resp2.data
        assert json.loads(resp2.data)["error"]["code"] == "DRAFT_NOT_FOUND"
        # ...and by the (now stale) cache id.
        resp3 = _delete(env, cache_id)
        assert resp3.status_code == 404, resp3.data
        assert json.loads(resp3.data)["error"]["code"] == "DRAFT_NOT_FOUND"
        # no zombies in the cache listing either
        assert drafts_in_cache(env.cache_path) == []
        assert env.server.messages["Drafts"] == {}


class TestDeleteOldCacheIdAfterReplace:
    def _replace_setup(self, env):
        old_cache_id = seed_cache_message(
            env.cache_path,
            uid="300",
            folder="Drafts",
            subject="old draft",
            message_id="<old@example.com>",
        )
        env.server.seed_message("Drafts", "300", b"old draft raw")
        from unittest.mock import patch

        with (
            fake_imap(env.server),
            patch("app.modules.mail.services.imap_client.delete_message_by_uid") as mock_del,
        ):
            mock_del.side_effect = lambda client, uid: env.server.messages["Drafts"].pop(
                str(uid), None
            )
            resp = env.client.post(
                "/api/v1/mail/drafts",
                json={
                    "to": ["a@example.com"],
                    "subject": "new draft",
                    "body_plain": "new",
                    "replace_uid": str(old_cache_id),
                },
                headers=_hdr(env),
            )
        assert resp.status_code == 201, resp.data
        new_uid = json.loads(resp.data)["data"]["draft_uid"]
        return old_cache_id, new_uid

    def test_old_cache_id_after_replace_honest_contract(self, qa_mail):
        """Old id: either a loud 404, or a delete of exactly one existing
        draft (never a phantom, never the old uid, never both drafts)."""
        env = qa_mail
        old_cache_id, new_uid = self._replace_setup(env)
        assert not env.server.uid_present("Drafts", "300")
        assert env.server.uid_present("Drafts", new_uid)

        resp2 = _delete(env, old_cache_id)
        assert resp2.status_code in (200, 404), resp2.data
        if resp2.status_code == 200:
            body = json.loads(resp2.data)["data"]
            assert body["draft_uid"] != "300"  # the old uid can't be "deleted" twice
            assert env.server.messages["Drafts"] == {}
            assert drafts_in_cache(env.cache_path) == []
        else:
            assert json.loads(resp2.data)["error"]["code"] == "DRAFT_NOT_FOUND"
            # replacement untouched
            assert env.server.uid_present("Drafts", new_uid)

    def test_old_cache_id_never_rebinds_to_the_replacement(self, qa_mail):
        """U6.5b (updated expectation): identifiers resolve via the CURRENT
        Drafts rows in the mail cache, and cache ids are plain SQLite
        rowids (`id INTEGER PRIMARY KEY`, no AUTOINCREMENT) — so after a
        replace that frees the old row and syncs the replacement, the old
        id legitimately names the replacement row. The documented honest
        contract is therefore: the old id deletes exactly one LIVE draft
        (the replacement it now resolves to) — never a phantom success and
        never the already-gone old uid."""
        env = qa_mail
        old_cache_id, new_uid = self._replace_setup(env)
        assert not env.server.uid_present("Drafts", "300")
        assert env.server.uid_present("Drafts", new_uid)

        resp2 = _delete(env, old_cache_id)
        if resp2.status_code == 200:
            body = json.loads(resp2.data)["data"]
            # it resolved to the replacement row — the only live draft
            assert body["draft_uid"] == new_uid
            assert body["draft_uid"] != "300"
            assert env.server.messages["Drafts"] == {}
            assert drafts_in_cache(env.cache_path) == []
        else:
            assert resp2.status_code == 404, resp2.data
            assert json.loads(resp2.data)["error"]["code"] == "DRAFT_NOT_FOUND"
            assert env.server.uid_present("Drafts", new_uid)


class TestDeleteFailureModes:
    def test_imap_raises_mid_delete_structured_json_not_html_500(self, qa_mail):
        env = qa_mail
        seed_cache_message(
            env.cache_path,
            uid="500",
            folder="Drafts",
            subject="mid-delete boom",
            message_id="<boom@example.com>",
        )
        env.server.seed_message("Drafts", "500", b"raw")

        with (
            fake_imap(env.server),
            patch(
                "app.modules.mail.services.imap_client.delete_message_by_uid",
                side_effect=Exception("connection reset mid-delete"),
            ) as mock_del,
        ):
            resp = env.client.delete("/api/v1/mail/drafts/500", headers=_hdr(env))
        mock_del.assert_called_once()
        assert resp.status_code in (500, 502), resp.data
        assert resp.content_type.startswith("application/json"), resp.content_type
        body = json.loads(resp.data)
        assert "error" in body and "code" in body["error"], body
        assert b"Internal Server Error" not in resp.data
        assert b"<!DOCTYPE html" not in resp.data
        # honest: no "deleted" claim
        assert body["error"]["code"] != "DRAFT_NOT_FOUND" or resp.status_code == 404

    def test_uid_still_present_after_delete_is_draft_delete_failed_502(self, qa_mail):
        """Server 'successfully' deletes but the UID survives (issue #2b):
        must be a loud DRAFT_DELETE_FAILED 502, never status deleted."""
        env = qa_mail
        seed_cache_message(
            env.cache_path,
            uid="501",
            folder="Drafts",
            subject="sticky",
            message_id="<sticky@example.com>",
        )
        env.server.seed_message("Drafts", "501", b"raw")
        env.server.ignore_deletes = True  # STORE/EXPUNGE "succeed", keep UID

        resp = _delete(env, 501)
        assert resp.status_code == 502, resp.data
        body = json.loads(resp.data)
        assert body["error"]["code"] == "DRAFT_DELETE_FAILED", body
        # and nothing was claimed deleted
        assert env.server.uid_present("Drafts", "501")

    def test_delete_garbage_identifier_is_404(self, qa_mail):
        env = qa_mail
        resp = _delete(env, "abc")
        assert resp.status_code == 404, resp.data
        assert json.loads(resp.data)["error"]["code"] == "DRAFT_NOT_FOUND"

    def test_delete_stale_cache_row_cleans_cache_and_404s(self, qa_mail):
        """Cache says the draft exists, server disagrees: the stale row must
        be cleaned (no zombie listings) and the delete must 404."""
        env = qa_mail
        stale_id = seed_cache_message(
            env.cache_path,
            uid="777",
            folder="Drafts",
            subject="stale zombie",
            message_id="<zombie@example.com>",
        )
        # nothing on the server
        resp = _delete(env, stale_id)
        assert resp.status_code == 404, resp.data
        assert json.loads(resp.data)["error"]["code"] == "DRAFT_NOT_FOUND"
        assert drafts_in_cache(env.cache_path) == []
