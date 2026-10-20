"""U4.7b e2e: revealing trimmed (quoted) content grows the message body
iframe to fit — the quoted part must not scroll inside a ~5-line frame."""

import uuid

import pytest

from tests.e2e.conftest import skip_if_no_services

# Short visible body + a long quoted block: add_quoted_collapse wraps the
# blockquote in <details> (collapsed on load). The quoted part is tall
# enough that opening it must more than double the iframe height.
QUOTED_LINES = "<br>".join(f"Quoted line {i} with some padding text." for i in range(1, 31))
QUOTED_BODY = f"<p>One-liner reply body.</p><blockquote><p>{QUOTED_LINES}</p></blockquote>"

# The thread stack renders every copy of the message oldest-first and only
# the current one expanded; send-to-self threads (Sent + INBOX copies) put
# the collapsed copy first in the DOM, so always pick the expanded frame.
PICK_FRAME_JS = """(() => {
  const body = [...document.querySelectorAll('[data-thread-body]')].find(
    (el) => !el.classList.contains('hidden')
  );
  return body ? body.querySelector('.thread-msg-body') : null;
})()"""


@pytest.fixture(scope="function")
def seeded_quoted_message():
    from tests.e2e.services import (
        APP_URL,
        E2E_DEFAULT_PASSWORD,
        get_account_id,
        imap_folder_has_message,
        login_session,
        wait_for,
    )

    subject = f"E2E Trim {uuid.uuid4().hex[:8]}"
    session = login_session("e2e-test@test.localhost")
    account_id = get_account_id(APP_URL, session)
    r = session.post(
        f"{APP_URL}/app/mail/send",
        data={
            "account_id": account_id,
            "to": "e2e-test@test.localhost",
            "subject": subject,
            "body_html": QUOTED_BODY,
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
class TestQuotedToggleResizesIframe:
    def test_show_trimmed_content_grows_iframe(self, seeded_quoted_message, logged_in_page):
        page = logged_in_page
        info = seeded_quoted_message
        page.goto(f"http://localhost:8001/app/mail/folder/{info['account_id']}/INBOX")
        row = page.wait_for_selector(f".message-row:has-text('{info['subject']}')", timeout=15000)
        assert row is not None
        row.click()
        page.wait_for_url("**/mail/message/**", timeout=10000)
        # The expanded (current) message's body iframe.
        page.wait_for_selector("[data-thread-body]:not(.hidden) .thread-msg-body", timeout=10000)
        # Collapsed on load; the parent has applied the initial (small)
        # height — the iframe's inline style.height is set by the parent
        # only after the first lrIframeHeight message.
        page.wait_for_function(
            """() => {
              const body = [...document.querySelectorAll('[data-thread-body]')].find(
                (el) => !el.classList.contains('hidden')
              );
              const f = body && body.querySelector('.thread-msg-body');
              const d = f && f.contentDocument;
              return !!(d && d.querySelector('details.lr-quoted:not([open])')
                && f.style.height);
            }""",
            timeout=10000,
        )
        # Settle past the parent's load-armed re-measure (lrResizeIframe on
        # iframe load + a ~300ms delayed second pass that reads
        # contentDocument.scrollHeight directly). Clicking before that timer
        # fires lets it measure the already-open details and mask the bug
        # this test guards — the baseline must be stable first.
        page.wait_for_timeout(700)
        collapsed_height = page.evaluate(f"() => ({PICK_FRAME_JS}).offsetHeight")
        # Click "Show trimmed content" inside the same-origin srcdoc iframe
        # (sandbox includes allow-same-origin).
        page.evaluate(
            """() => {
              const body = [...document.querySelectorAll('[data-thread-body]')].find(
                (el) => !el.classList.contains('hidden')
              );
              const f = body && body.querySelector('.thread-msg-body');
              f.contentDocument.querySelector('.lr-quoted-toggle').click();
            }"""
        )
        # U4.7b: the iframe grows to fit the revealed quoted content —
        # ~30 quoted lines at 14px/1.5 must clear 350px.
        page.wait_for_function(
            """() => {
              const body = [...document.querySelectorAll('[data-thread-body]')].find(
                (el) => !el.classList.contains('hidden')
              );
              const f = body && body.querySelector('.thread-msg-body');
              const d = f && f.contentDocument;
              return !!(d && d.querySelector('details.lr-quoted[open]')
                && f.offsetHeight > 350);
            }""",
            timeout=10000,
        )
        open_height = page.evaluate(f"() => ({PICK_FRAME_JS}).offsetHeight")
        assert open_height > collapsed_height * 2

        # Toggling back shrinks the iframe again (no stale tall frame).
        page.evaluate(
            """() => {
              const body = [...document.querySelectorAll('[data-thread-body]')].find(
                (el) => !el.classList.contains('hidden')
              );
              const f = body && body.querySelector('.thread-msg-body');
              f.contentDocument.querySelector('.lr-quoted-toggle').click();
            }"""
        )
        page.wait_for_function(
            """(h) => {
              const body = [...document.querySelectorAll('[data-thread-body]')].find(
                (el) => !el.classList.contains('hidden')
              );
              const f = body && body.querySelector('.thread-msg-body');
              return !!f && f.offsetHeight < h;
            }""",
            arg=open_height - 100,
            timeout=10000,
        )
