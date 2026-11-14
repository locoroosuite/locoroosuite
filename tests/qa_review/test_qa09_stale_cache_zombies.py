"""ATTACK 9: stale-cache interleaving — no zombie drafts.

Interleave save→delete→save→delete while the cache and the IMAP server
disagree (draft vanished server-side but the cache row lingers). After
each step the cache must converge to the truth: listings show exactly the
drafts that exist on the server.
"""

import json

from tests.qa_review.conftest import drafts_in_cache, fake_imap, seed_cache_message


def _hdr(env):
    return {"Authorization": f"Bearer {env.token}"}


def _listing(env):
    resp = env.client.get("/api/v1/mail/folders/Drafts/messages", headers=_hdr(env))
    assert resp.status_code == 200
    return json.loads(resp.data)["data"]


def _save(env, subject):
    with fake_imap(env.server):
        return env.client.post(
            "/api/v1/mail/drafts",
            json={"to": ["a@example.com"], "subject": subject, "body_plain": "x"},
            headers=_hdr(env),
        )


def _delete(env, ident):
    with fake_imap(env.server):
        return env.client.delete(f"/api/v1/mail/drafts/{ident}", headers=_hdr(env))


class TestStaleCacheInterleave:
    def test_save_delete_save_delete_with_stale_cache(self, qa_mail):
        env = qa_mail

        # --- save #1 (draft uid 700, cached) ---------------------------
        resp = _save(env, "one")
        assert resp.status_code == 201, resp.data
        uid1 = json.loads(resp.data)["data"]["draft_uid"]
        assert len(_listing(env)) == 1

        # --- stale: the draft vanishes server-side (expunged elsewhere),
        # the cache row survives ----------------------------------------
        env.server.messages["Drafts"].clear()
        assert len(drafts_in_cache(env.cache_path)) == 1  # zombie present

        # delete must clean the zombie and 404 honestly
        resp = _delete(env, uid1)
        assert resp.status_code == 404, resp.data
        assert json.loads(resp.data)["error"]["code"] == "DRAFT_NOT_FOUND"
        assert drafts_in_cache(env.cache_path) == []
        assert _listing(env) == []

        # --- save #2 (fresh uid minted, cached) ------------------------
        resp = _save(env, "two")
        assert resp.status_code == 201, resp.data
        listing = _listing(env)
        assert len(listing) == 1
        assert listing[0]["subject"] == "two"

        # --- delete #2 by cache id: real delete ------------------------
        cache_id2 = json.loads(resp.data)["data"]["draft_id"]
        resp = _delete(env, cache_id2)
        assert resp.status_code == 200, resp.data
        assert env.server.messages["Drafts"] == {}
        assert drafts_in_cache(env.cache_path) == []
        assert _listing(env) == []

    def test_repeated_zombie_seeds_never_accumulate(self, qa_mail):
        """Hammer the stale-row cleanup: three zombie rows, one delete each,
        interleaved with saves — the final listing must match the server."""
        env = qa_mail
        for uid in ("900", "901", "902"):
            seed_cache_message(
                env.cache_path,
                uid=uid,
                folder="Drafts",
                subject=f"zombie {uid}",
                message_id=f"<z{uid}@example.com>",
            )
            resp = _delete(env, uid)
            assert resp.status_code == 404, resp.data
            assert drafts_in_cache(env.cache_path) == []

        resp = _save(env, "final")
        assert resp.status_code == 201
        listing = _listing(env)
        assert [m["subject"] for m in listing] == ["final"]
        assert len(env.server.messages["Drafts"]) == 1

    def test_delete_by_stale_cache_id_after_server_side_vanish(self, qa_mail):
        """Same staleness, attacked via the cache id rather than the uid."""
        env = qa_mail
        stale_id = seed_cache_message(
            env.cache_path,
            uid="950",
            folder="Drafts",
            subject="stale by id",
            message_id="<s950@example.com>",
        )
        env.server.messages["Drafts"].clear()

        resp = _delete(env, stale_id)
        assert resp.status_code == 404, resp.data
        assert json.loads(resp.data)["error"]["code"] == "DRAFT_NOT_FOUND"
        assert drafts_in_cache(env.cache_path) == []
        assert _listing(env) == []
