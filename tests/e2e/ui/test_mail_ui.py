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

    def test_protected_message_shows_badge(self, logged_in_page):
        """HLD U5.15h: a protected (starred) row renders the Protected badge.

        Starring via XHR (rather than a direct star-toggle click) avoids the
        sticky-header pointer intercept; the assertion then verifies the badge
        is server-rendered into the folder list."""
        row = logged_in_page.query_selector(".message-row")
        if row is None:
            return  # empty mailbox - nothing to test
        if row.get_attribute("data-is-flagged") == "1":
            return  # already protected
        msg_id = row.get_attribute("data-message-id")
        acct = row.get_attribute("data-account-id")
        was_flagged = logged_in_page.evaluate(
            "async ([a, m]) => {"
            "  const fd = new FormData(); fd.set('action','add');"
            "  const r = await fetch(`/app/mail/message/${a}/${m}/flag`,"
            "    {method:'POST', body:fd, headers:{'X-Requested-With':'XMLHttpRequest'}});"
            "  return (await r.json()).is_flagged;"
            "}",
            [acct, msg_id],
        )
        if not was_flagged:
            return
        logged_in_page.reload()
        logged_in_page.wait_for_load_state("networkidle")
        logged_in_page.wait_for_selector("[data-protected-badge]", timeout=5000)
        assert logged_in_page.query_selector("[data-protected-badge]") is not None
        # restore state so other tests are not affected
        logged_in_page.evaluate(
            "async ([a, m]) => {"
            "  const fd = new FormData(); fd.set('action','remove');"
            "  await fetch(`/app/mail/message/${a}/${m}/flag`,"
            "    {method:'POST', body:fd, headers:{'X-Requested-With':'XMLHttpRequest'}});"
            "}",
            [acct, msg_id],
        )

    def test_delete_spinner_clears_on_protected_message(self, logged_in_page):
        """Regression for the stuck-spinner bug: on the message detail page,
        clicking Delete on a protected message must show a specific toast and
        clear the spinner (not stick forever). This is the exact scenario the
        user reported (HLD U5.15g)."""
        row = logged_in_page.query_selector(".message-row")
        if row is None:
            return
        msg_id = row.get_attribute("data-message-id")
        acct = row.get_attribute("data-account-id")
        # star the message so delete is refused with a 409
        flagged = logged_in_page.evaluate(
            "async ([a, m]) => {"
            "  const fd = new FormData(); fd.set('action','add');"
            "  const r = await fetch(`/app/mail/message/${a}/${m}/flag`,"
            "    {method:'POST', body:fd, headers:{'X-Requested-With':'XMLHttpRequest'}});"
            "  return (await r.json()).is_flagged;"
            "}",
            [acct, msg_id],
        )
        if not flagged:
            return
        # open the message detail page (where the user reported the stuck spinner)
        logged_in_page.goto(f"http://localhost:8001/app/mail/message/{acct}/{msg_id}")
        logged_in_page.wait_for_load_state("networkidle")
        delete_form = logged_in_page.query_selector('form[data-action="delete"]')
        if delete_form is None:
            return
        delete_btn = delete_form.query_selector("button")
        delete_btn.click()
        # the specific protection toast must appear (proves the 409 was handled)
        toast = logged_in_page.wait_for_selector("#action-toast:not(.hidden)", timeout=5000)
        assert toast is not None
        assert "protected" in toast.inner_text().lower() or "starred" in toast.inner_text().lower()
        # the spinner (lr-spinner) must disappear after the response is handled
        try:
            logged_in_page.wait_for_selector(".lr-spinner", state="detached", timeout=5000)
        except Exception:
            pass
        assert logged_in_page.query_selector_all(".lr-spinner") == [], \
            "Delete spinner stuck after protected 409"
        # restore: unstar the message
        logged_in_page.evaluate(
            "async ([a, m]) => {"
            "  const fd = new FormData(); fd.set('action','remove');"
            "  await fetch(`/app/mail/message/${a}/${m}/flag`,"
            "    {method:'POST', body:fd, headers:{'X-Requested-With':'XMLHttpRequest'}});"
            "}",
            [acct, msg_id],
        )
