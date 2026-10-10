from pathlib import Path

import pytest

from tests.e2e.conftest import skip_if_no_services
from tests.e2e.services import is_collabora_conversion_available

_PDF_FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.pdf"

skip_if_collabora_cannot_convert = pytest.mark.skipif(
    not is_collabora_conversion_available(),
    reason="Collabora is up but /cool/convert-to cannot convert "
    "(container likely lacks jail/mount permissions — see docker-compose.dev.yml)",
)


@skip_if_no_services
@skip_if_collabora_cannot_convert
class TestDocsEditorConvert:
    """Guards the editor 'Convert to editable document' button.

    Regression test for a bug where, on an original-format (e.g. PDF) document,
    the editor page's script threw a TypeError attaching a handler to the
    absent ``rename-btn`` element, which aborted the IIFE before the convert
    handler was wired -- so clicking the button did nothing.
    """

    def test_pdf_editor_convert_button_navigates_and_has_no_js_errors(
        self, logged_in_page, app_url
    ):
        page = logged_in_page

        # Capture any uncaught JS errors so a null-deref regression fails the
        # test even if the click happens to no-op.
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        # Upload a real PDF through the docs list's hidden file input. The
        # upload handler opens the editor in a NEW TAB (window.open), so
        # follow the popup; never wait for a same-tab navigation here.
        page.goto(f"{app_url}/app/docs/")
        page.wait_for_load_state("load")
        page.wait_for_selector("#upload-input", state="attached", timeout=10000)
        with page.expect_popup(timeout=30000) as popup_info:
            page.set_input_files("#upload-input", str(_PDF_FIXTURE))
        editor = popup_info.value
        editor.on("pageerror", lambda exc: errors.append(str(exc)))
        editor.wait_for_load_state("load")

        # U13.21 moved Convert into the kebab action menu: open it first.
        editor.wait_for_selector("#action-menu-toggle", state="visible", timeout=10000)
        editor.click("#action-menu-toggle")
        editor.wait_for_selector("#convert-btn", state="visible", timeout=10000)
        before_url = editor.url
        editor.click("#convert-btn")

        # Successful conversion navigates to the NEW document's editor
        # (a different /edit URL -- waiting for "**/edit" alone would match
        # the current page and pass even if the click no-oped).
        editor.wait_for_url(lambda url: url != before_url and url.endswith("/edit"), timeout=30000)

        # The original bug surfaced as an uncaught TypeError at the rename-btn
        # line; assert the editor script ran clean.
        rename_errors = [
            e for e in errors if "rename-btn" in e or "Cannot read properties of null" in e
        ]
        assert not rename_errors, (
            f"Editor JS errors (convert handler likely not attached): {rename_errors}"
        )
