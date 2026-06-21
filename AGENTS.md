## Change Request Flow (HLD Alignment)
For every user-requested change:
- Read `HLD.md` and explicitly state whether the request is a bug fix or a feature gap relative to HLD.
- Ask any clarifying questions needed before proceeding.
- Update `HLD.md` if required to align requirements.
- Wait for explicit final confirmation before making any code changes.
- If it is a bug, investigate why the existing tests did not prevent the issue.

## Purpose
Build the webmail application described in `HLD.md` using Python + Flask, MVC, and a modern Tailwind UI. Keep privacy as a first-class requirement.

## Architecture
The application uses a module-as-blueprint pattern. Each module (mail, contacts, calendar, docs) is a self-contained Flask Blueprint under `app/modules/` with its own controllers, models, services, templates, and static files.

- **Modules never import from other modules.** An agent working on one module only needs to read that module's directory + `app/shared/`.
- Shared infrastructure (auth, database factory) lives in `app/shared/`.
- Admin/manager area stays at `app/admin/` (top-level, cross-cutting).
- Each module's `__init__.py` exposes a `register(app)` function as the single integration point.

### Directory Structure

```
app/
  modules/
    mail/              # IMAP-based email
    contacts/          # CardDAV-based contacts
    calendar/          # CalDAV-based calendar
    docs/              # Collabora/WOPI-based documents
  api/                 # REST API (/api/v1/) — mirrors module structure
    token_service.py   # API key generation, DEK wrapping, token auth
    controllers/       # mail, contacts, calendar, docs, accounts, tokens
    controllers/helpers.py  # shared request/response utilities
  mcp/                 # MCP server (FastMCP + Starlette ASGI)
    auth.py            # Bearer token / OAuth JWT auth
    api_client.py      # HTTP client for calling Flask API from MCP tools
    tools/             # one file per module
  workers/             # Background threads (IMAP IDLE, imports, periodic sync)
  shared/              # Auth, DB, timezone helpers, cache_errors
  admin/               # Admin/manager (cross-cutting)
  __init__.py          # App factory

packages/
  locoroosuite-mcp/    # TypeScript MCP client package (npx, stdio transport)
    src/
      index.ts         # CLI entry point
      client.ts        # ApiClient for REST API requests
      tools/           # mirrors app/api/controllers/
    dist/              # Compiled JS output

mail-api/              # Separate Flask service — Dovecot/Postfix management
  managers/            # dovecot.py (passwd-file), postfix.py (virtual maps)
dev-infra/             # Docker dev infrastructure (Dovecot, Postfix, OpenDKIM)
```

### Integration Points

- **Dev environment**: `docker-compose.dev.yml` starts all services. `make dev-up` / `dev-down` / `dev-build`.
- **Mail server**: Admin domain/user CRUD calls `mail-api` when `MAIL_API_URL` is configured. Integration points: `_mail_api_call` and `_sync_domain_to_mail_api` in `app/admin/controllers/admin.py`.
- **REST API auth**: Bearer token (API key or OAuth JWT). `authenticate_request()` in each controller validates the token.
- **MCP parity**: `app/api/controllers/<mod>.py` ↔ `app/mcp/tools/<mod>.py` ↔ `packages/locoroosuite-mcp/src/tools/<mod>.ts` must stay in sync.
- **TS MCP client**: opencode/Claude Desktop/Cursor connect via `npx locoroosuite-mcp` (stdio). Debug connection issues there, not in the Python MCP server.
- **Workers** (`app/workers/manager.py`): Import across module boundaries (they are infrastructure). Started by app factory, run for process lifetime.

## Testing

Run `./venv/bin/pytest` with Flask test client and in-memory SQLite. Tests are organized per module: `tests/mail/`, `tests/contacts/`, `tests/calendar/`, `tests/docs/`, `tests/admin/`, `tests/shared/`, `tests/api/`.

### General Rules

- For every GET/POST/PUT/PATCH route, test: **happy path** + **common validation errors** (missing fields, invalid input, unauthorized, duplicates).
- **Bug fixes always require test changes.** Before writing any fix, state: (1) which existing tests cover the broken path and pass/fail status, (2) whether existing tests need updating, (3) any new tests needed. Wait for confirmation before proceeding.
- Run the full suite after implementation: `./venv/bin/pytest tests/ --ignore=tests/e2e` and `npm test --prefix packages/locoroosuite-mcp`.
- During development, single-module tests are fine: `./venv/bin/pytest tests/mail/`

### Schema Migrations

All database layers use the unified migration runner (`app/shared/migrations.py`). See `HLD.md → Schema Migration Architecture` for the full design.

- **Adding a migration**: append `Migration("NNNN_name", fn)` to the relevant registry (`app/shared/app_migrations.py` for the main DB, `app/modules/<mod>/services/cache_migrations.py` for caches). The function must self-guard.
- **Never call `conn.commit()` inside a migration** — the runner commits after recording.
- **Bug fixes that require schema changes**: always add a migration + a test that builds the pre-migration schema and verifies the fix.

