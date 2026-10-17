"""U5.5a: move_destination_folders — the single source for every
move-to-folder picker (folder view rows/swipe sheet/bulk toolbar, search
results, full-text search, message detail)."""

from app.modules.mail.services.folder_sort import move_destination_folders


def test_custom_first_then_system_then_inbox(cache_conn_factory):
    conn = cache_conn_factory(
        folders=["INBOX", "Sent", "Trash", "Projects", "Receipts"],
        message_dates=[("Receipts", 100)],
    )
    # Custom (dated desc, undated after) → system (canonical order) → INBOX.
    assert move_destination_folders(conn) == ["Receipts", "Projects", "Sent", "Trash", "INBOX"]


def test_empty_custom_folder_is_a_destination(cache_conn_factory):
    """Regression test: the pre-U5.5a source (SELECT DISTINCT folder FROM
    messages) hid empty custom folders, so pickers listed system folders
    only — reported against the search bulk-move dropdown."""
    conn = cache_conn_factory(folders=["INBOX", "Sent", "Projects"])
    assert "Projects" in move_destination_folders(conn)


def test_fallback_inbox_when_folders_table_empty(cache_conn_factory):
    conn = cache_conn_factory()
    assert move_destination_folders(conn) == ["INBOX"]


def test_alias_variants_kept_and_grouped(cache_conn_factory):
    """U4.15b: pickers list every canonical alias (no sidebar-style
    representative dedupe); aliases stay grouped in canonical order and
    alphabetical within a group."""
    conn = cache_conn_factory(folders=["INBOX", "Sent", "Junk", "Spam"])
    assert move_destination_folders(conn) == ["Sent", "Junk", "Spam", "INBOX"]


def test_custom_ordered_by_recent_activity(cache_conn_factory):
    conn = cache_conn_factory(
        folders=["INBOX", "Alpha", "Beta"],
        message_dates=[("Beta", 50), ("Alpha", 100)],
    )
    assert move_destination_folders(conn) == ["Alpha", "Beta", "INBOX"]


def test_undated_custom_after_dated(cache_conn_factory):
    conn = cache_conn_factory(
        folders=["INBOX", "Dated", "Zzz"],
        message_dates=[("Dated", 10)],
    )
    assert move_destination_folders(conn) == ["Dated", "Zzz", "INBOX"]
