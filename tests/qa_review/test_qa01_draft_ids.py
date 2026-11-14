"""ATTACK 1 (issues #1/#5): replace_uid hostile identifier torture.

Invariants under attack, for every identifier shape:
- the response is either a clean 201 replace or a loud 404 DRAFT_NOT_FOUND
- the IMAP append count grows by AT MOST one per call (never a duplicate)
- non-draft messages (INBOX) are never touched
"""

import json

from tests.qa_review.conftest import drafts_in_cache, fake_imap, seed_cache_message

DRAFTS_URL = "/api/v1/mail/drafts"


def _save(env, payload, *, cache_fetch=True):
    with fake_imap(env.server, cache_fetch=cache_fetch):
        return env.client.post(
            DRAFTS_URL, json=payload, headers={"Authorization": f"Bearer {env.token}"}
        )


def _count_drafts_on_server(env):
    return sum(len(env.server.messages.get(f, {})) for f in list(env.server.messages))


class TestReplaceUidHostileIdentifiers:
    def test_replace_uid_garbage_string_is_loud_404_no_append(self, qa_mail):
        env = qa_mail
        first = _save(env, {"to": ["a@example.com"], "subject": "v1", "body_plain": "v1"})
        assert first.status_code == 201
        baseline_appends = env.server.append_count()

        resp = _save(
            env,
            {
                "to": ["a@example.com"],
                "subject": "v2",
                "body_plain": "v2",
                "replace_uid": "abc",
            },
        )
        assert resp.status_code == 404, resp.data
        assert json.loads(resp.data)["error"]["code"] == "DRAFT_NOT_FOUND"
        # no duplicate append, drafts unchanged
        assert env.server.append_count() == baseline_appends
        assert _count_drafts_on_server(env) == 1

    def test_replace_uid_huge_int_is_loud_404_no_append(self, qa_mail):
        """BUG (found by this review): a huge-int identifier overflows
        SQLite's INTEGER in resolve_draft_identifier (drafts.py:75,
        int(ident)) and surfaces as 502 MAILBOX_UNAVAILABLE with the raw
        internal message — structured, but misclassified and leaky. A
        hostile identifier shape must be a 404 DRAFT_NOT_FOUND."""
        env = qa_mail
        first = _save(env, {"to": ["a@example.com"], "subject": "v1", "body_plain": "v1"})
        assert first.status_code == 201
        baseline_appends = env.server.append_count()

        resp = _save(
            env,
            {
                "to": ["a@example.com"],
                "subject": "v2",
                "body_plain": "v2",
                "replace_uid": "99999999999999999999999999",
            },
        )
        assert resp.status_code == 404, resp.data
        assert json.loads(resp.data)["error"]["code"] == "DRAFT_NOT_FOUND"
        assert env.server.append_count() == baseline_appends

    def test_replace_uid_inbox_message_id_is_404_and_inbox_survives(self, qa_mail):
        env = qa_mail
        inbox_id = seed_cache_message(env.cache_path, uid="100", folder="INBOX")
        first = _save(env, {"to": ["a@example.com"], "subject": "v1", "body_plain": "v1"})
        assert first.status_code == 201
        baseline_appends = env.server.append_count()

        resp = _save(
            env,
            {
                "to": ["a@example.com"],
                "subject": "v2",
                "body_plain": "v2",
                "replace_uid": str(inbox_id),
            },
        )
        assert resp.status_code == 404, resp.data
        assert json.loads(resp.data)["error"]["code"] == "DRAFT_NOT_FOUND"
        assert env.server.append_count() == baseline_appends
        # INBOX row untouched
        got = env.client.get(
            f"/api/v1/mail/messages/{inbox_id}",
            headers={"Authorization": f"Bearer {env.token}"},
        )
        assert got.status_code == 200

    def test_replace_uid_inbox_id_colliding_with_draft_uid_replaces_only_draft(self, qa_mail):
        """Nasty case: INBOX cache id == a draft's IMAP uid. The unified ID
        space may resolve to the draft (by design) but must never touch the
        INBOX message and must never leave two drafts behind.

        BUG (found by this review): with a real un-selected IMAP connection
        this currently 502s with DRAFT_DELETE_FAILED — save_draft calls
        delete_message_by_uid (STORE+EXPUNGE) BEFORE any SELECT, so on a
        real server the STORE is a silent NO-op and the post-delete
        verification then finds the UID still present
        (app/modules/mail/services/drafts.py:272 -> imap_client.py:225).
        The repo suite misses it because it patches delete_message_by_uid."""
        env = qa_mail
        # INBOX row gets cache id 1; draft's IMAP uid is also "1".
        inbox_id = seed_cache_message(env.cache_path, uid="100", folder="INBOX")
        seed_cache_message(
            env.cache_path,
            uid="1",
            folder="Drafts",
            subject="colliding draft",
            message_id="<collide@example.com>",
        )
        env.server.seed_message("Drafts", "1", b"old draft raw")

        resp = _save(
            env,
            {
                "to": ["a@example.com"],
                "subject": "replaced",
                "body_plain": "new body",
                "replace_uid": str(inbox_id),
            },
        )
        # Either a clean replace or a loud miss is acceptable — never a dup.
        assert resp.status_code in (201, 404), resp.data
        if resp.status_code == 201:
            # old draft uid gone, exactly one draft on the server
            assert not env.server.uid_present("Drafts", "1")
            assert len(env.server.messages["Drafts"]) == 1
        # INBOX row untouched either way
        got = env.client.get(
            f"/api/v1/mail/messages/{inbox_id}",
            headers={"Authorization": f"Bearer {env.token}"},
        )
        assert got.status_code == 200

    def test_replace_uid_raw_server_uid_without_cache_row(self, qa_mail):
        """U6.5b: identifiers resolve ONLY via the Drafts rows in the mail
        cache — there is deliberately NO raw-IMAP-UID fallback. A digit
        that matches no cache row is a loud 404 DRAFT_NOT_FOUND (a raw-UID
        fallback could delete the wrong draft when the cache-id and UID
        spaces collide), so a draft the cache has never seen is not
        replaceable even though it exists on the server."""
        env = qa_mail
        env.server.seed_message("Drafts", "650", b"orphan draft raw")

        resp = _save(
            env,
            {
                "to": ["a@example.com"],
                "subject": "replaced orphan",
                "body_plain": "body",
                "replace_uid": "650",
            },
        )
        # Cache miss = loud 404, never a silent duplicate append (U6.5b).
        assert resp.status_code == 404, resp.data
        assert json.loads(resp.data)["error"]["code"] == "DRAFT_NOT_FOUND"
        # nothing appended, the orphan is untouched, cache stays empty
        assert env.server.append_count() == 0
        assert env.server.uid_present("Drafts", "650")
        assert len(env.server.messages["Drafts"]) == 1
        assert drafts_in_cache(env.cache_path) == []

    def test_replace_by_cache_id_end_to_end_no_patched_delete(self, qa_mail):
        """The literal issue #1 flow: save a draft, replace it via the cache
        message id read endpoints expose. No patching of delete_message_by_uid —
        the fake server enforces RFC 3501 semantics (STORE needs a selected
        mailbox; a NO status is not an exception).

        BUG (found by this review): save_draft deletes via STORE+EXPUNGE on
        a freshly connected, never-SELECTed client, so the delete is a
        silent no-op on a real server and the verification then rejects the
        replace with 502. The fix for #1 therefore still cannot replace
        anything when the cache resolves the draft (the primary path)."""
        env = qa_mail
        first = _save(env, {"to": ["a@example.com"], "subject": "v1", "body_plain": "v1"})
        assert first.status_code == 201
        first_uid = json.loads(first.data)["data"]["draft_uid"]
        cache_id = json.loads(first.data)["data"]["draft_id"]

        resp = _save(
            env,
            {
                "to": ["a@example.com"],
                "subject": "v2",
                "body_plain": "v2",
                "replace_uid": str(cache_id),
            },
        )
        assert resp.status_code == 201, resp.data
        assert not env.server.uid_present("Drafts", first_uid)
        assert len(env.server.messages["Drafts"]) == 1

    def test_replace_uid_nonexistent_raw_uid_is_404_no_append(self, qa_mail):
        env = qa_mail
        resp = _save(
            env,
            {
                "to": ["a@example.com"],
                "subject": "v1",
                "body_plain": "v1",
                "replace_uid": "1194",
            },
        )
        assert resp.status_code == 404, resp.data
        assert json.loads(resp.data)["error"]["code"] == "DRAFT_NOT_FOUND"
        assert env.server.append_count() == 0
        assert drafts_in_cache(env.cache_path) == []


