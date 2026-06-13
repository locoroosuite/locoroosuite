from tests.e2e.conftest import skip_if_no_services


@skip_if_no_services
class TestLayoutUI:
    def test_header_shows_locoroomail_logo(self, logged_in_page):
        logo = logged_in_page.query_selector("text=LocoRoomail")
        assert logo is not None

    def test_app_launcher_button_exists(self, logged_in_page):
        btn = logged_in_page.query_selector("#app-launcher-button")
        assert btn is not None

    def test_search_bar_present(self, logged_in_page):
        search = logged_in_page.query_selector('input[name="q"][type="search"]')
        assert search is not None
        placeholder = search.get_attribute("placeholder") or ""
        assert "Search" in placeholder

    def test_user_menu_button_exists(self, logged_in_page):
        btn = logged_in_page.query_selector("#user-menu-button")
        assert btn is not None

    def test_module_switcher_links_exist(self, logged_in_page):
        launcher_btn = logged_in_page.query_selector("#app-launcher-button")
        assert launcher_btn is not None
        launcher_btn.click()
        panel = logged_in_page.query_selector("#app-launcher-panel")
        assert panel is not None
        assert panel.query_selector("text=Mail") is not None
        assert panel.query_selector("text=Contacts") is not None
        assert panel.query_selector("text=Calendar") is not None
        assert panel.query_selector("text=Docs") is not None

    def test_clicking_mail_link_navigates_to_mail(self, logged_in_page):
        launcher_btn = logged_in_page.query_selector("#app-launcher-button")
        launcher_btn.click()
        mail_link = logged_in_page.query_selector("#app-launcher-panel a:has-text('Mail')")
        assert mail_link is not None
        mail_link.click()
        logged_in_page.wait_for_url("**/mail/**", timeout=10000)

    def test_clicking_contacts_link_navigates_to_contacts(self, logged_in_page):
        launcher_btn = logged_in_page.query_selector("#app-launcher-button")
        launcher_btn.click()
        contacts_link = logged_in_page.query_selector("#app-launcher-panel a:has-text('Contacts')")
        assert contacts_link is not None
        contacts_link.click()
        logged_in_page.wait_for_url("**/contacts/**", timeout=10000)
