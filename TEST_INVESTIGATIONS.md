# Test Investigations

Flaky / unresolved test failures that need a dedicated investigation before a
fix is attempted. Each entry contains everything another agent needs to pick
up the work: reproduction steps, failure evidence, code pointers, ruled-out
hypotheses, and the next diagnostic step.

**Do not "fix" by adding retries or skipping.** Root-cause first (see
`AGENTS.md` → Bug fixes always require test changes).

---

## 1. `tests/e2e/test_mail.py::TestSendingLimit::test_sending_limit_enforced` — RESOLVED (2026-09-26)

- **Status:** resolved — root-caused with live evidence; fixes deployed and
  verified (full-suite runs pass, see below).

### Root cause (two independent defects, both fixed)

Reproduced during the investigation session (full-suite run 2026-09-26
11:02). Dovecot's own log pinned it:

```
Sep 26 11:02:32 auth: ... passwd-file(e2e-limit-f2dde6b6@test.localhost):
    lookup: user=e2e-limit-f2dde6b6@test.localhost file=/var/lib/dovecot-users/passwd
Sep 26 11:02:32 auth: ... passwd-file(e2e-limit-f2dde6b6@test.localhost):
    unknown user
```

The user **existed in the passwd file** (mail-api confirmed it; the app's IMAP
login is the only step that failed). Dovecot served a stale in-memory copy of
the passwd file.

**Defect A — Dovecot's once-per-second sync gate (trigger).** Dovecot 2.3.20
`src/auth/db-passwd-file.c: passwd_file_sync()`:

```c
if (pw->last_sync_time == ioloop_time)   /* stat at most once per second */
    return ...;                          /* serve cached copy */
```

The full suite's background IMAP workers (from ~50 earlier tests) keep an
auth lookup running almost every second. When mail-api's write lands *in the
same second* as one of those syncs, the sending-limit test's login lookup is
gated, skips the stat, and serves a cache that can be minutes old →
`AUTHENTICATIONFAILED`. Standalone runs pass because without the background
IMAP churn the login's own sync is the first in its second and re-parses.
This is Dovecot design (not configurable), so the fix belongs in the test:
synchronize on **Dovecot auth readiness**, not on mail-api's view of the file.

**Defect B — mail-api's non-atomic passwd rewrite (hazard).**
`mail-api/managers/dovecot.py:_write_users()` truncated the file in place and
rewrote it line by line. A Dovecot sync landing inside that window re-parses
a **partial** file and caches it (for up to a second, or until the next
write) — the same `unknown user` symptom, plus `Password mismatch` when a
half-written hash line still parses. Also: the threaded Flask dev server
(`app.run(debug=True)`) allows concurrent read-modify-write cycles → lost
updates.

### Fixes

| Fix | Where |
| --- | --- |
| Atomic passwd writes (temp file + `fsync` + `os.replace`, cleanup on failure) and a `threading.Lock` serializing all read-modify-write mutations | `mail-api/managers/dovecot.py` |
| `wait_for_imap_login()` helper: block until Dovecot accepts IMAP login for a user (service-level readiness), used by `TestSendingLimit` **and** `setup_e2e_users` (the session-setup login hit the same 1s window and aborted whole-suite runs with "E2E user setup failed") | `tests/e2e/services.py` |
| Real login diagnostics: scrape the actual error markup (`text-red-600` div from `login.html`), not an `alert` block that never exists — failures now say `page_error='IMAP authentication failed.'` | `login_session()` in `tests/e2e/services.py` |
| Test ordering: `wait_for(mailapi_user_exists(...))` before `_set_sending_limit` / login | `tests/e2e/test_mail.py` |

Note: the original hypothesis "`_set_sending_limit` rewrites the passwd
entry" was **disproven** — sending limits live in a SQLite DB
(`mail-api/server.py:SENDING_LIMITS_DB`), not the passwd file.

### Regression tests added

- `tests/mail_api/test_dovecot_manager.py::TestAtomicWrites` — deterministic
  `os.replace` spy (fails if the file is ever written in place), failed-write
  leaves the original intact + no temp litter, concurrent-reader stress
  (partial reads impossible), concurrent-writer stress (no lost updates).
