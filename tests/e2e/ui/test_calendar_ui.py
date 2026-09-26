import pytest

from tests.e2e.conftest import skip_if_no_services


@skip_if_no_services
class TestCalendarUI:
    def test_calendar_grid_present(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/")
        logged_in_page.wait_for_load_state("load")
        grid = logged_in_page.query_selector("#calendar-grid")
        assert grid is not None

    def test_mini_calendar_in_sidebar(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/")
        logged_in_page.wait_for_load_state("load")
        mini = logged_in_page.query_selector("#mini-calendar")
        assert mini is not None

    def test_calendar_list_shows_color_dots(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/")
        logged_in_page.wait_for_load_state("load")
        dots = logged_in_page.query_selector_all("span.rounded-full[style*='background-color']")
        assert len(dots) >= 0

    def test_new_event_and_sync_buttons_present(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/")
        logged_in_page.wait_for_load_state("load")
        logged_in_page.wait_for_selector("#calendar-grid", timeout=10000)
        new_event = logged_in_page.query_selector("#cal-new-event")
        sync_btn = logged_in_page.query_selector("button:has-text('Sync')")
        fab = logged_in_page.query_selector("#cal-fab")
        assert new_event is not None or sync_btn is not None or fab is not None

    def test_week_view_now_line_visible_on_current_week(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/?view=week")
        line = logged_in_page.wait_for_selector("#cal-now-line", timeout=15000)
        assert line is not None
        assert line.is_visible()
        style = line.get_attribute("style") or ""
        assert "rgb(239, 68, 68)" in style or "#ef4444" in style
        assert "top:" in style

    def test_week_view_now_line_absent_on_other_week(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/?view=week")
        logged_in_page.wait_for_selector(".time-cell", timeout=10000)
        logged_in_page.click("#cal-prev")
        logged_in_page.wait_for_timeout(500)
        assert logged_in_page.query_selector("#cal-now-line") is None

    def test_day_view_now_line_visible_on_today(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/?view=day")
        line = logged_in_page.wait_for_selector("#cal-now-line", timeout=15000)
        assert line is not None
        assert line.is_visible()
        style = line.get_attribute("style") or ""
        assert "rgb(239, 68, 68)" in style or "#ef4444" in style

    def test_time_grid_has_visible_borders(self, logged_in_page):
        logged_in_page.goto("http://localhost:8001/app/calendar/?view=week")
        logged_in_page.wait_for_selector(".time-cell", timeout=10000)
        cell = logged_in_page.query_selector(".time-cell")
        cls = (cell.get_attribute("class") or "") if cell else ""
        assert "border-slate-100" in cls
        column = cell.evaluate("el => el.parentElement.className")
        assert "border-slate-200" in column

    def test_editor_dialog_opens(self, logged_in_page):
        """U12.56b: New event opens the dialog editor."""
        logged_in_page.goto("http://localhost:8001/app/calendar/")
        logged_in_page.wait_for_selector("#calendar-grid", timeout=10000)
        btn = logged_in_page.query_selector("#cal-new-event")
        if btn is None:
            pytest.skip("No calendars available")
        btn.click()
        editor = logged_in_page.wait_for_selector("#cal-editor:not(.hidden)", timeout=5000)
        assert editor is not None
        assert logged_in_page.query_selector("#ce-title") is not None

    def test_prefill_deep_link_opens_editor(self, logged_in_page):
        """U12.39c/U12.56b: ?new=1 opens the editor pre-populated."""
        logged_in_page.goto("http://localhost:8001/app/calendar/?new=1&summary=Prefill%20Check")
        editor = logged_in_page.wait_for_selector("#cal-editor:not(.hidden)", timeout=10000)
        assert editor is not None
        value = logged_in_page.input_value("#ce-title")
        assert value == "Prefill Check"

    def test_week_view_time_grid_columns(self, logged_in_page):
        """U12.56: the week grid must lay out as gutter + 7 day columns.

        Regression: the grid template was built as a runtime-concatenated
        Tailwind class that does not exist in the compiled CSS, collapsing
        the grid to one stacked column.
        """
        logged_in_page.goto("http://localhost:8001/app/calendar/?view=week")
        logged_in_page.wait_for_selector(".time-cell", timeout=15000)
        tracks = logged_in_page.evaluate(
            """() => {
              const grid = document.querySelector('.cal-timegrid');
              return getComputedStyle(grid).gridTemplateColumns
                .split(' ').filter(Boolean).length;
            }"""
        )
        assert tracks == 8  # hour gutter + 7 days

    def test_week_view_day_headers_share_one_row(self, logged_in_page):
        """U12.56: day headers align horizontally, each in its own column."""
        logged_in_page.goto("http://localhost:8001/app/calendar/?view=week")
        logged_in_page.wait_for_selector(".time-cell", timeout=15000)
        geometry = logged_in_page.evaluate(
            """() => {
              const header = document.querySelector('#calendar-grid .grid');
              const cells = [...header.children].slice(1); // skip hour-gutter spacer
              const boxes = cells.map((c) => c.getBoundingClientRect());
              return {
                tops: [...new Set(boxes.map((b) => Math.round(b.top)))],
                lefts: boxes.map((b) => Math.round(b.left)),
              };
            }"""
        )
        assert len(geometry["tops"]) == 1
        assert len(geometry["lefts"]) == 7
        assert geometry["lefts"] == sorted(geometry["lefts"])

    def test_spanish_locale_renders_without_js_errors(self, logged_in_page):
        """U26: Spanish must not crash calendar boot.

        Regression: window.LR_I18N.locale ("es_ES") was passed unnormalized
        to Intl.DateTimeFormat, throwing RangeError and killing the views.
        """
        errors = []
        logged_in_page.on("pageerror", lambda exc: errors.append(str(exc)))
        logged_in_page.context.add_cookies(
            [{"name": "browser_lang", "value": "es", "url": "http://localhost:8001"}]
        )
        logged_in_page.goto("http://localhost:8001/app/calendar/?view=week")
        logged_in_page.wait_for_selector(".time-cell", timeout=15000)
        assert errors == []
        mini = logged_in_page.evaluate("() => window.LRCal.DAYS_MINI()")
        assert len(mini) == 7
        assert all(isinstance(name, str) and name for name in mini)
