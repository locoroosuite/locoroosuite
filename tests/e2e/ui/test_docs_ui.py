from pathlib import Path

from tests.e2e.conftest import skip_if_no_services

_PDF_FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample.pdf"


@skip_if_no_services
class TestDocsEditorConvert:
    """Guards the editor 'Convert to editable document' button.

    Regression test for a bug where, on an original-format (e.g. PDF) document,
    the editor page's script threw a TypeError attaching a handler to the
    absent ``rename-btn`` element, which aborted the IIFE before the convert
    handler was wired -- so clicking the button did nothing.
    """

    def test_pdf_editor_convert_button_navigates_and_has_no_js_errors(self, logged_in_page, app_url):
        page = logged_in_page

        # Capture any uncaught JS errors so a null-deref regression fails the
        # test even if the click happens to no-op.
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        # Upload a real PDF through the docs list's hidden file input.
        page.goto(f"{app_url}/app/docs/")
        page.wait_for_load_state("networkidle")
        page.set_input_files("#upload-input", str(_PDF_FIXTURE))

        # The upload handler redirects to the editor for the new PDF doc.
        page.wait_for_url("**/edit", timeout=30000)
        page.wait_for_load_state("networkidle")

        # The floating toolbar auto-hides; reveal it so the button is clickable.
        page.evaluate("document.getElementById('floating-bar').classList.remove('auto-hidden');")

        page.wait_for_selector("#convert-btn", state="visible", timeout=10000)
        page.click("#convert-btn")

        # Successful conversion redirects to the NEW document's editor.
        page.wait_for_url("**/edit", timeout=30000)

        # The original bug surfaced as an uncaught TypeError at the rename-btn
        # line; assert the editor script ran clean.
        rename_errors = [e for e in errors if "rename-btn" in e or "Cannot read properties of null" in e]
        assert not rename_errors, f"Editor JS errors (convert handler likely not attached): {rename_errors}"
