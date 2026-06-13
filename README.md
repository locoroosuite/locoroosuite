# LocoRooSuite

Self-hosted email, contacts, calendar, and documents — with first-class agent integration. AI assistants don't just read your data, they act on it: send emails, create calendar events, edit documents, manage contacts. Privacy-first: data stays on your server, admin accounts cannot read user content, and credentials are encrypted at rest with per-user keys.

A managed version is available at [locoroo.net/suite](https://locoroo.net/suite/) if you'd rather not run your own.

## What it does

| Module | Protocol | What you get |
|--------|----------|-------------|
| **Mail** | IMAP/SMTP | Send, receive, search. Threaded conversations, drafts, attachments, drag-and-drop folder management. Real-time updates via IMAP IDLE. |
| **Contacts** | CardDAV | Address book with vCard support. CRUD, search, autocomplete integration with compose. |
| **Calendar** | CalDAV | Events, recurring events, attendees, reminders, iMIP invitations. Week/day/month/agenda views. |
| **Documents** | WOPI (Collabora) | Create, edit, upload ODF documents. PDF export. File sharing with internal and external users. |
| **Agent integration** | MCP + REST API | Coding agents (Claude Code, Cursor, Codex) connect via `npx locoroosuite-mcp`. Web-based agents (ChatGPT, etc.) connect via the HTTP/SSE MCP server. Agents can send email, schedule events, edit documents, create contacts — full write access, not just read. ([Full tool reference](docs/AGENT_TOOLS.md)) |

All modules share the same account — one login, one set of domain credentials, one API token for agents.

## Quick start

You need Docker, Docker Compose, and Git.

```bash
# Clone from Codeberg (primary) or GitHub (mirror)
git clone https://codeberg.org/locoroo/locoroosuite.git
cd locoroosuite
cp .env.example .env

# Build and start (app, Dovecot, Postfix, Collabora, Radicale, mail API)
make dev-build && make dev-up
```

Open `http://localhost:5001`. The first-run setup page creates your admin account, domain, and test mailbox in one step. IMAP/SMTP/CardDAV/CalDAV settings are auto-configured in dev mode.

## API and AI integration

### REST API

All modules have REST endpoints under `/api/v1/`. Authenticate with a Bearer token (`lr_...`). Tokens are scoped per module (e.g. `mail:read`, `calendar:write`). Enable API access and create tokens in Settings → API Tokens.

### MCP server

LocoRooSuite ships with two MCP transports so agents can connect however they need:

- **`npx locoroosuite-mcp`** (stdio) — for coding agents running on your machine: Claude Code, Cursor, Windsurf, Cline, Zed, Codex, Gemini CLI. Add it to your client's MCP config with your API token and you're done.
- **Python HTTP/SSE server** (port 8001) — for cloud-hosted agents like ChatGPT that connect over HTTP.

Once connected, agents get the same capabilities you have in the web UI:

- **Mail**: send emails, reply to threads, move and delete messages, manage drafts, search
- **Calendar**: create events with attendees, send iMIP invitations, update recurring series, check free/busy
- **Documents**: create documents, write content via markdown, propose changes through a draft/review workflow (agent creates a draft → you review and accept in the web UI)
- **Contacts**: create, update, delete, search

The draft/review workflow for documents is worth calling out: an agent can propose edits to a document by creating a draft with its changes. The draft appears in your docs list alongside the original. You review it, then accept (replaces the original) or discard. The agent never overwrites your work without you seeing it first.

See [`docs/AGENT_TOOLS.md`](docs/AGENT_TOOLS.md) for the full tool reference (every tool, every parameter, every scope). See [`packages/locoroosuite-mcp/README.md`](packages/locoroosuite-mcp/README.md) for client-specific setup guides.

## Running tests

```bash
# Unit/integration tests (no Docker needed)
./venv/bin/pytest tests/ --ignore=tests/e2e

# Per-module during development
./venv/bin/pytest tests/mail/
./venv/bin/pytest tests/contacts/

# E2E tests (requires dev stack running)
./venv/bin/pytest tests/e2e/

# MCP client integration tests
npm test --prefix packages/locoroosuite-mcp
```

## Production deployment

Use `docker-compose.prod.yml`. You'll need:

1. A `.env` with real `SECRET_KEY`, `WOPI_JWT_SECRET`, `SERVER_NAME`, and SMTP settings
2. A reverse proxy (nginx) with TLS termination
3. DNS records pointing to your server

```bash
make prod-build
make prod-up
```

The production Compose file does not include Dovecot/Postfix — it expects those to be managed separately on the host. The `mail-api` container connects to the host's Dovecot and Postfix via bind-mounted config files.

See `docker-compose.prod.yml` for the volume mounts and environment variables required.

## Privacy model

- **Per-user encryption**: each user's cache (mail, contacts, calendar, documents) is stored in a separate SQLCipher database encrypted with a key derived from their credential. The server stores the key in memory only during the user's active session.
- **Admin cannot read user data**: admin accounts manage domains and users but have zero access to email content, contacts, calendar events, or documents. This is enforced at the application layer — the admin UI never exposes user content.
- **API tokens use a separate DEK**: when API access is enabled, a random Data Encryption Key replaces the credential-derived key. The DEK is wrapped per-token, so revoking a token removes that decryption path without affecting other tokens or the web session.
- **No third-party tracking**: the app doesn't phone home, embed analytics, or load external resources on user-facing pages. External images in emails are blocked by default.

## Architecture

```
Browser
  │
  ├─ /app/*        → Flask (gunicorn, port 5001)
  ├─ /api/v1/*     → REST API (same Flask process)
  ├─ /mcp          → MCP server (uvicorn, port 8001)
  └─ /collabora/   → Collabora Online (port 9980)

Flask app
  ├── app/modules/mail/       (IMAP cache → per-user SQLCipher)
  ├── app/modules/contacts/   (CardDAV cache → per-user SQLCipher)
  ├── app/modules/calendar/   (CalDAV cache → per-user SQLCipher)
  ├── app/modules/docs/       (Collabora WOPI host)
  ├── app/api/                (REST API controllers)
  ├── app/mcp/                (Python MCP server, ASGI)
  ├── app/admin/              (Admin/manager back office)
  └── app/shared/             (Auth, DB, keys, timezone)
```

Each module is a Flask Blueprint. Modules never import from each other — they share only `app/shared/`. Per-user data is stored in individual SQLCipher databases, encrypted with keys derived from the user's credential (or a random DEK when API access is enabled).

The MCP server and REST API expose full write access to all modules. Coding agents connect via the `locoroosuite-mcp` npm package (stdio transport) and web-based agents connect via the Python MCP server (HTTP/SSE). An agent with the right scopes can send emails on your behalf, create and edit calendar events, write to documents (including a draft/review workflow for AI-modified content), and manage your contacts — the same actions you'd take in the web UI, available as tool calls. See [`docs/AGENT_TOOLS.md`](docs/AGENT_TOOLS.md) for the full reference.

### External services

The app connects to these services (all included in the dev Docker setup):

- **Dovecot** — IMAP mailbox storage
- **Postfix** — SMTP relay
- **Radicale** — CardDAV + CalDAV server
- **Collabora Online** — Document editing via WOPI

### Tech stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, Flask, SQLAlchemy |
| Encryption | SQLCipher (data at rest), Fernet (credentials), cryptography (DEK wrapping) |
| Frontend | Tailwind CSS, vanilla JS |
| Mail server | Dovecot + Postfix + OpenDKIM |
| Contacts/Calendar | Radicale (CardDAV/CalDAV) |
| Documents | Collabora Online (WOPI protocol) |
| API | REST (JSON, `/api/v1/`), MCP (stdio + HTTP/SSE) |
| AI integration | MCP server (Python ASGI + npm stdio package) |
| Deployment | Docker Compose, gunicorn + uvicorn behind nginx |

## Project structure

```
app/
  modules/           # Mail, contacts, calendar, docs (Flask Blueprints)
  api/               # REST API controllers (/api/v1/)
  mcp/               # Python MCP server (ASGI)
  workers/           # Background threads (IMAP IDLE, sync)
  shared/            # Auth, DB, keys, timezone helpers
  admin/             # Admin/manager back office
packages/
  locoroosuite-mcp/  # TypeScript MCP client (npm, stdio transport)
mail-api/            # Dovecot/Postfix management service
dev-infra/           # Docker dev infrastructure configs
tests/               # Per-module test suites
```

## License

[AGPL-3.0](LICENSE) — you can run, modify, and self-host freely. If you modify the software and make it available over a network, you must make the modified source available to your users.