### Mock Data Contract

Mock data **must match the actual return shape** — column count, column order, field names. Wrong mock data is worse than no test.

- Before mocking, read the actual function to verify the return shape.
- Document expected column order in a comment.
- Use actual key names (e.g., `tel_work` from `parse_vcard()`, not `phone_work`).
- For Row/dict returns, use `.keys()` or read the SQL to verify field names.

### API Endpoint Tests (`tests/api/`)

Exercise the **full request-to-response cycle** with a real SQLCipher cache DB.

- Create a temp file, set `account.cache_db_path`, use `open_cache()` + `init_cache_schema` + `upsert_*` helpers to seed data. **Never mock `open_cache` or `cache_db`** — it hides type mismatches.
- **Required coverage per endpoint**: happy path (verify response shape), empty state, 404 cases, validation errors, response schema validation (all fields present, correct types).
- Fixture pattern: `api_customer` from `tests/api/conftest.py` + temp cache DB + Flask test client.

### E2E Tests (`tests/e2e/`)

Run against live Docker env (`make dev-up`). Developer-only, not in CI. Auto-skip if services unreachable.

- **Run**: `./venv/bin/pytest tests/e2e/`
- **Install Playwright**: `./venv/bin/pip install pytest-playwright && ./venv/bin/playwright install chromium`
- **Core principle**: Verify effects at the **service level**, not just HTTP status codes.
- **Service-level** (`tests/e2e/test_*.py`): Use IMAP/CardDAV/CalDAV clients to verify actual state (e.g., send email → IMAP-verify arrival).
- **UI** (`tests/e2e/ui/`): Playwright for rendering, forms, empty states, dynamic behavior.
- **Helpers** (`tests/e2e/services.py`): `imap_search`, `mailapi_get_users`, `carddav_get_addressbooks`, `caldav_get_calendars`, `wait_for`.
- **Config** (env vars): `E2E_APP_URL` (default `http://localhost:8001`), `E2E_MAIL_API_URL` (`http://localhost:8800`), `E2E_MAIL_API_KEY` (`dev-mail-api-secret`), `E2E_IMAP_HOST` (`localhost`), `E2E_IMAP_PORT` (`993`), `E2E_CARDDAV_URL` / `E2E_CALDAV_URL` (`http://localhost:5232`). Test users: `test@test.localhost`/`TestPass123!`, `test2@test.localhost`/`TestPass123!`, `admin@dev.test`/`TestPass123!`.

### MCP Client Integration Tests (`packages/locoroosuite-mcp/tests/integration/`)

Vitest tests exercising the TS MCP client against the real Flask REST API.

- **Run**: `npm test --prefix packages/locoroosuite-mcp`
- **When to run**: After ANY change to `app/api/controllers/`, `app/mcp/tools/`, or `packages/locoroosuite-mcp/src/`.
- Each test imports `ApiClient` from `src/client.js`, makes real HTTP requests, asserts response shape. Error cases use `try/catch` (`ApiError` on non-2xx).
- When adding/modifying an API endpoint, add a matching integration test.

### Zero-Warning Policy

The test suite treats new warnings as failures (`filterwarnings = ["error", ...]` in `pyproject.toml`). **Do not add new ignore rules without justification.**

- Use modern APIs: `db.session.get(Model, id)` (not `Model.query.get`), `datetime.now(timezone.utc)` (not `datetime.utcnow()`).
- Mock all external connections (IMAP, SMTP, CardDAV, CalDAV) to avoid thread exceptions.

## Coding Standards

### UI
- Actionable buttons: inline spinner, disable while in-flight, re-enable on success/failure.
- On failure: inform user with next steps (retry, check connection, refresh).
- Avoid navigating away for inline actions; update UI in place.
- TailwindCSS — professional, Big Tech quality.
- Module switcher in header: Mail, Contacts, Calendar.

### Error Handling

These rules apply to **all layers** (MCP tools, REST API controllers, Flask handlers).

- **Never silently swallow exceptions.** Every `except` block must: re-raise, log with `_logger.warning()`/`_logger.exception()` + context, or return a structured error. Only exception: `finally`-block cleanup with `_logger.debug()`.
- **Structured errors**: MCP uses `err("CODE", "message")` from `app/mcp/helpers.py`. REST API uses `api_error("CODE", "message", status)` from `app/api/controllers/helpers.py` — always `{"error": {"code": "...", "message": "..."}}`. Flask error handlers (404, 405) must return structured JSON.
- **Never use bare `assert`** — use `raise McpAuthError(...)` or `return err(...)`.
- **Map new exception types** to user-facing errors in `_KNOWN_ERROR_MAP` (`app/mcp/errors.py`) with error code, human message, and optional remediation steps.
- **Log with context**: always include identifiers (`message_id`, `account_id`, `uid`, `request_id`). Use `exc_info=True` for caught exceptions.
- **`CacheKeyMismatchError`** lives in `app/shared/cache_errors.py`. All `open_cache()` calls must catch decryption failures and raise it.
- **Never return bare "INTERNAL SERVER ERROR"** — always include `request_id` or actionable guidance.

