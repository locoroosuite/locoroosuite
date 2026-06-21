# Contributing to LocoRooSuite

## Dev setup

### Prerequisites

- Python 3.12+
- Node.js 22+
- Docker and Docker Compose

### Get running

```bash
git clone https://codeberg.org/locoroo/locoroosuite.git
cd locoroosuite

# Python environment
python -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt

# Start the full dev stack
cp .env.example .env
make dev-build && make dev-up
```

Open `http://localhost:5001` — the first-run setup page creates your admin account, domain, and test mailbox.

## Running tests

```bash
# Full suite (no Docker required for unit/integration tests)
./venv/bin/pytest tests/ --ignore=tests/e2e

# Single module
./venv/bin/pytest tests/mail/
./venv/bin/pytest tests/contacts/
./venv/bin/pytest tests/calendar/
./venv/bin/pytest tests/docs/
./venv/bin/pytest tests/api/

# E2E tests (requires dev stack running)
make dev-up
./venv/bin/pytest tests/e2e/

# MCP client integration tests
npm test --prefix packages/locoroosuite-mcp
```

## Codebase layout

```
app/
  modules/
    mail/              # IMAP email (Blueprint)
    contacts/          # CardDAV contacts (Blueprint)
    calendar/          # CalDAV calendar (Blueprint)
    docs/              # Collabora/WOPI documents (Blueprint)
  api/                 # REST API (/api/v1/)
  mcp/                 # Python MCP server (ASGI)
  workers/             # Background threads
  shared/              # Cross-module infrastructure (auth, DB, keys)
  admin/               # Admin/manager area
packages/
  locoroosuite-mcp/    # TypeScript MCP client (npm)
mail-api/              # Dovecot/Postfix management Flask service
tests/                 # Per-module test directories
```

### Key rules

- **`HLD.md` is the source of truth for functionality.** It defines what the software does. Code implements what the HLD describes. If you implement new functionality, update the HLD first or as part of your PR. If the HLD describes something that the code doesn't match, that's a bug — fix whichever side is wrong.
- **Modules never import from other modules.** Cross-module data goes through `app/shared/` only.
- **Each module is a Flask Blueprint** registered via `register(app)` in its `__init__.py`.
- **MCP parity**: REST API endpoints, Python MCP tools, and TypeScript MCP tools must stay in sync. Changes to one layer require updates to all three:
  1. `app/api/controllers/<module>.py`
  2. `app/mcp/tools/<module>.py`
  3. `packages/locoroosuite-mcp/src/tools/<module>.ts`

## How to contribute

### Pull requests

1. Fork the repo
2. Create a branch from `main`
3. Make your changes
4. Add tests (see testing expectations below)
5. Run the full test suite and `make check`, verify zero failures and a clean ratchet
6. Open a PR with a clear description of what changed and why

### Testing expectations

Every route (GET/POST/PUT/PATCH) needs tests for:
- Happy path
- Missing/invalid input
- Unauthorized access
- Duplicate/conflict cases

For API endpoint tests (`tests/api/`): exercise the full request-to-response cycle with a real SQLCipher cache database. See `tests/api/conftest.py` for the fixture pattern.

For bug fixes: describe which existing tests cover the broken path, whether they pass or fail, and what new tests you're adding.

### Code style

- Python: follow PEP 8, use type hints. Static checks are mandatory as the last step of any change:
  - `ruff` (lint + format) must be **clean** on touched files — run `./venv/bin/ruff check --fix <file>` then `./venv/bin/ruff format <file>`, then re-check for 0 issues.
  - `pyright` (types) must get **strictly cleaner** — the touched file's error+warning count after your change must be less than its state on `master` (or remain 0 if already 0). See `AGENTS.md → Static Checks (last step)` for the full ratchet and the escape hatch for pre-existing errors that would require a large refactor.
- No bare `except` blocks — every exception handler must re-raise, log with context, or return a structured error
- Use modern APIs: `db.session.get(Model, id)` not `Model.query.get`, `datetime.now(timezone.utc)` not `datetime.utcnow()`
- No comments unless the code genuinely needs explanation
- Files over 600 lines should be split

### Mock data

Mock data must match actual return shapes — column count, column order, field names. Read the actual function before writing mocks. Wrong mock data is worse than no test.

### Warnings

The test suite treats warnings as errors (`filterwarnings = ["error", ...]` in `pyproject.toml`). Your PR will not pass if it introduces new deprecation warnings.

## Reporting issues

- **Bugs**: open an issue on [Codeberg](https://codeberg.org/locoroo/locoroosuite/issues) with steps to reproduce, expected behavior, and actual behavior
- **Security vulnerabilities**: see [SECURITY.md](SECURITY.md) — do not file public issues for security problems
- **Feature requests**: open an issue on [Codeberg](https://codeberg.org/locoroo/locoroosuite/issues) describing the use case, not just the solution

## Commit style

Short, imperative, lowercase. Examples:

```
add pagination to contacts list
fix thread ordering when messages have no date header
update caldav sync to handle changed sync-tokens
```

(Note: the existing history uses Capitalized sentence-style messages like "Added email folder management" — match the surrounding commits if in doubt.)

## Releases

Versions follow `0.MINOR.PATCH`. A release is a version-bump commit plus an annotated git tag — both are required.

### Files to update (all four)

The version string is duplicated across the root project and the MCP package:

1. `package.json` — root `"version"`
2. `packages/locoroosuite-mcp/package.json` — `"version"`
3. `packages/locoroosuite-mcp/src/index.ts` — the `version:` field in the `McpServer` config
4. `packages/locoroosuite-mcp/dist/index.js` — regenerated by rebuilding (next step)

### Steps

```bash
# 1. Edit the three source files above to the new version (e.g. 0.5.1).

# 2. Rebuild the TS package so dist/index.js picks up the new version.
npm run build --prefix packages/locoroosuite-mcp

# 3. Verify tests still pass.
npm test --prefix packages/locoroosuite-mcp

# 4. Commit.
git add package.json packages/locoroosuite-mcp/package.json \
        packages/locoroosuite-mcp/src/index.ts \
        packages/locoroosuite-mcp/dist/index.js
git commit -m "Bump version to 0.5.1"

# 5. Create an ANNOTATED tag on the bump commit (not a lightweight tag).
#    Format: vX.Y.Z, message starts with "Version X.Y.Z" then release-note bullets.
git tag -a v0.5.1 -m "Version 0.5.1

- <bullet summarizing each notable change since the last tag>
- ..."

# 6. Push commits AND tags (tags are not pushed by default).
git push --follow-tags
```

### Tag conventions

- **Annotated** (`git tag -a`), never lightweight — annotated tags carry the release notes and are what `git describe` and release tooling expect.
- Name: `v` + the exact version string (`v0.5.1`).
- Message: first line `Version X.Y.Z`, blank line, then bullet points summarizing the user-facing changes since the previous tag. Match the style of `git show v0.5.0`.
- The tag goes on the **bump commit**, not on a feature commit.

### What goes in a release

Group completed work since the last tag into one release. A release may contain multiple feature/fix commits — the tag message summarizes them. Don't tag until the version-bump commit is in place.
