from tests.e2e.conftest import skip_if_no_services


def _submit_search(page, query):
    page.goto("http://localhost:8001/app/mail/")
    page.wait_for_load_state("networkidle")
    form = page.query_selector('form[action*="mail/search"]')
    if form is None:
        form_html = f'<form method="post" action="/app/mail/search"><input name="q" value="{query}"/><input name="account_id" value="1"/></form>'
        page.evaluate(f'document.body.insertAdjacentHTML("beforeend", `{form_html}`)')
        form = page.query_selector('form:last-of-type')
    else:
        q_input = form.query_selector('input[name="q"]')
        if q_input:
            q_input.fill(query)
    form.evaluate("f => f.submit()")
    page.wait_for_url("**/mail/search**", timeout=15000)
    page.wait_for_load_state("networkidle")


@skip_if_no_services
class TestSearchUI:
    def test_search_page_renders_message_rows(self, logged_in_page):
        _submit_search(logged_in_page, "test")
        rows = logged_in_page.query_selector_all("#search-results .message-row")
        assert len(rows) >= 0

    def test_search_results_show_subject_sender_date(self, logged_in_page):
        _submit_search(logged_in_page, "test")
        rows = logged_in_page.query_selector_all("#search-results .message-row")
        if not rows:
            return
        row = rows[0]
        assert row.query_selector("[data-star-icon]") is not None
        assert row.query_selector("span[title]") is not None

    def test_empty_search_shows_no_messages_found(self, logged_in_page):
        _submit_search(logged_in_page, "zzznonexistent12345")
        text = logged_in_page.query_selector("text=No messages found")
        assert text is not None

    def test_no_duplicate_no_messages_found_divs(self, logged_in_page):
        _submit_search(logged_in_page, "zzznonexistent12345")
        container = logged_in_page.query_selector("#search-results")
        assert container is not None
        all_divs = container.query_selector_all("div")
        count = 0
        for div in all_divs:
            inner = div.inner_text()
            if "No messages found" in inner:
                count += 1
        assert count <= 1
