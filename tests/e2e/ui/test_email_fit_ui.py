"""UX8a e2e: fixed-width emails fit the mobile viewport without horizontal
scrolling, and the fit toggle appears when auto-scaling engaged."""

import uuid

import pytest

from tests.e2e.conftest import skip_if_no_services

# A legacy fixed-layout email: nowrap table cell forces a min-content width
# far above a phone viewport, so only the wrapper's fit machinery can make
# it fit (CSS reflow alone cannot break white-space:nowrap content).
FIXED_WIDTH_BODY = (
    '<table width="700" cellspacing="0" cellpadding="0" border="0">'
    '<tr><td style="font-size:16px;padding:20px;white-space:nowrap;">'
    + ("FIXED&nbsp;LAYOUT&nbsp;NO&nbsp;WRAP&nbsp;" * 8)
    + "</td></tr></table>"
)


@pytest.fixture(scope="function")
def seeded_fixed_width_message():
    from tests.e2e.services import (
        APP_URL,
        E2E_DEFAULT_PASSWORD,
        get_account_id,
        imap_folder_has_message,
        login_session,
        wait_for,
    )

    subject = f"E2E Fit {uuid.uuid4().hex[:8]}"
    session = login_session("e2e-test@test.localhost")
    account_id = get_account_id(APP_URL, session)
    r = session.post(
        f"{APP_URL}/app/mail/send",
        data={
            "account_id": account_id,
            "to": "e2e-test@test.localhost",
            "subject": subject,
            "body_html": FIXED_WIDTH_BODY.replace("E2E", subject),
        },
        allow_redirects=True,
    )
    assert r.status_code == 200, f"send failed: {r.status_code}"
    assert imap_folder_has_message(
        "e2e-test@test.localhost",
        E2E_DEFAULT_PASSWORD,
        "INBOX",
        subject_contains=subject,
        timeout=30,
    )

    def folder_lists_subject() -> bool:
        resp = session.get(f"{APP_URL}/app/mail/folder/{account_id}/INBOX")
        return resp.status_code == 200 and subject in resp.text

    wait_for(folder_lists_subject, timeout=30)
    return {"subject": subject, "account_id": account_id}


@skip_if_no_services
class TestEmailBodyFitMobileUi:
    def test_fixed_width_body_fits_mobile_viewport(
        self, seeded_fixed_width_message, mobile_logged_in_page
    ):
        page = mobile_logged_in_page
        info = seeded_fixed_width_message
        page.goto(f"http://localhost:8001/app/mail/folder/{info['account_id']}/INBOX")
        row = page.wait_for_selector(f".message-row:has-text('{info['subject']}')", timeout=15000)
        assert row is not None
        row.click()
        page.wait_for_url("**/mail/message/**", timeout=10000)
        page.wait_for_selector(".thread-msg-body", timeout=10000)
        # The body iframe is same-origin (srcdoc + allow-same-origin): verify
        # at the service level that the rendered document fits its viewport.
        page.wait_for_function(
            """() => {
              const f = document.querySelector('.thread-msg-body');
              const d = f && f.contentDocument;
              if (!d || !d.querySelector('.email-body')) return false;
              return d.documentElement.scrollWidth <= d.documentElement.clientWidth;
            }""",
            timeout=10000,
        )
        # The nowrap content cannot reflow, so auto-scale must have engaged
        # and exposed the fit/actual-size toggle (UX8a).
        controls = page.wait_for_selector("[data-fit-controls]:not(.hidden)", timeout=5000)
        assert controls is not None
        toggle = controls.query_selector("[data-fit-toggle]")
        assert toggle is not None
        assert toggle.is_visible()

    def test_fit_toggle_switches_to_actual_size(
        self, seeded_fixed_width_message, mobile_logged_in_page
    ):
        page = mobile_logged_in_page
        info = seeded_fixed_width_message
        page.goto(f"http://localhost:8001/app/mail/folder/{info['account_id']}/INBOX")
        row = page.wait_for_selector(f".message-row:has-text('{info['subject']}')", timeout=15000)
        row.click()
        page.wait_for_url("**/mail/message/**", timeout=10000)
        page.wait_for_selector(".thread-msg-body", timeout=10000)
        page.wait_for_function(
            """() => {
              const f = document.querySelector('.thread-msg-body');
              const d = f && f.contentDocument;
              if (!d || !d.querySelector('.email-body')) return false;
              const wrap = d.querySelector('.email-body');
              return !!wrap.style.transform;
            }""",
            timeout=10000,
        )
        # Fitted by default; toggling reveals the natural (overflowing) width.
        page.click("[data-fit-toggle]")
        page.wait_for_function(
            """() => {
              const f = document.querySelector('.thread-msg-body');
              const d = f && f.contentDocument;
              if (!d || !d.querySelector('.email-body')) return false;
              const wrap = d.querySelector('.email-body');
              return !wrap.style.transform
                && d.documentElement.scrollWidth > d.documentElement.clientWidth;
            }""",
            timeout=10000,
        )
        # And back to fitted.
        page.click("[data-fit-toggle]")
        page.wait_for_function(
            """() => {
              const f = document.querySelector('.thread-msg-body');
              const d = f && f.contentDocument;
              if (!d || !d.querySelector('.email-body')) return false;
              return !!d.querySelector('.email-body').style.transform;
            }""",
            timeout=10000,
        )
