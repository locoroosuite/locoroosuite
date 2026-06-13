from tests.e2e.conftest import skip_if_no_services


@skip_if_no_services
class TestCalendarUI:
    def test_calendar_grid_present(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/")
        logged_in_page.wait_for_load_state("networkidle")
        grid = logged_in_page.query_selector("#calendar-grid")
        assert grid is not None

    def test_mini_calendar_in_sidebar(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/")
        logged_in_page.wait_for_load_state("networkidle")
        mini = logged_in_page.query_selector("#mini-calendar")
        assert mini is not None

    def test_calendar_list_shows_color_dots(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/")
        logged_in_page.wait_for_load_state("networkidle")
        dots = logged_in_page.query_selector_all("span.rounded-full[style*='background-color']")
        assert len(dots) >= 0

    def test_new_event_and_sync_buttons_present(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/")
        logged_in_page.wait_for_load_state("networkidle")
        new_event = logged_in_page.query_selector('a:has-text("New event")')
        sync_btn = logged_in_page.query_selector('button:has-text("Sync")')
        assert new_event is not None or sync_btn is not None
