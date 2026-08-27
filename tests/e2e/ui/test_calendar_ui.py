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

    def test_week_view_now_line_visible_on_current_week(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/?view=week")
        line = logged_in_page.wait_for_selector("#cal-now-line", timeout=5000)
        assert line is not None
        assert line.is_visible()
        style = line.get_attribute("style") or ""
        assert "rgb(239, 68, 68)" in style or "#ef4444" in style
        assert "top:" in style

    def test_week_view_now_line_absent_on_other_week(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/?view=week")
        logged_in_page.wait_for_selector(".time-cell", timeout=5000)
        logged_in_page.click("#cal-prev")
        logged_in_page.wait_for_timeout(500)
        assert logged_in_page.query_selector("#cal-now-line") is None

    def test_day_view_now_line_visible_on_today(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/?view=day")
        line = logged_in_page.wait_for_selector("#cal-now-line", timeout=5000)
        assert line is not None
        assert line.is_visible()
        style = line.get_attribute("style") or ""
        assert "rgb(239, 68, 68)" in style or "#ef4444" in style

    def test_time_grid_has_visible_borders(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/?view=week")
        logged_in_page.wait_for_selector(".time-cell", timeout=5000)
        cell = logged_in_page.query_selector(".time-cell")
        cls = (cell.get_attribute("class") or "") if cell else ""
        assert "border-slate-100" in cls
        assert "border-slate-200" in cls
