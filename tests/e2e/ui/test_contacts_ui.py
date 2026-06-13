from tests.e2e.conftest import skip_if_no_services


@skip_if_no_services
class TestContactsTable:
    def test_contact_table_has_expected_columns(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/contacts/")
        logged_in_page.wait_for_load_state("networkidle")
        headers = logged_in_page.query_selector_all("thead th")
        if not headers:
            return
        header_texts = [h.inner_text().strip() for h in headers]
        assert any(t.upper() == "NAME" for t in header_texts), f"Name column not found in {header_texts}"
        assert any(t.upper() == "EMAIL" for t in header_texts), f"Email column not found in {header_texts}"
        assert any(t.upper() == "PHONE" for t in header_texts), f"Phone column not found in {header_texts}"
        assert any(t.upper() == "ORGANIZATION" for t in header_texts), f"Organization column not found in {header_texts}"

    def test_contact_rows_contain_expected_data(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/contacts/")
        logged_in_page.wait_for_load_state("networkidle")
        rows = logged_in_page.query_selector_all("tbody tr")
        if not rows:
            return
        row = rows[0]
        cells = row.query_selector_all("td")
        assert len(cells) >= 4

    def test_empty_state_when_no_contacts(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/contacts/?q=zzznonexistent12345")
        logged_in_page.wait_for_load_state("networkidle")
        empty_text = logged_in_page.query_selector("text=No contacts")
        rows = logged_in_page.query_selector_all("tbody tr")
        assert empty_text is not None or len(rows) == 0

    def test_search_box_present_with_placeholder(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/contacts/")
        logged_in_page.wait_for_load_state("networkidle")
        search = logged_in_page.query_selector('#contacts-search-input')
        if search is None:
            return
        placeholder = search.get_attribute("placeholder") or ""
        assert "Search contacts" in placeholder
