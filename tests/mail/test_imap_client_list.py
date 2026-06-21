from app.modules.mail.services.imap_client import _parse_list_entry, list_folders


class _MockClient:
    def __init__(self, lines):
        self._lines = lines

    def list(self):
        return "OK", self._lines


def test_list_folders_filters_noselect_and_nonexistent():
    # "\NoSelect" parents (e.g. Dovecot's phantom "dovecot" produced by an
    # in-maildir Sieve symlink) and "\NonExistent" mailboxes cannot be SELECTed
    # and must be hidden so the app never tries to sync them.
    lines = [
        b'(\\HasNoChildren) "." "INBOX"',
        b'(\\Noselect \\HasChildren) "." "dovecot"',
        b'(\\Noselect) "." "parent"',
        b'(\\NonExistent) "." "ghost"',
        b'(\\HasNoChildren) "." "Sent"',
    ]
    assert list_folders(_MockClient(lines)) == ["INBOX", "Sent"]


def test_list_folders_keeps_normal_folders():
    lines = [
        b'(\\HasNoChildren) "." "INBOX"',
        b'(\\HasNoChildren) "." "Archive"',
        b'(\\Marked) "." "Work"',
    ]
    assert list_folders(_MockClient(lines)) == ["INBOX", "Archive", "Work"]


def test_list_folders_empty_on_non_ok():
    class Client:
        def list(self):
            return "NO", []

    assert list_folders(Client()) == []


def test_parse_list_entry_flags_lowercase():
    flags, name = _parse_list_entry(b'(\\Noselect \\HasChildren) "." "dovecot"')
    assert name == "dovecot"
    assert "\\noselect" in flags
    assert "\\haschildren" in flags


def test_parse_list_entry_quoted_name():
    _flags, name = _parse_list_entry(b'(\\HasNoChildren) "/" "Foo/Bar"')
    assert name == "Foo/Bar"