- `tests/mail_api/test_dovecot_manager.py::TestUserCrud` + real-file-backed
  endpoint tests in `tests/mail_api/test_server_users.py` (mail-api had zero
  coverage of the real file path).

### Verification

- Reproduced pre-fix (1 failed / 49 passed, 2026-09-26 11:02 run).
- Post-fix: `TestSendingLimit` standalone passes; full e2e suite
  (`tests/e2e/ --ignore=tests/e2e/ui`) passes twice consecutively.

---

## 2. `tests/e2e/ui/test_docs_ui.py::TestDocsEditorConvert::test_pdf_editor_convert_button_navigates_and_has_no_js_errors` — RESOLVED (2026-09-26)

- **Status:** resolved — three stacked causes; all fixed and verified.

### Root causes (in discovery order)

1. **Container permissions (as suspected).** The Collabora container
   (`collabora/code:latest`, CODE 25.04.9.4) could not spawn jailed convert
   workers: `coolmount: Operation not permitted` → forkit cannot set up
   jails → `convert-to` connections died (`RemoteDisconnected`). Fixed with
   `privileged: true` (dev-only). `cap_add: [SYS_ADMIN]` +
   `seccomp=unconfined` + `apparmor=unconfined` was tried first and is NOT
   sufficient on this host (exec processes kept `CapEff=0`,
   `unshare(CLONE_NEWNS)` → EPERM). Image pinned by digest
   (`collabora/code@sha256:fe49c08c…`, CODE 25.04.9.4) so a floating `:latest`
   pull cannot reintroduce this.
2. **The investigation's direct-reproduction snippet was misleading.** It
   converted a fake 14-byte `%PDF-1.4 test` payload — which fails with a
   *different* error (`loadComponentFromURL returned an empty reference`) —
   and used the query-string syntax `convert-to?format=odt`, which this
   coolwsd mis-parses as format `format=odt` → garbage filename → instant
   `savefailed`. The app's multipart-field syntax (`data={"format": ...}`)
   is correct.
3. **App bug: `window.LR` undefined in the editor popup (the real reason the
   test timed out even with a healthy Collabora).** `docs_editor.html` is a
   standalone page (no `layout.html`) and its inline script calls
   `window.LR.t(...)` in the convert handler — but never loaded
   `/app/i18n/messages.js` which defines `window.LR`. First click threw
   `Cannot read properties of undefined (reading 't')` **before** the
   `fetch()`, so no convert POST ever reached the app and the navigation
   never happened. (This also affected rename/delete/share error paths.)

### Fixes

| Fix | Where |
| --- | --- |
| `privileged: true` + digest-pinned image (comments explain why and what was tried) | `docker-compose.dev.yml` |
| Load the i18n catalog on the standalone editor page | `app/modules/docs/templates/docs_editor.html` |
| Replace `alert()` conversion-failure UX with an inline `#convert-error` element next to the button (AGENTS.md UI rules) | `app/modules/docs/templates/docs_editor.html` |
| Regression guard: rendered editor must include `messages.js` + `#convert-error` | `tests/docs/test_docs.py::test_docs_editor_page_for_pdf_shows_convert` |
| Environment probe: `is_collabora_conversion_available()` performs one real txt→odt conversion per process; docs convert UI tests skip with an actionable reason when Collabora is up but cannot convert (the exact failure mode that used to burn 30s per test) | `tests/e2e/services.py`, `tests/e2e/ui/test_docs_ui.py` |

### Verification

- `curl -F data=@sample.pdf -F format=odg http://localhost:9980/cool/convert-to`
  → 200, valid OpenDocument Drawing (PDFs target **odg**, not odt — see
  `app/shared/pandoc_formats.py:target_odf_type`).
- `tests/e2e/ui/test_docs_ui.py` passes (was deterministic fail).
- `tests/docs/test_docs.py -k editor_page` passes.

### Post-mortem note

A misconfigured Collabora now skips the convert UI tests loudly instead of
failing after a timeout. If those tests skip on your machine, fix the
container (see compose comments) — do not ignore the skip.
