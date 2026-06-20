from app.modules.mail.services.protection import (
    LOCKED_KEYWORD,
    is_system_folder,
    folder_is_protected,
    load_protected_folders,
    set_folder_protected,
    locked_keyword_enabled,
    set_locked_keyword_enabled,
    protect_starred_enabled,
    message_is_protected,
)


class _Settings:
    def __init__(self, protected_folders=None, protect_starred=True, locked_keyword_prefs=None):
        self.protected_folders = protected_folders
        self.protect_starred = protect_starred
        self.locked_keyword_prefs = locked_keyword_prefs


class TestSystemFolders:
    def test_system_set_is_protected(self):
        for f in ("INBOX", "Sent", "Drafts", "Trash", "Junk", "Spam", "Bookings"):
            assert is_system_folder(f) is True

    def test_user_folder_not_system(self):
        assert is_system_folder("LifeLenz") is False
        assert is_system_folder("") is False
        assert is_system_folder(None) is False


class TestFolderProtection:
    def test_system_folder_always_protected(self):
        assert folder_is_protected(_Settings(), "INBOX") is True
        assert folder_is_protected(None, "Trash") is True

    def test_user_protected_list(self):
        s = _Settings(protected_folders='["LifeLenz"]')
        assert folder_is_protected(s, "LifeLenz") is True
        assert folder_is_protected(s, "lifelenz") is True
        assert folder_is_protected(s, "Other") is False

    def test_set_protected_toggles(self):
        s = _Settings()
        set_folder_protected(s, "Work", True)
        assert "Work" in load_protected_folders(s)
        set_folder_protected(s, "work", False)
        assert load_protected_folders(s) == []

    def test_invalid_json_returns_empty(self):
        assert load_protected_folders(_Settings(protected_folders="not json")) == []


class TestMessageProtection:
    def test_locked_keyword_protects(self):
        assert message_is_protected([LOCKED_KEYWORD], _Settings()) is True

    def test_starred_protects_when_enabled(self):
        assert message_is_protected(["\\Flagged"], _Settings(protect_starred=True)) is True

    def test_starred_not_protected_when_disabled(self):
        assert message_is_protected(["\\Flagged"], _Settings(protect_starred=False)) is False

    def test_plain_message_unprotected(self):
        assert message_is_protected(["\\Seen"], _Settings()) is False

    def test_protect_starred_default_true(self):
        assert protect_starred_enabled(_Settings()) is True
        assert protect_starred_enabled(_Settings(protect_starred=False)) is False


class TestLockedKeywordPrefs:
    def test_default_enabled(self):
        assert locked_keyword_enabled(_Settings(), 5) is True

    def test_disable_per_account(self):
        s = _Settings(locked_keyword_prefs='{"5": false}')
        assert locked_keyword_enabled(s, 5) is False
        assert locked_keyword_enabled(s, 6) is True

    def test_set_enabled(self):
        s = _Settings()
        set_locked_keyword_enabled(s, 7, False)
        assert locked_keyword_enabled(s, 7) is False
