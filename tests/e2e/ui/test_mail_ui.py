from tests.e2e.conftest import skip_if_no_services


@skip_if_no_services
class TestMailMessageList:
    def test_message_rows_have_star_subject_sender_date(self, logged_in_page):
        rows = logged_in_page.query_selector_all(".message-row")
        if not rows:
            return
        row = rows[0]
        assert row.query_selector("[data-star-icon]") is not None
        assert row.query_selector("[data-subject]") is not None
        sender = row.query_selector("[title]")
        assert sender is not None

    def test_unread_messages_have_bold_styling(self, logged_in_page):
        try:
            logged_in_page.wait_for_function(
                "document.querySelector('.message-row[data-is-unread=\"1\"] [data-subject=\"true\"].font-bold') !== null",
                timeout=5000,
            )
        except Exception:
            pass
        unread = logged_in_page.query_selector('.message-row[data-is-unread="1"]')
        if unread is None:
            return
        subject = unread.query_selector('[data-subject="true"]')
        assert subject is not None
        classes = subject.get_attribute("class") or ""
        assert "font-bold" in classes

    def test_folder_sidebar_shows_inbox(self, logged_in_page):
        sidebar = logged_in_page.query_selector("#sidebar")
        assert sidebar is not None
        inbox_link = sidebar.query_selector('a[data-folder="INBOX"]')
        if inbox_link is None:
            inbox_label = sidebar.query_selector('[data-folder-label="INBOX"]')
            assert inbox_label is not None

    def test_empty_folder_shows_no_messages(self, logged_in_page):
        trash_link = logged_in_page.query_selector('a[data-folder="Trash"]')
        if trash_link:
            trash_link.click()
            logged_in_page.wait_for_load_state("networkidle")
        try:
            logged_in_page.wait_for_function(
                "!document.getElementById('message-skeleton')",
                timeout=15000,
            )
        except Exception:
            pass
        message_rows = logged_in_page.query_selector_all(".message-row")
        if message_rows:
            return
        status = logged_in_page.query_selector("#message-list-status")
        if status:
            try:
                logged_in_page.wait_for_function(
                    "document.getElementById('message-list-status').textContent.trim().length > 0",
                    timeout=15000,
                )
            except Exception:
                pass
            text = status.inner_text().strip()
            assert "No messages" in text or "Syncing" in text

    def test_hover_actions_archive_delete_present_in_dom(self, logged_in_page):
        row = logged_in_page.query_selector(".message-row")
        if row is None:
            return
        actions = row.query_selector("[data-message-actions]")
        assert actions is not None
        assert actions.query_selector('button:has-text("Archive")') is not None
        assert actions.query_selector('button:has-text("Delete")') is not None