### MCP Tool Parity

Every REST API endpoint in `app/api/controllers/` **must** have a corresponding MCP tool in `app/mcp/tools/`. When adding/modifying an endpoint, update all three layers:
1. `app/api/controllers/<module>.py` (REST API)
2. `app/mcp/tools/<module>.py` (Python MCP tools)
3. `packages/locoroosuite-mcp/src/tools/<module>.ts` (TypeScript MCP client)

### Timezone Awareness

- Every user-facing datetime **must** be converted to their configured timezone (`CustomerSettings.timezone`).
- Use `app/shared/timezone.py`: `resolve_user_timezone()` → IANA string, `resolve_tzinfo()` → `tzinfo` object. The `"browser"` value resolves from the `browser_tz` cookie.
- Calendar events: store with original TZID, display converted via `_format_event_time()`. JS must send browser IANA timezone.
- Mail: pass `timezone_name=settings.timezone` to formatting functions.
- Server-side timestamps (`created_at`, `updated_at`) are always UTC — **do not convert**.

### Static Checks (last step)

This is a **ratchet**, not a suggestion. The codebase is agent-maintained, and the per-file pyright baseline is currently non-zero (~1956 errors repo-wide). To reverse that rot, every touched file must end **cleaner** than it started — never worse, never flat unless already clean. "Pre-existing" is not an excuse: the base count is measured (`git show master:<file>`), not vibes.

Before declaring any change done, for **every file you created or modified**:

1. **Lint + format — must be clean (not just cleaner).**
   - Python: `./venv/bin/ruff check --fix <file>` then `./venv/bin/ruff format <file>`, then re-run `./venv/bin/ruff check <file>` — it must report **0 issues**.
   - TypeScript: `npm run build --prefix packages/locoroosuite-mcp` must succeed.
2. **Typecheck — must get strictly cleaner (pyright errors + warnings).**
   Compare the file's count **before** your change (its state on `master`) vs **after**:
   - `before > 0` → `after` must be **strictly less** than `before`.
   - `before == 0` → `after` must remain **0**.
   - New file → `after` must be **0**.
   - Mechanic: `make typecheck-ratchet` prints per-file `before -> after` and fails on regression/flat. You can also measure by hand: save the working file, `git show master:<file> > <file>`, run `./venv/bin/pyright <file>` (= `before`), restore the working file, run again (= `after`).
3. **Escape hatch — do NOT silently balloon scope.** If reducing the count requires editing other modules, changing a public signature, a schema migration, or a large/unrelated refactor: **stop and flag it** to the user with the exact error text and `file:line`. Never silently leave it; never silently expand the diff. Wait for the user's decision.
4. **Report before → after** per touched file in your summary. Reductions are wins — call them out explicitly.

Canonical commands: `make lint`, `make format`, `make typecheck`, `make typecheck-mcp`, `make typecheck-ratchet`, `make check` (lint + typecheck on touched files). All scope to files modified vs `master` (override with `BASE_REF=origin/master make ...`).

Why ruff + pyright together: `ruff` is the linter/formatter (style, unused imports, dead code, common bugs — a fast superset of flake8/isort/black-as-formatter); `pyright` is the type checker (None-handling, argument types, signature mismatches). They are complementary, not redundant. `black` and `mypy` are deprecated as canonical tools here — use `ruff format` and `pyright` instead.

### Miscellaneous

- Use simple language to communicate changes.
- Stay within module directory boundaries. Read only the relevant module + shared layer.
- Files > 600 lines are too large. Prefer multiple small files.
- **Fail early on misconfiguration** — return a clear error immediately, never silently work around missing config.
- **Fix problems as you go** — LSP errors, unused imports, dead code, bad error handling. Fix immediately, don't leave messes (see **Static Checks (last step)** above for the mandatory final gate).
- ~~Ensure `pyright` reports 0 errors on any file you modify.~~ → replaced by **Static Checks (last step)** above: the baseline is non-zero today, so the rule is *strict improvement on each touch*, not instant zero.
- **Never deploy to production.** Ask the user to deploy.
- **Production infra is managed separately.** Provide prompts for ops agents, don't SSH into production.
- **Breaking changes require confirmation.** Default to clean break unless user requests backward compatibility.
- **Releases need a tag, not just a bump commit.** When asked to cut a version, follow `CONTRIBUTING.md → Releases`: update all four version files, rebuild `dist`, commit "Bump version to X.Y.Z", **and** create an annotated `vX.Y.Z` tag with release-note bullets on that commit. A bump commit without the tag is incomplete.