class TestReplaceDraftInAliasedDraftsFolder:
    """(d) drafts living in an aliased folder name ('INBOX.Drafts')."""

    def test_replace_primary_invariant_no_duplicate_append(self, qa_mail):
        env = qa_mail
        # Cache thinks drafts live in INBOX.Drafts (a canonical drafts alias).
        old_id = seed_cache_message(
            env.cache_path,
            uid="800",
            folder="INBOX.Drafts",
            subject="aliased draft",
            message_id="<alias@example.com>",
        )
        # Server has BOTH mailboxes; the draft physically lives in INBOX.Drafts.
        env.server.messages["INBOX.Drafts"] = {}
        env.server.seed_message("INBOX.Drafts", "800", b"aliased draft raw")

        baseline = env.server.append_count()
        resp = _save(
            env,
            {
                "to": ["a@example.com"],
                "subject": "v2",
                "body_plain": "v2",
                "replace_uid": str(old_id),
            },
        )
        # Scenario contract: 404 or clean replace, and at most one append.
        assert resp.status_code in (201, 404), resp.data
        assert env.server.append_count() <= baseline + 1

    def test_replace_actually_removes_old_draft_from_its_real_folder(self, qa_mail):
        """Stricter end-state: after a *successful* replace, the old draft
        must be gone from the folder it actually lived in — otherwise the
        user sees duplicate drafts again after the next sync (issue #1
        symptom)."""
        env = qa_mail
        old_id = seed_cache_message(
            env.cache_path,
            uid="800",
            folder="INBOX.Drafts",
            subject="aliased draft",
            message_id="<alias@example.com>",
        )
        env.server.messages["INBOX.Drafts"] = {}
        env.server.seed_message("INBOX.Drafts", "800", b"aliased draft raw")

        resp = _save(
            env,
            {
                "to": ["a@example.com"],
                "subject": "v2",
                "body_plain": "v2",
                "replace_uid": str(old_id),
            },
        )
        assert resp.status_code == 201, resp.data
        assert not env.server.uid_present("INBOX.Drafts", "800"), (
            "replace deleted from hardcoded 'Drafts' but the old draft still "
            "lives in INBOX.Drafts — duplicate drafts will reappear on sync"
        )
