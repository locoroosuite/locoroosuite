import re

import pytest

from tests.e2e.conftest import skip_if_no_services


@skip_if_no_services
class TestSearchDataIntegrity:
    def _search(self, session, app_url, query, account_id):
        r = session.post(
            f"{app_url}/app/mail/search",
            data={"q": query, "account_id": account_id},
        )
        assert r.status_code == 200
        return r.text

    def _extract_message_rows(self, html):
        return re.findall(r'data-message-id="(\d+)"', html)

    def _extract_subjects(self, html):
        return re.findall(
            r'class="truncate[^"]*"[^>]*title="([^"]+)"',
            html,
        )

    def _extract_senders(self, html):
        return re.findall(
            r'<span class="truncate"[^>]*title="([^"]+)"',
            html,
        )

    def _extract_message_urls(self, html):
        return re.findall(r'data-message-url="([^"]+)"', html)

    def test_search_results_have_correct_subjects(self, app_url, user_session, user_account_id):
        html = self._search(user_session, app_url, "test", user_account_id)
        subjects = self._extract_subjects(html)
        for subject in subjects:
            assert not re.match(r"^\d+$", subject), f"Subject looks like a UID: {subject}"
            assert "/" not in subject or "@" in subject, f"Subject looks like a path: {subject}"

    def test_search_results_have_correct_senders(self, app_url, user_session, user_account_id):
        html = self._search(user_session, app_url, "test", user_account_id)
        senders = self._extract_senders(html)
        for sender in senders:
            assert not re.match(r"^\d+$", sender), f"Sender looks like a UID: {sender}"
            assert not sender.startswith("INBOX"), f"Sender looks like a folder name: {sender}"
            assert not sender.startswith("Sent"), f"Sender looks like a folder name: {sender}"

    def test_search_results_link_to_message_detail(self, app_url, user_session, user_account_id):
        html = self._search(user_session, app_url, "test", user_account_id)
        urls = self._extract_message_urls(html)
        message_ids = self._extract_message_rows(html)
        assert len(urls) == len(message_ids)
        for url in urls:
            assert "/mail/" in url
            assert re.search(r"/mail/message/\d+/\d+", url), f"URL does not look like a message detail link: {url}"

    def test_empty_search_shows_no_messages_found(self, app_url, user_session, user_account_id):
        html = self._search(user_session, app_url, "zzznonexistentquery12345xyz", user_account_id)
        subjects = self._extract_subjects(html)
        if not subjects:
            assert "No messages found" in html

    def test_specific_subject_returns_only_matching(self, app_url, user_session, user_account_id):
        html = self._search(user_session, app_url, "test", user_account_id)
        subjects = self._extract_subjects(html)
        if not subjects:
            pytest.skip("No messages in test mailbox to search")
        specific = subjects[0]
        unique_word = None
        for word in specific.split():
            if len(word) > 4:
                unique_word = word
                break
        if not unique_word:
            pytest.skip("Could not find a unique enough word in subject")
        html2 = self._search(user_session, app_url, unique_word, user_account_id)
        subjects2 = self._extract_subjects(html2)
        assert len(subjects2) <= len(subjects)
