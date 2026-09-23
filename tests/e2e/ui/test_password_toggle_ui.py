from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import APP_URL


@skip_if_no_services
class TestPasswordToggleUI:
    def test_login_password_toggle(self, page):
        page.goto(f"{APP_URL}/app/login")
        page.wait_for_load_state("networkidle")

        password_input = page.query_selector('input[name="password"]')
        assert password_input is not None
        assert password_input.get_attribute("type") == "password"

        toggle = page.query_selector('button[aria-label="Show password"]')
        assert toggle is not None

        page.fill('input[name="password"]', "s3cret-value")
        toggle.click()

        assert password_input.get_attribute("type") == "text"
        assert password_input.input_value() == "s3cret-value"

        hide_button = page.query_selector('button[aria-label="Hide password"]')
        assert hide_button is not None
        hide_button.click()
        assert password_input.get_attribute("type") == "password"
