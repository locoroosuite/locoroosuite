import imaplib
from unittest.mock import MagicMock

import pytest

from app.modules.mail.services.imap_client import select_folder, append_message, ensure_folder_and_append
from app.modules.mail.services.folder_aliases import (
    resolve_folder_name,
    canonical_folder_key,
)
from app.modules.mail.services.imap_sync import _resolve_folders
from app.modules.mail.services.folder_sort import build_folder_sections


class TestSelectFolder:
    def test_raises_on_no_response(self):
        client = MagicMock()
        client.select.return_value = ("NO", [b"Mailbox doesn't exist"])
        client._quote = lambda x: x
        with pytest.raises(imaplib.IMAP4.error, match="SELECT failed"):
            select_folder(client, "Sent")

    def test_raises_on_bad_response(self):
        client = MagicMock()
        client.select.return_value = ("BAD", [b"Invalid mailbox name"])
        client._quote = lambda x: x
        with pytest.raises(imaplib.IMAP4.error, match="SELECT failed"):
            select_folder(client, "Sent")

    def test_returns_ok_on_success(self):
        client = MagicMock()
        client.select.return_value = ("OK", [b"42"])
        client._quote = lambda x: x
        typ, dat = select_folder(client, "INBOX")
        assert typ == "OK"


class TestAppendMessage:
    def test_raises_on_no_response(self):
        client = MagicMock()
        client.append.return_value = ("NO", [b"[TRYCREATE] Mailbox doesn't exist"])
        with pytest.raises(imaplib.IMAP4.error, match="IMAP APPEND failed"):
            append_message(client, "Sent", b"test")

    def test_returns_ok_on_success(self):
        client = MagicMock()
        client.append.return_value = ("OK", [b"[APPENDUID 1 42] APPEND completed."])
        status, data = append_message(client, "Sent", b"test")
        assert status == "OK"


class TestEnsureFolderAndAppend:
    def test_succeeds_immediately_when_folder_exists(self):
        client = MagicMock()
        client.append.return_value = ("OK", [b"[APPENDUID 1 42] APPEND completed."])
        status, data = ensure_folder_and_append(client, "Sent", b"msg")
        assert status == "OK"
        assert client.append.call_count == 1

    def test_creates_folder_when_missing(self):
        client = MagicMock()
        ok_resp = ("OK", [b"[APPENDUID 1 42] APPEND completed."])
        client.append.side_effect = [
            imaplib.IMAP4.error("APPEND failed"),
            imaplib.IMAP4.error("APPEND failed"),
            ok_resp,
        ]
        client.list.return_value = ("OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
        ])
        client.create.return_value = ("OK", [None])

        status, data = ensure_folder_and_append(client, "Sent", b"msg")
        assert status == "OK"
        client.create.assert_called_once_with("Sent")
        assert client.append.call_count == 3

    def test_uses_existing_alias_instead_of_creating(self):
        client = MagicMock()
        ok_resp = ("OK", [b"[APPENDUID 1 42] APPEND completed."])
        client.append.side_effect = [
            imaplib.IMAP4.error("APPEND failed"),
            ok_resp,
        ]
        client.list.return_value = ("OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "Sent Items"',
        ])

        status, data = ensure_folder_and_append(client, "Sent", b"msg")
        assert status == "OK"
        assert client.append.call_args[0][0] == "Sent Items"
        client.create.assert_not_called()


class TestResolveFolderName:
    def test_exact_match(self):
        assert resolve_folder_name(["INBOX", "Sent", "Drafts"], "Sent") == "Sent"

    def test_case_insensitive(self):
        assert resolve_folder_name(["INBOX", "sent"], "Sent") == "sent"

    def test_sent_items_alias(self):
        assert resolve_folder_name(["INBOX", "Sent Items"], "Sent") == "Sent Items"

    def test_sent_messages_alias(self):
        assert resolve_folder_name(["INBOX", "Sent Messages"], "Sent") == "Sent Messages"

    def test_draft_messages_alias(self):
        assert resolve_folder_name(["INBOX", "Draft Messages"], "Drafts") == "Draft Messages"

    def test_deleted_items_alias(self):
        assert resolve_folder_name(["INBOX", "Deleted Items"], "Trash") == "Deleted Items"

    def test_spam_alias_for_junk(self):
        assert resolve_folder_name(["INBOX", "Spam"], "Junk") == "Spam"

    def test_no_match_returns_original(self):
        assert resolve_folder_name(["INBOX"], "Sent") == "Sent"

    def test_archives_alias(self):
        assert resolve_folder_name(["INBOX", "Archives"], "Archive") == "Archives"


class TestCanonicalFolderKey:
    def test_sent_items(self):
        assert canonical_folder_key("Sent Items") == "sent"

    def test_sent_messages(self):
        assert canonical_folder_key("Sent Messages") == "sent"

    def test_plain_sent(self):
        assert canonical_folder_key("Sent") == "sent"

    def test_custom_folder(self):
        assert canonical_folder_key("Projects") == "projects"

    def test_spam(self):
        assert canonical_folder_key("Spam") == "junk"


class TestResolveFolders:
    def test_resolves_sent_to_sent_items(self):
        result = _resolve_folders(["INBOX", "Sent Items"], ["Sent"])
        assert result == ["Sent Items"]

    def test_resolves_exact_match(self):
        result = _resolve_folders(["INBOX", "Sent"], ["Sent"])
        assert result == ["Sent"]

    def test_returns_all_when_no_requested(self):
        result = _resolve_folders(["INBOX", "Sent"], None)
        assert result == ["INBOX", "Sent"]

    def test_unresolved_keeps_original(self):
        result = _resolve_folders(["INBOX"], ["Sent"])
        assert result == ["Sent"]


class TestBuildFolderSections:
    def _make_conn(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = (None,)
        return conn

    def test_sent_items_in_system_section(self):
        conn = self._make_conn()
        sections = build_folder_sections(
            ["INBOX", "Sent Items", "Trash"], [], conn
        )
        system = next(s for s in sections if s["title"] == "System")
        assert "Sent Items" in system["folders"]

    def test_spam_in_system_section(self):
        conn = self._make_conn()
        sections = build_folder_sections(
            ["INBOX", "Spam", "Trash"], [], conn
        )
        system = next(s for s in sections if s["title"] == "System")
        assert "Spam" in system["folders"]

    def test_deleted_items_in_system_section(self):
        conn = self._make_conn()
        sections = build_folder_sections(
            ["INBOX", "Deleted Items", "Spam"], [], conn
        )
        system = next(s for s in sections if s["title"] == "System")
        assert "Deleted Items" in system["folders"]

    def test_plain_names_still_work(self):
        conn = self._make_conn()
        sections = build_folder_sections(
            ["INBOX", "Sent", "Drafts", "Trash", "Junk", "Archive"], [], conn
        )
        system = next(s for s in sections if s["title"] == "System")
        system_names = set(system["folders"])
        assert system_names == {"Sent", "Drafts", "Trash", "Junk", "Archive"}
