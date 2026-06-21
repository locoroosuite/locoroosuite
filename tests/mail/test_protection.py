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
    protection_reason,
    protected_delete_message,
    protected_badge_label,
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


class TestProtectionReason:
    def test_none_when_unprotected(self):
        assert protection_reason(["\\Seen"], _Settings()) is None
        assert protection_reason(None, _Settings()) is None

    def test_locked_reason(self):
        assert protection_reason([LOCKED_KEYWORD], _Settings()) == "locked"

    def test_locked_protects_even_when_protect_starred_off(self):
        assert protection_reason([LOCKED_KEYWORD], _Settings(protect_starred=False)) == "locked"

    def test_starred_reason(self):
        assert protection_reason(["\\Flagged"], _Settings(protect_starred=True)) == "starred"

    def test_starred_not_a_reason_when_policy_off(self):
        assert protection_reason(["\\Flagged"], _Settings(protect_starred=False)) is None

    def test_starred_and_locked_reason(self):
        assert protection_reason(["\\Flagged", LOCKED_KEYWORD], _Settings()) == "starred+locked"

    def test_predicate_matches_reason(self):
        # message_is_protected must agree with protection_reason across all cases
        cases = [
            (["\\Seen"], _Settings()),
            ([LOCKED_KEYWORD], _Settings(protect_starred=False)),
            (["\\Flagged"], _Settings(protect_starred=True)),
            (["\\Flagged", LOCKED_KEYWORD], _Settings()),
            (["\\Flagged"], _Settings(protect_starred=False)),
        ]
        for flags, settings in cases:
            reason = protection_reason(flags, settings)
            assert message_is_protected(flags, settings) == (reason is not None), (
                f"predicate/reason mismatch for flags={flags}"
            )


class TestProtectedDeleteMessage:
    def test_locked_message_is_actionable(self):
        msg = protected_delete_message("locked")
        assert "locked" in msg.lower()
        assert "unlock" in msg.lower()
        # protection errors must not suggest a retry
        assert "retry" not in msg.lower()

    def test_starred_message_is_actionable(self):
        msg = protected_delete_message("starred")
        assert "starred" in msg.lower()
        assert "unstar" in msg.lower()
        assert "retry" not in msg.lower()

    def test_starred_and_locked_message_names_both(self):
        msg = protected_delete_message("starred+locked")
        assert "starred" in msg.lower()
        assert "locked" in msg.lower()
        assert "unstar" in msg.lower()
        assert "unlock" in msg.lower()
        assert "retry" not in msg.lower()

    def test_each_reason_mentions_the_menu(self):
        for reason in ("locked", "starred", "starred+locked"):
            assert "\u22ef" in protected_delete_message(reason)


class TestProtectedBadgeLabel:
    def test_labels_are_distinct_and_prefixed(self):
        assert protected_badge_label("locked").startswith("Protected:")
        assert "locked" in protected_badge_label("locked")
        assert "starred" in protected_badge_label("starred")
        assert "starred" in protected_badge_label("starred+locked")
        assert "locked" in protected_badge_label("starred+locked")


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
