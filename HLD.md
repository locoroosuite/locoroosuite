# Objective
To build a webmail that has a clean UI interface and it is focused in usability and cleanless. 

# Roles
Admin - user that can manage all the domains and all users. There will be a back office with overall statistics and administrative actions. Data privacy is key, so the emails must be hidden from the admins. 
Manager - user that can manage specific domains. 
 Customer - user that can send, receive email, and manage one or more email addresses. 

# Versions
MVPv0.1 - Basic user interface that relays in IMAPS to send and receive emails.
Deployment target for MVP: single server VM.

# Architecture — Module-as-Blueprint

The application is structured as independent modules, each implemented as a Flask Blueprint with its own full MVC stack. Modules share only authentication, database connection, and the base layout template.

## Directory Structure

```
app/
  modules/
    mail/              # Email (IMAP-based)
      __init__.py      # register(app) function
      controllers/
      models/
      services/
      templates/
      static/
    contacts/          # Contacts (CardDAV-based) — not yet implemented
    calendar/          # Calendar (CalDAV via Radicale) — see U12
  api/                 # REST API (see U15)
    __init__.py        # register(app) — registers /api/v1/ blueprint
    controllers/       # API controllers calling shared service layer
    auth.py            # Token authentication & scope enforcement
  mcp/                 # Python MCP Server (see U17)
    __init__.py        # create_app() — ASGI app factory for uvicorn
    server.py          # MCP server setup, tool registration, HTTP transport
    auth.py            # OAuth 2.1 JWT verification + API key fallback
    tools/             # Tool definitions calling shared service layer
      mail.py
      contacts.py
      calendar.py
      docs.py
  shared/              # Shared infrastructure only
    auth.py            # Login/session decorators, user helpers
    db.py              # Database engine/session factory
    keys.py            # In-memory key management (_user_keys)
    oauth.py           # OAuth 2.1 authorization server (see U18)
  admin/               # Cross-cutting admin/manager area (top-level)
    controllers/
    models/
    services/
    templates/
  __init__.py          # App factory: calls each module's register()
```

## Integration Contract

- Each module's `__init__.py` exposes a single `register(app)` function that creates a Blueprint, imports controllers, and registers the blueprint with its URL prefix.
- No module may import from another module. All cross-module data access goes through `app.shared` only.
- Each module owns its database tables. No foreign keys across module boundaries.

## Schema Migration Architecture

The application uses a unified, dependency-free, versioned migration runner (`app/shared/migrations.py`) for all five database layers: the main app database (SQLAlchemy / sqlite3) and the four per-account encrypted cache databases (mail, contacts, calendar, docs — each SQLCipher).

### Two-Layer Robustness

1. **Applied-migrations tracking table** (`_schema_migrations`) — Records every migration that has run, providing a fast skip-path and an inspectable audit trail.
2. **Self-guarding migration functions** — Every migration inspects the actual schema and no-ops if the change is already present. Pre-versioning databases bootstrap correctly: the runner runs the full chain once, each step either no-ops or performs a real fix depending on the actual state. No version-stamping heuristics required.

This combination survives fresh databases, old databases, partially-migrated databases, and corrupt-schema databases (e.g., a stale `account_id NOT NULL` column on the mail `folders` table that broke every INBOX/Sent sync).

### File Layout

- `app/shared/migrations.py` — The runner: `Migration`, `run_migrations()`, introspection helpers (`table_columns`, `has_table`, `has_index`).
- `app/shared/app_migrations.py` — Registry for the main app DB (12 migrations).
- `app/modules/<module>/services/cache_migrations.py` — Registry per cache module (mail, contacts, calendar, docs).

### Adding a New Migration

1. Append a `Migration("NNNN_descriptive_name", fn)` to the relevant registry tuple.
2. The function must self-guard: check the current schema and return early if already applied.
3. Do **not** call `conn.commit()` inside the migration — the runner commits after recording.
4. Add a test that builds the pre-migration schema, runs migrations, and asserts the post-migration shape + data preservation.

### Operational Notes

- Migrations run automatically: the app factory calls `run_migrations()` for the main DB on startup; each cache `open_cache()` calls it on first open (memoized per-process).
- Migrations are forward-only (no downgrades). The `applied_at` timestamp in `_schema_migrations` provides an audit trail.
- For table-rebuild migrations (drop/rename columns), the SQLite dance is used: create new table → copy data → drop old → rename → recreate indexes. The mail `0005_drop_folders_account_id` migration is the reference example.
- Production deployment: migrations run automatically on app startup. No manual `flask db upgrade` step is needed.

## URL Layout

- `/app/login`, `/app/logout` — shared authentication (not inside any module)
- `/app/mail/*` — mail module
- `/app/contacts/*` — contacts module (future)
- `/app/calendar/*` — calendar module (see U12)
- `/admin/*` — admin/manager area (top-level)
- `/api/v1/*` — REST API for API token access (see U15)

# Navigation & Entry Points
N1 - Root "/" redirects to the customer login page at "/app/login".
N2 - Customer login remains at "/app/login".
N3 - Admin/manager login is at "/admin/login"; all admin/manager UI lives under "/admin" (manager screens under "/admin/manager/...").
N4 - Header includes a top-right user area with: (1) a Gmail-style app launcher icon (waffle/grid) to the left of the user avatar that opens a dropdown panel showing available modules — Mail, Contacts, Calendar (future) for customers; Admin for admin/manager users; (2) the user avatar (initials) to the right of the launcher. The avatar dropdown menu items remain: Settings, Logout, and Domain Management when the logged-in email matches a local admin/manager account.
N6 - Header logo ("LR" + "LocoRoomail") links to the role landing page: customer -> "/app/mail/", admin -> "/admin/", manager -> "/admin/manager/"; logged-out -> "/app/login".
N5 - The same email address may be used for customer IMAP login even if it also has admin/manager credentials.

# Use Case U1 – Administration (General)

U1.1 - The admins are created using the cli tool and authenticate via local accounts in the app. 
U1.1a - No password policy required for MVP. Optional per-user 2FA (TOTP) is supported per U22.
U1.1b - Admin password resets are CLI-only.
U1.2 - Admins can create managers and customers; managers can create customers. Managers authenticate via local accounts in the app. 
U1.3 - The admins configure domains that will manage emails. This includes IMAPS/SMTP host, port, TLS/SSL requirements (STARTTLS or SMTPS), and allowed authentication methods. SMTP is domain-configured (no per-user override). CalDAV/CardDAV server URL and authentication are also configured per-domain (typically Radicale serving both protocols on the same base URL). 
U1.3a - Domain creation supports DNS-based discovery (SRV/MX and common hostnames) to prefill IMAP/SMTP settings. New domains start in "draft" status, then move to "complete" if critical values are found or "review" if manual input is required.
U1.4 - The admins can assign managers to domains.
U1.4a - Self-provisioned customers are automatically assigned to the domain's manager(s).
U1.5 - Admin back office (minimal): list domains, managers, and customers; show basic counts per domain.
U1.6 - Admin actions (standard): create/edit/disable domains; update domain IMAP/SMTP settings; assign/unassign managers; create/deactivate/reactivate customers (app-level only, IMAP untouched); delete customer local data (cache/tags) without touching IMAP.
U1.7 - Admins can create mailbox import requests for customers. For MVP, the admin UI exposes source type selection for Google (Gmail / Google Workspace) and Google Takeout; destination is always IMAP and is configured by the admin on the import request.
U1.7a - Each import request produces a unique signed link with expiry and an enable/disable flag. The link alone is sufficient for the end user to start the import flow; the user does not need an existing LocoRoomail session.
U1.7b - Admin-visible import data is privacy-safe only: source type, destination mailbox identity, status, timestamps, folder/message counts, and sanitized errors. Admins must never see message subjects, bodies, attachments, OAuth tokens, or destination passwords.
U1.7c - Admins can revoke an import link before expiry by disabling the import request. Disabled or expired links must not permit OAuth or import execution.

# Use Case U1b – Platform DNS & Self-Hosted Domains

U1b.1 - Admins configure platform-level DNS settings via the "Platform DNS" admin page: MX server hostnames with priorities (supporting multiple backup servers) and a DKIM selector shared across all self-hosted domains.
U1b.2 - Each MX server entry is validated before saving: the hostname must resolve to an A/AAAA record and accept connections on port 25.
U1b.3 - Domains can be marked as "self-hosted" in the domain review page. Self-hosted domains use the platform MX servers for email delivery and the platform DKIM selector for email signing. Enabling self-hosted requires a configured Mail API connection for the domain.
U1b.4 - When self-hosted is enabled for a domain, the admin configures the DMARC policy (`none`, `quarantine`, or `reject`) and the report recipient email address (`rua`). Default policy is `none`.
U1b.5 - The domain review page shows a "DNS Health" section for self-hosted domains with four cards: MX, SPF, DKIM, and DMARC. Each card displays the expected DNS record value (with copy support) and the current verification status.
U1b.6 - DNS health checks query the domain's authoritative nameservers directly (not local DNS). Each record type has three possible states:
  - **Not configured** (red) — no authoritative NS has the correct record or the value doesn't match.
  - **Propagating** (amber) — at least one but not all authoritative NS servers have the correct record.
  - **Verified** (green) — all authoritative NS servers have the correct record.
U1b.7 - DNS checks are performed asynchronously via JavaScript to avoid blocking page load. The admin can test all records at once or individually.
U1b.8 - DKIM key pairs are generated per domain via the mail-api (`POST /api/dkim/<domain>`). The mail-api manages OpenDKIM KeyTable and SigningTable entries. The public key is retrieved for DNS record instructions.
U1b.9 - The domain list page shows a "Self-hosted" badge for domains with self-hosted enabled.

# Use Case U2 – Managers (General)
On the MVPv0.1 the managers see the Customer that logged in in their domain.  
U2.1 - Managers can create customers in their domains.
U2.2 - Managers can deactivate/reactivate customers (app-level only, IMAP untouched).
U2.3 - Managers can reset a customer's local data (cache/tags) without touching IMAP.
U2.4 - Manager password resets are performed by admins.

# Use Case U3 – Customer Accounts & Authentication
U3.1 - Customers authenticate via IMAP username/password. The customer password is the IMAP password and IMAP credentials are stored for session reuse (encrypted at rest using a per-user key — credential-derived by default, or a random DEK when API access is enabled per U14). 
U3.2 - If a domain is enabled, first customer login auto-creates the customer account (self-provisioning). 
U3.3 - Customers can add multiple email accounts.
U3.4 - Customers can remove an email account and purge its local cache.
U3.5 - No per-customer account limit in MVP.

# Use Case U4 – Mailbox Navigation & Viewing
U4.1 - Will see the list of folders and the emails in the current folder.
U4.2 - Multiple accounts are kept separate with an account switcher (no unified inbox in MVP); account switcher is at the top of the left sidebar.
U4.3 - Use IMAP IDLE for real-time updates.
U4.4 - If IDLE drops or is unsupported, fall back to polling every 60 seconds; interval is configurable per user.
U4.5 - Use WebSocket or SSE to push real-time UI updates.
U4.6 - New mail updates UI counts/badges only (no toast notifications in MVP).
U4.7 - Provide threaded/conversation view (client-side threading). Uses References/In-Reply-To headers for accurate thread grouping with normalized-subject fallback. Sent messages from the Sent folder are merged into threads and visually distinguished (indigo tint, "You" label, send-arrow icon).
U4.7d - Bounce / Delivery Status Notification (DSN) messages (RFC 3464 multipart/report) are detected during IMAP sync. The embedded original-message headers (Message-ID, In-Reply-To, References, Subject) are extracted and used for thread_id computation, so bounces appear within the original conversation thread. Bounces are shown in both the INBOX thread view and the Sent folder thread view. The UI renders bounce messages with a distinctive red warning style, a "Delivery failed" badge, and the failure reason (e.g., "550 5.7.606 Access denied") displayed inline.
U4.7c - In the folder/message list view, when a thread has more than 4 messages, the 2 newest messages are displayed normally and the remaining older messages are collapsed behind a clickable bar (e.g. "▸ 3 older messages"). Clicking the bar toggles the collapsed messages inline with a smooth slide animation. The thread count badge remains visible on the first message regardless of collapse state.
U4.7a - Message detail page shows a Gmail-style stacked conversation view: all messages sharing the same thread_id (plus normalized-subject fallback) are displayed vertically, sorted oldest-first. The current message is expanded; other messages show a collapsed header (avatar, sender, date, snippet). Clicking a collapsed header expands it inline; clicking again collapses it. Each expanded message renders its cached body in its own iframe. The current message uses the full IMAP-fetched body; other messages use the cached plain-text body.
U4.7b - Quoted text in email bodies is collapsed by default with a toggle to reveal it. HTML <blockquote> elements and consecutive plain-text lines starting with > are wrapped in a <details>/<summary> toggle ("Show trimmed content" / "Hide trimmed content"). This applies to both the message detail view and the thread conversation view.
U4.8 - Show unread counts per folder.
U4.9 - Use a single-pane message list layout with a left collapsible folder sidebar.
U4.10 - Provide a toggleable preview pane.
U4.10a - Message row click behavior:
  - Left click opens the message in the preview pane when preview is enabled.
  - Left click opens the full message page when preview is disabled.
  - Ctrl/Cmd+Click and middle-click open the full message page in a new browser tab/window.
U4.11 - No keyboard shortcuts in MVP.
U4.12 - Message list shows a short body preview snippet.
U4.16 - Folder view is paginated at the conversation level: 50 conversations per page. The folder header displays the total number of conversations and emails (e.g. "11 conversations (228 emails)"). Pagination controls (Previous/Next) appear at the bottom when the folder has more than 50 conversations. Messages without a valid date are sorted first in the list so they are never hidden. The listing query excludes the body column for performance; body is fetched on demand when viewing a single message.
U4.13 - Support folder favorites/pinning.
U4.14 - Smart folders (local-only views): Unread and Starred only. Show these links under the INBOX section.
U4.14a - Folder badges (including Smart folders) always represent unread message counts; Smart folder badges must never show total items, only unread totals.
U4.15 - Folder list ordering: INBOX (case-insensitive) always first; then Favorites (pinned, in pin order); then System folders in fixed order (Sent, Drafts, Archive, Trash, Junk); then remaining folders sorted by most recent cached message Date header (descending); folders with no cached messages last. Sidebar groups are shown with visible section headers (INBOX, Favorites, System, Folders).
U4.15a - Folders section header: label “FOLDERS” (uppercase). A small “+” add-folder icon is pinned to the right edge of the header; it appears when hovering any row within the Folders section (but remains positioned at the header). On touch/mobile, the “+” is always visible. Clicking the “+” reveals the inline “New Folder” input + “Add” button directly below the FOLDERS header (not at the bottom of the section).

# Use Case U4A – Email Update Rules (Business)
U4A.1 - Never perform a full-account sync after the first login; rely on per-folder incremental sync state.
U4A.2 - Initial login syncs Inbox only (recent + unread) to show mail quickly without full sync.
U4A.3 - Folder open triggers a one-time incremental sync for that folder (recent + unread), then returns to background cadence.
U4A.3a - On folder open, always include the most recent page size (100) regardless of age so the first page can be populated even if the newest mail is older than 30 days.
U4A.4 - Per-folder sync state is stored in the per-user cache (UIDVALIDITY, UIDNEXT, optional HIGHESTMODSEQ, last_new_at).
U4A.5 - If UIDVALIDITY changes for a folder, purge that folder's cache and re-sync it.
U4A.6 - Prefer QRESYNC/CONDSTORE when available to detect flag changes, new mail, and expunges; otherwise use UID tracking and periodic UID diffs for active folders.
U4A.7 - Run IDLE for Inbox and the currently selected folder; if IDLE is unsupported, poll those folders only.
U4A.7a - If IDLE is unsupported, use a short fast-poll window (e.g., 5s interval for ~30s) after a folder is opened to surface new messages quickly, then fall back to the user-configured polling interval.
U4A.8 - While a user is connected, run light background checks every 5 minutes for folders with last_new_at within 30 days to refresh unread counts and detect changes.
U4A.9 - Do not run daily sweeps for inactive folders; older mail is fetched only when a folder is opened or via IMAP SEARCH per U7.1.
U4A.10 - On customer login or account switch, trigger a non-blocking initial sync for the selected account's Inbox (recent + unread only).
U4A.11 - Keep syncing the selected account's Inbox in the background (IDLE when available; polling fallback otherwise).
U4A.12 - When the user clicks a folder, prioritize a one-time sync for that folder's recent + unread; then resume the normal background sync cadence.
U4A.13 - Show sync status in the sidebar with a subtle per-folder indicator on the far-right of each folder row (replacing any "Sync Status" text): 
  - Syncing with unknown progress: spinner.
  - Syncing with known progress: circular progress ring.
  - Idle/complete: no indicator.
  - Error: error icon; hover tooltip shows error details.
U4A.13a - Tooltip on hover must describe current status; when progress is known include "<done> of <total>" counts. If counts are not currently available, add the fields needed to provide them.
U4A.13b - Transient IMAP/connect/auth failures must not break folder navigation. Folder pages should render using cached data when available, always include INBOX in the sidebar fallback, and show a non-blocking banner that explains background retry behavior.

# Use Case U5 – Message Actions & Organization
U5.1 - The customer can open individual emails.
U5.2 - An email that has been seen will be marked as read; user can also mark it as unread.
U5.3 - Support message flagging (star/important) via IMAP flags.
U5.4 - Can create folders (IMAP server-side).
U5.4a - Folder management is exposed beyond the web UI: customers can create, rename, and delete mail folders. Create maps to IMAP CREATE, rename to IMAP RENAME, delete to IMAP DELETE. Creation is idempotent (creating an existing mailbox returns success without error). When `parent` is supplied, the new mailbox is nested using the server hierarchy delimiter from IMAP LIST (e.g. `parent<delim>name`). Non-ASCII mailbox names are encoded with modified UTF-7 (RFC 3501 §5.1.3). `list_folders` reflects create/rename/delete immediately via the per-user cache.
U5.4b - System folders are protected from destructive management: `INBOX`, `Sent`, `Drafts`, `Trash`, `Junk` (alias `Spam`), and `Bookings` can never be renamed or deleted through the application (REST API, MCP, or web UI); these are refused with a structured error. Folder management is exposed via the REST API (`/api/v1/mail/folders`) and MCP, alongside the existing web UI.
U5.5 - Can move emails to folders.
U5.6 - (Removed for now) Bulk actions are out of scope for MVP.
U5.7 - Support "mark all as read" per folder.
U5.8 - Support drag-and-drop of messages into folders.
U5.8a - During drag-and-drop, the UI provides clear visual feedback: valid drop-target folders highlight with a colored background and left-border accent on hover; the folder the message is currently in is dimmed as an invalid target; the dragged row shows reduced opacity. If the move fails, a toast notification informs the user with a retry suggestion.
U5.9 - The user can delete emails by moving them to Trash.
U5.9a - Delete banner behavior matches Archive: no countdown or auto-dismiss; banner stays visible until the user navigates away; Undo is available only while the banner is visible; Undo restores to the original folder; banner includes actions: Undo and "View Trash" (links to the Trash folder).
U5.10 - Archive: show Archive folder if present; create it on first archive action and move messages there.
U5.11 - Delete/archive/report-spam actions are immediate with an undo option (no confirmation dialog). For Archive, there is no countdown or auto-dismiss; the banner stays visible until the user navigates away, and Undo is available only while the banner is visible. The archive banner includes actions: Undo and "View Archived" (links to the Archive folder). Undo should restore to the original folder.
U5.12 - Spam/Junk: display the IMAP Junk/Spam folder if present and allow moving messages into it. Do not auto-create a Spam/Junk folder. If the server has no Junk/Spam folder, disable the Spam action and inform the user.
U5.13 - "Report spam/phishing": move to Junk/Spam and set IMAP \\Junk flag only when supported and enabled in customer settings. If the server rejects the \\Junk flag, disable the Spam action in settings and show: "Server doesn’t support Spam flags, the option is disabled in your account”.
U5.13a - Report spam banner behavior matches Archive/Delete: no countdown or auto-dismiss; banner stays visible until the user navigates away; Undo is available only while the banner is visible; Undo restores to the original folder; banner includes actions: Undo and "View Junk"/"View Spam" depending on which system folder exists (links to that folder).
U5.14 - No snooze functionality in MVP.
U5.15 - Delete protection (lock). Customers can protect messages and folders from accidental deletion.
  - U5.15a - Per-message lock stored as the IMAP keyword `$Locked`, cached locally the same way `\Seen`/`\Flagged` are (it round-trips through IMAP FETCH/STORE and the cache `flags` JSON list). Toggling the lock uses the same flag-sync path as read/starred. Because the lock lives on the IMAP message, it survives cache resets and is consistent across devices.
  - U5.15b - "Starred emails are protected from delete" is a policy: any message carrying `\Flagged` refuses delete / move-to-Trash until it is un-starred. This is on by default and can be toggled in customer settings (`protect_starred`). An explicit `$Locked` flag protects a message regardless of this setting.
  - U5.15c - Per-folder lock is stored in `CustomerSettings.protected_folders` (a JSON list, same pattern as `pinned_folders`). A locked folder refuses delete. System folders (U5.4b) are always protected and cannot be unprotected. Renaming a user folder is allowed; rename is blocked only for the system set.
  - U5.15d - Protection blocks delete, move-to-Trash, bulk-delete, and empty-Trash. Reorganizing (move to a real folder), archive, mark read, and star/unstar remain allowed. Bulk delete skips protected messages and reports them in the `failed` list with code `PROTECTED`.
  - U5.15e - Per-account keyword capability fallback: if the IMAP server rejects the `$Locked` keyword (custom keywords not permitted), the lock action is auto-disabled for that account (mirroring the `\Junk` behaviour in U5.13/U9.5), stored in `CustomerSettings.locked_keyword_prefs` (JSON dict keyed by account id). The web UI hides the lock control for that account; the API/MCP return a structured error.
  - U5.15f - Exposure: lock/unlock of an individual message (the `$Locked` keyword) is available via the REST API (the flags endpoints accept a `locked` boolean) and MCP, with the same `{data: ...}` envelope. The protection **policy** settings — `protect_starred`, `protected_folders`, and the per-folder protect toggle — are **web-UI-only by design** and are intentionally not exposed via REST/MCP; this prevents an agent from disabling the protection that exists to stop it from deleting things the user wants kept. **Enforcement**, however, is mandatory in every layer: the web UI, REST API, Python MCP tools, and TS MCP client all refuse delete and move-to-Trash on protected items and return the `PROTECTED` error (U5.15g). No path may bypass the check (move-to-Trash, single and bulk, included).
  - U5.15f.1 - The protected state is surfaced read-only across all API/MCP layers. Message list and detail responses (REST and MCP) include a `protected` boolean; folder list responses include a `protected` boolean per folder. This extends U5.15h to programmatic clients so agents can warn proactively ("this message is protected, I won't delete it") instead of discovering the state only from a 409 at delete time.
  - U5.15g - Protection errors are specific and actionable. When a delete/move-to-Trash is refused, the web UI, REST API, and MCP each return a message that states the **active reason** ("This message is starred.", "This message is locked.", or "This message is starred and locked.") and points the user at the resolution control ("Click Unstar in the ⋯ menu to allow deletion." / "Click Unlock …"). The `PROTECTED` error code stays stable for API/MCP clients; only the human-facing `message` becomes specific. The "Please retry…" suffix appended by the toast helper is suppressed for protection errors because there is nothing to retry.
  - U5.15h - Protected state is visible before a delete is attempted. The message detail header and each message-list row show a protected indicator (a muted lock icon, with a tooltip giving the reason) whenever a message carries `$Locked`, or carries `\Flagged` while `protect_starred` is enabled. The indicator is deliberately understated (UX6): it shows the state without calling attention to it. This makes the current protection state discoverable instead of surfacing it only as a delete-time error.

# Use Case U6 – Compose, Drafts & Sending
U6.1 - The user can send emails, including attachments. Compose is full-featured: new/reply/forward, CC/BCC, and HTML + auto-generated plain text.
U6.1a - The compose editor is a lightweight built-in WYSIWYG (no third-party editor library in MVP).
U6.1b - The compose HTML must use inline style attributes only (no embedded <style> tags), to maximize client compatibility.
U6.1c - CC and BCC inputs are hidden by default and revealed via "Add Cc" / "Add Bcc" toggles.
U6.1d - Compose keyboard tab order must be: To, (if visible) Cc, (if visible) Bcc, Subject, Body editor, Attachments, Read receipt, Send, Save Draft, then formatting toolbar buttons (bold/italic/underline/list/link/clear).
U6.2 - Attachment size limits are enforced by the SMTP server (no additional app-level limit in MVP).
U6.2a - In addition to SMTP enforcement, the app applies pre-send attachment size limits with clear structured errors before SMTP is attempted (see U6.14). SMTP rejection remains the final backstop.
U6.3 - Outgoing emails are saved to Sent using IMAP APPEND.
U6.4 - Drafts: if the IMAP server supports a Drafts folder, save drafts via IMAP APPEND with the `\Draft` flag set.
U6.4a - On save-draft/auto-save, if Drafts does not exist, attempt to create it once and then append. If Drafts creation/append fails, keep the user on compose and show a clear inline error with next steps.
U6.4b - Draft messages are visually distinguished in the message list with an amber "Draft" badge, pencil icon, and amber-tinted background. In the thread card, threads containing drafts show an amber left border.
U6.4c - When viewing a draft in the message detail view, show a prominent "Draft" banner at the top with "This message has not been sent yet." text. The toolbar shows "Edit draft" and "Discard" buttons instead of Reply/Reply All/Forward. Draft thread messages in the conversation view show an amber "Draft" badge and pencil avatar icon.
U6.4d - Resume draft: the compose page accepts a `draft_uid` query parameter. When provided, the draft is loaded from IMAP and the compose form is pre-filled with the draft's To, Cc, Bcc, Subject, and Body. The `draft_uid` is passed as a hidden field so subsequent auto-saves and manual saves replace the existing draft instead of creating duplicates.
U6.4e - Discard draft: `POST /mail/draft/<account_id>/<draft_uid>/discard` deletes the draft from the Drafts folder via IMAP. Available as a button on both the message detail view and the compose page.
U6.4f - Drafts in thread view: drafts matching a thread (by `thread_id` or normalized subject) are merged into the thread in the folder message list, sorted by date. Threads containing drafts sort to the top based on the draft's timestamp. In the message detail conversation view, draft messages from the Drafts folder are included in the thread if they match by subject.
U6.5 - Auto-save drafts while composing. Auto-save replaces the previous draft (if any) by deleting the old UID before appending the new one.
U6.5a - API draft support: the REST API exposes endpoints to create, replace, and delete drafts via IMAP APPEND to the Drafts folder. `POST /api/v1/mail/drafts` accepts the same fields as the send endpoint (`to`, `cc`, `bcc`, `subject`, `body_html`, `body_plain`) plus an optional `replace_uid` to update an existing draft (deletes the old UID before appending). Returns `{"status": "draft", "draft_uid": "<uid>", "message_id": "<id>"}`. `DELETE /api/v1/mail/drafts/<uid>` removes a draft from the Drafts folder. The send endpoint (`POST /api/v1/mail/messages`) accepts an optional `draft_uid`; after a successful send, the referenced draft is deleted from the Drafts folder. If Drafts folder does not exist on APPEND, the API creates it once (matching U6.4a behavior).
U6.6 - Provide an "undo send" grace period (5–10 seconds) before SMTP send.
U6.6a - After pressing Send, show a send status page with a visible countdown and actions: Undo and "Send now". "Send now" skips the remaining countdown and starts SMTP immediately.
U6.6b - Scheduled sends must continue in background even if the user navigates away from the send status page.
U6.6c - If send fails, show a clear error state in the send status UI with actions: Retry send (immediate) and Open Draft.
U6.6d - If a send fails while the user is no longer on the send status page, show a failure banner on the next mailbox load with actions: Retry send and Open Draft.
U6.7 - Support read receipts (request and display when received).
U6.8 - No email signatures in MVP.
U6.9 - No vacation/auto-reply in MVP.
U6.10 - Compose recipient fields (To, Cc, Bcc) use a chips/tags input with autocomplete from the contacts search API (U11.13). Typing 2+ characters triggers a debounced fetch to `/app/contacts/api/search?q=`; matching contacts appear in a dropdown showing name and email; selecting a contact inserts a removable chip. Manual email entry is also supported (Enter or comma creates a chip). Each chip validates email format on blur. Clicking a chip copies its email address to the clipboard and shows a brief "Copied!" tooltip. The chips component is a shared JS widget reused across To, Cc, and Bcc fields. Paste support: pasting comma-separated emails automatically creates individual chips.
U6.11 - Compose attachments support multiple files via both "browse" and drag-and-drop. Each attachment renders as a card showing a type icon, file name, size, a per-file upload progress bar, and a remove control. A drag-and-drop zone accepts one or more files at once and is visually highlighted while dragging.
U6.12 - Attachments use staged uploads: each selected/dropped file is uploaded immediately to a staging endpoint (`POST /app/mail/attachments/stage`) with a real per-file XHR progress bar; the Send and Save-Draft actions reference the resulting staged attachment IDs (plus a client-generated `compose_session_id`) rather than the final multipart files. Staging is scoped per user and per compose session under `data/mail_attachments/<user_id>/<compose_session_id>/`, cleaned up on successful send/manual-draft-save, with opportunistic garbage collection of abandoned staging directories older than 24 hours. Staged files can be removed before sending via `DELETE /app/mail/attachments/<file_id>`.
U6.13 - "Attach from Docs": the compose page can attach documents from the user's Docs module. Documents are attached in their stored format (ODF for native docs; original format for non-ODF originals). The picker is populated via a new session-auth JSON endpoint `GET /app/docs/api/list?account_id=&q=`; the file bytes are fetched client-side via `/app/docs/<doc_id>/download` (which gains a backward-compatible `account_id` query parameter defaulting to the session active account) and then staged exactly like a local file. No cross-module Python imports — the browser is the bridge between modules.
U6.14 - App-level attachment size limits, configurable via `MAIL_ATTACHMENT_MAX_FILE_BYTES` (default 25 MB per file) and `MAIL_ATTACHMENT_MAX_TOTAL_BYTES` (default 50 MB per message), enforced at stage time (per-file and cumulative across the compose session) and re-checked at send, returning structured errors before SMTP.

# Use Case U7 – Search
U7.1 - Search: results from the local cache are shown immediately, then expanded with IMAP SEARCH (headers only). Offer an optional full-server search after the initial search completes.
U7.2 - Global search box visible in the main app header.
U7.3 - Allow searching within the current folder.

# Use Case U8 – Content Safety & Downloads
U8.1 - External images are blocked by default; user can choose to show external images.
U8.2 - Sanitize HTML content on display (strip scripts/unsafe elements).
U8.3 - No S/MIME or PGP support in MVP.
U8.4 - Calendar invite handling: .ics attachments are detected and rendered as inline preview cards in the email view. See U12.34–U12.39c for full .ics import, invitation handling, cancellation, conflict warnings, and create-event-from-email.
U8.5 - Allow downloading a message as .eml.
U8.6 - Allow printing a message.
U8.7 - No server-side filtering/rules in MVP.
U8.8 - Attachments support three actions: Download (all formats), View (pandoc-convertible formats rendered as HTML in a new tab), and Open in Docs (all uploadable formats stored as a new document in the docs module). Non-ODF files are stored in their original format without conversion; the user can convert to ODF later via the docs module's "Convert to Docs" action (U13.38–U13.42). Supported formats are defined in `app/shared/pandoc_formats.py`. Non-uploadable formats remain download-only.

# Use Case U9 – Customer Settings
U9.1 - Customer settings page with standard settings; include polling interval, default state for the preview pane, a global default sort order (date descending), and per-user timezone setting (default from browser). Message list and search date/time rendering must use the user's configured timezone (see UX3a).
U9.1a - Date-desc sorting must be based on a normalized message timestamp (not raw Date header text) so newest messages are reliably shown first.
U9.4 - Customer settings include a "Reset Cache" action that clears the local cache for the currently active customer account only (IMAP untouched). Next access re-syncs.
U9.2 - Language: English only for MVP.
U9.3 - Theme: light/dark toggle.
U9.5 - Customer settings include a per-account toggle for the Spam action (enable/disable). Default is enabled; if the server rejects the \\Junk flag, auto-disable and inform the user per U5.13.
U9.6 - Customer settings include an "API Access" section (see U14) where the customer can enable/disable API access and manage API tokens. This section requires password confirmation for all state-changing actions.

# Use Case U10 – Mailbox Import / Migration
U10.1 - The product supports mailbox import requests initiated by admins. For MVP, supported source types are Google (Gmail / Google Workspace) and Google Takeout mail export; future source types IMAP and O365 are planned but out of scope for implementation.
U10.1a - Google source access uses OAuth 2.0 with the user's normal Google or Google Workspace account in a browser-based consent flow. Raw Google account passwords must never be collected or accepted by the app for source access.
U10.1c - Google Takeout import is a separate source type from live Google OAuth import. For MVP, the admin chooses the source mode when creating the import request so the user flow is unambiguous.
U10.1b - Destination is always IMAP for MVP and is configured by the admin as part of the import request. Destination authentication is password-only in MVP.
U10.2 - The signed import link opens a dedicated import flow where the user authorizes Google access and confirms the import. The link may be reused until expiry unless disabled by an admin.
U10.3 - Imports are safe by design:
  - Source access is read-only.
  - Destination access is append-only plus post-append flag updates needed to preserve read/unread and starred state.
  - The system must never delete or modify source messages.
  - The system must never delete destination messages as part of an import.
U10.4 - Imports copy full raw messages including attachments to the destination IMAP mailbox.
U10.4a - Google Takeout imports also copy full raw messages including attachments to the destination IMAP mailbox.
U10.5 - Folder scope for MVP is limited to standard folders only: Inbox, Sent, Drafts, and Archive. Spam/Junk and Trash are excluded from import by default for MVP.
U10.5a - If a source message appears in multiple Google labels/folders, it must be imported once only. Folder precedence for a single imported copy is: Sent, Drafts, Trash, Archive, Inbox.
U10.5b - Because Trash is excluded for MVP, the effective precedence for imported folders is: Sent, Drafts, Archive, Inbox.
U10.6 - Imports must be idempotent across reruns. Re-running the same import request must only append new source messages that have not already been imported for that request/destination pair.
U10.6a - Idempotency must use a stable per-source message identifier recorded in the application database. For Google imports, use the provider's stable message identifier rather than relying only on RFC Message-ID.
U10.6c - For Google Takeout imports, idempotency must use a deterministic content-based dedupe key per message because Takeout exports do not provide the Gmail API message identifier. Re-uploading the same Takeout file, or a file containing previously imported messages, must not duplicate messages on the destination.
U10.6b - The system must persist per-import checkpoints and imported-message mappings so interrupted runs can resume safely without duplicating messages.
U10.7 - Imported messages should preserve message date and best-effort standard state on destination IMAP: read/unread and flagged/starred. If the destination server cannot represent a source state, import the message and record a non-fatal warning.
U10.8 - Imports run asynchronously as background jobs with resumable progress. The user-facing import page must show status, counts, and clear next steps on failure without exposing sensitive data.
U10.8a - The admin-facing back office shows status, counts, timestamps, and sanitized errors only. It must not expose message content or attachment names.
U10.8b - Google Takeout uploads may be very large. The upload flow must support resumable or chunked transfer, temporary disk staging, and asynchronous background processing so a single HTTP request is not required to hold the entire file in memory or finish the full import.
U10.8c - For MVP, Google Takeout accepts MBOX mail files only. Full archive extraction from ZIP/TGZ Takeout bundles is out of scope unless the archive contains exactly one supported MBOX file and the implementation explicitly adds staged extraction support.
U10.9 - UI behavior for import actions follows the standard inline-action rules: show a subtle inline spinner, disable the action while a request is in flight, re-enable it on success or failure, and show a clear non-blocking error with retry guidance.
U10.10 - For Gmail / Google Workspace imports, use the Gmail API with minimum required read-only scopes if policy and verification requirements permit. The design must account for Google's OAuth verification and restricted-scope requirements before production rollout.
U10.10a - Product/policy caveat: Google Workspace API policy lists "applications that export email on a one-time or manual basis" as a disallowed Gmail API use case. Before releasing Google import to production, confirm that the intended mailbox-import workflow is compliant or adjust the product/authorization approach accordingly.
U10.11 - Google Takeout upload data must be stored on disk only as long as needed to complete ingestion, validation, resume, and retry. Temporary staged files must be deleted after successful completion or bounded retention expiry after failure.

# Use Case U11 – Contacts

U11.1 - Contacts module uses CardDAV as the upstream source of truth with a local SQLite cache (same pattern as IMAP mail cache).
U11.2 - Contact data is stored in the user's individual SQLCipher database (per-user, same as mail), encrypted at rest using the same per-user key derived from their password (M6/M6a).
U11.3 - Contact module lives at URL prefix `/app/cowntacts/` with its own full MVC stack under `app/modules/contacts/`.
U11.4 - Contacts are tied 1:1 to mail accounts. Each mail account has exactly one CardDAV address book, using the same domain credentials. The account switcher from the mail sidebar is reused.
U11.5 - CardDAV target server for MVP is Radicale; implementation follows RFC 6352 and should be server-agnostic where practical.
U11.6 - Contact sync follows the same background-worker pattern as mail: initial sync on first access, incremental sync using sync-token (CTag/ETag) when supported, with full re-sync fallback. Per-address-book sync state is stored in the user's cache.
U11.7 - vCard field support (MVP): FN (formatted name), N (structured name), EMAIL (WORK/HOME), TEL (WORK/HOME/CELL), ORG, TITLE, NOTE. Other vCard fields are preserved raw but not exposed in the UI.
U11.8 - MVP features: list contacts, view contact detail, create contact, edit contact, delete contact, search contacts (by name/email/phone).
U11.9 - Contact list view: paginated list showing name, primary email, primary phone; sorted alphabetically by FN; supports search filtering.
U11.10 - Contact detail view: displays all supported vCard fields; edit and delete actions available.
U11.11 - Contact create/edit: form-based input for supported vCard fields; saves to CardDAV via PUT and updates local cache.
U11.12 - Contact delete: removes from CardDAV and local cache; immediate with undo option (same pattern as mail delete).
U11.13 - Shared contacts search API: a lightweight endpoint (`/app/contacts/api/search?q=`) returns name + email pairs for autocomplete in mail compose (To/Cc/Bcc). No cross-module Python imports; the mail module calls this endpoint client-side via fetch.
U11.14 - No contact groups/lists in MVP.
U11.15 - No contact import/export in MVP.
U11.16 - No contact sharing in MVP.
U11.17 - Auto-save recipients as contacts: a new endpoint (`/app/contacts/api/auto-save`) accepts a JSON POST with `{account_id, recipients: [{email, name?}]}`. For each recipient whose email is not already in the user's contacts (exact match against email_work and email_home), a new vCard contact is created (FN = name if provided, else email local-part; EMAIL;TYPE=WORK = the address) via CardDAV PUT and cached locally. The mail compose form calls this endpoint client-side on send (before SMTP), with a 3-second timeout; failures are non-blocking (email sends regardless). If CardDAV is not configured, auto-save is skipped silently.

# Use Case U12 – Calendar (CalDAV via Radicale)

U12.1 - Calendar module uses CalDAV as the upstream source of truth with a local SQLite cache (same pattern as IMAP mail cache and CardDAV contacts cache).
U12.2 - Calendar data is stored in the user's individual SQLCipher database (per-user, same as mail and contacts), encrypted at rest using the same per-user key derived from their password (M6/M6a).
U12.3 - Calendar module lives at URL prefix `/app/calendar/` with its own full MVC stack under `app/modules/calendar/`.
U12.4 - CalDAV target server for MVP is Radicale; implementation follows RFC 4791 (CalDAV) and RFC 5545 (iCalendar) and should be server-agnostic where practical.
U12.5 - CalDAV connection is configured per-domain by the admin (same pattern as IMAP/SMTP per U1.3): CalDAV base URL, authentication method, and credentials. Each domain maps to one CalDAV server. Radicale serves both CardDAV (U11) and CalDAV on the same base URL; admin configures CalDAV URL alongside existing IMAP/SMTP/CardDAV domain settings.
U12.6 - Calendars are tied 1:1 to mail accounts. Each mail account discovers its CalDAV calendars using the same domain credentials. The account switcher from the mail sidebar is reused.
U12.7 - Multiple calendars per account: the user can see all calendars from their CalDAV account. Each calendar has a name, color (from CalDAV or locally assigned), and can be toggled visible/hidden in the UI. Users can create and delete calendars via CalDAV MKCALENDAR/DELETE.
U12.7a - Default calendar auto-creation: on first sync, if the user has no calendars on the CalDAV server, the app automatically creates a default personal calendar via MKCALENDAR. The calendar name is derived from the user's email local part (e.g., `user@example.com` → `User`), capitalized. The default calendar is marked with `is_default` in the local cache and cannot be deleted (only renamed). Additional calendars can be created by the user at any time.
U12.7b - Defensive UI: the "New event" button is only shown when at least one calendar exists in the cache. If no calendars exist (e.g., CalDAV server unreachable during sync), the UI shows a clear message with a link to retry sync or create a calendar. Event creation routes must never silently redirect back to the calendar index; they must render an informative error if calendars are unavailable.
U12.8 - Calendar sync follows the same background-worker pattern as mail and contacts: initial sync on first access, incremental sync using sync-token (CTag/ETag) when supported, with full re-sync fallback. Per-calendar sync state is stored in the user's cache. Sync interval follows the same background cadence as contacts.

## iCalendar Support

U12.9 - iCalendar component support (MVP): VEVENT with SUMMARY, DESCRIPTION, LOCATION, DTSTART, DTEND (or DURATION), DTSTART;VALUE=DATE (all-day events), UID, RECURRENCE-ID, RRULE, EXDATE, RDATE, ORGANIZER, ATTENDEE, STATUS (TENTATIVE/CONFIRMED/CANCELLED), CATEGORIES, CLASS, URL, CREATED, LAST-MODIFIED, SEQUENCE. VALARM for reminders (DISPLAY and EMAIL action types). VTODO and VJOURNAL are out of scope for MVP.
U12.9a - iCalendar properties not explicitly listed above are preserved raw in the cache but not exposed in the UI, same pattern as vCard field handling in U11.7.

## Calendar Views

U12.10 - Default view is the week view. User's last-selected view is persisted in customer settings.
U12.11 - Month view: shows events as colored blocks on calendar days; click a day to see day detail or create event. Events that overflow the day cell show a "+N more" indicator.
U12.12 - Week view: hourly grid (00:00–23:00) showing events as colored blocks; current time shown as a red horizontal line. Supports click-and-drag to quick-create an event with the time range pre-filled.
U12.13 - Day view: detailed hourly grid for a single day; same current-time indicator and quick-create as week view.
U12.14 - Mini calendar widget in the sidebar for date navigation; highlights days with events. Clicking a date navigates the main view to that date.
U12.15 - Agenda/list view: upcoming events in a scrollable chronological list (next 30 days by default, with load-more). Shows summary, date/time, calendar color dot, and location.

## Event CRUD

U12.16 - Create event: modal or full-page form with fields for summary, description, location, start/end datetime (or all-day), calendar selection, category, repeat rule, attendees, and reminders. Saves to CalDAV via PUT and updates local cache.
U12.17 - Edit event: same form pre-populated with existing values. Updates via PUT with incremented SEQUENCE when attendees exist. For recurring events, offers choice: edit this occurrence only, edit this and future occurrences, or edit the entire series.
U12.18 - Delete event: removes from CalDAV and local cache; immediate with undo option (same pattern as mail/contacts delete per U5.11). For recurring events, offers the same occurrence/series choice as edit.
U12.19 - Quick create: click-and-drag on week/day grid creates a minimal event with pre-filled time range; opens an inline edit popover for summary and calendar selection. Full edit available via click-through.
U12.20 - Drag-and-drop resize/move: events can be dragged to a different time/day and resized to change duration in week and day views. Saves updated DTSTART/DTEND via CalDAV PUT.

## Recurring Events

U12.21 - Recurring event support: RRULE with FREQ (DAILY, WEEKLY, MONTHLY, YEARLY), INTERVAL, COUNT, UNTIL, BYDAY (MO,TU,...), BYMONTHDAY, and BYSETPOS. UI provides a "Repeat" dropdown with common presets (daily, weekly, biweekly, monthly, yearly) and a custom repeat dialog for advanced options.
U12.22 - Exception dates: EXDATE removes specific occurrences from a recurring series. RDATE adds extra occurrences. Both are supported in the UI via "Remove this occurrence" and "Add an extra date" actions.
U12.23 - Editing a recurring event prompts the user to choose scope: (1) this occurrence only (creates an exception with RECURRENCE-ID), (2) this and future occurrences (modifies RRULE UNTIL and creates a new series), or (3) the entire series (modifies the master VEVENT).
U12.23a - Deleting a recurring event follows the same scope prompt as editing. "This occurrence only" adds an EXDATE; "Entire series" deletes the master VEVENT.

## Attendees & Invitations

U12.24 - Attendees: events can have one or more attendees specified by email address. The calendar owner is the ORGANIZER by default. Attendee fields: email (required), display name (optional), ROLE (default REQ-PARTICIPANT), RSVP (default TRUE), PARTSTAT (response status).
U12.24a - Attendee autocomplete: the attendee input uses the shared recipient-chips JS widget (same as mail compose U6.10) with autocomplete from the contacts search API (U11.13). Typing 2+ characters triggers a debounced fetch to `/app/contacts/api/search?q=`; matching contacts appear in a dropdown showing name and email; selecting a contact inserts a removable chip with the contact's name and email. Manual email entry is also supported. The shared chips widget is located at `app/static/js/recipient-chips.js`.
U12.24b - Attendee auto-save: when an event with attendees is created or updated, the calendar module calls the contacts auto-save endpoint (`/app/contacts/api/auto-save`) client-side with the attendee list, following the same non-blocking pattern as mail compose (U11.17): 3-second timeout, failures are non-blocking (event saves regardless). Uses `fetch` with `keepalive: true` to ensure the request completes even after page navigation.
U12.25 - Invitation sending (iMIP): when an event with attendees is created or updated and the organizer is the current user, a Gmail-style modal asks whether to send updates to guests. If confirmed, the app sends an iMIP invitation email (RFC 6047) via SMTP containing a multipart/alternative with text/calendar and application/ics parts. The email contains the VEVENT with METHOD:REQUEST. When an event with attendees is deleted, a modal offers to notify guests with METHOD:CANCEL. The ORGANIZER field is automatically set to the current user's email on event creation.
U12.26 - Invitation replies: when the user receives a reply email (METHOD:REPLY with PARTSTAT), the app processes the reply and updates the attendee's PARTSTAT in the corresponding cached event. Replies are processed automatically on email fetch (during IMAP sync).
U12.27 - Invitation response status: the UI shows each attendee's response status (accepted, declined, tentative, needs-action) with a color-coded indicator in the event detail and edit views.
U12.28 - Counter proposals: out of scope for MVP. Attendees who want to propose a new time should decline and propose via email.
U12.29 - Free/busy lookup: when adding attendees, the app shows a simplified free/busy check for the proposed event time. For MVP, this is best-effort based on the user's own cached events (no cross-user CalDAV free/busy query unless the CalDAV server supports it).
U12.29a - If the CalDAV server supports CalDAV free/busy queries (RFC 4791 section 8.4), use it for attendee availability. Otherwise, fall back to local cache only and show a note that availability data may be incomplete.

## Reminders / Alarms

U12.30 - Reminders via VALARM: each event can have zero or more reminders. Supported reminder types: DISPLAY (in-app notification) and EMAIL (email sent via SMTP). TRIGGER is relative to DTSTART (default: -PT15M, i.e., 15 minutes before).
U12.31 - Default reminder: configurable per calendar in calendar settings. When a new event is created, it inherits the calendar's default reminder. User can add/remove/modify reminders per event.
U12.32 - In-app display reminders: shown as a non-blocking notification banner at the top of the calendar UI with the event summary and time. Actions: "Dismiss" (acknowledges the reminder) and "Snooze" (re-shows after a selectable delay: 5 min, 10 min, 15 min, 30 min, 1 hour).
U12.32a - In-app reminders are checked client-side against cached event data. A background worker evaluates upcoming VALARMs and sends email reminders; in-app display reminders are triggered by the frontend based on cached VALARM data.
U12.33 - Email reminders: sent via SMTP using the user's mail account. The email contains the event summary, date/time, location, and description. No reply handling for reminder emails.

## .ics Import from Email

U12.34 - The app detects .ics (text/calendar) attachments and text/calendar MIME parts in email messages.
U12.35 - Inline event preview: when an .ics is detected, the email message view shows an inline preview card with event summary, date/time, location, organizer, and attendee list. The card is rendered above the email body.
U12.36 - For invitation-type .ics (METHOD:REQUEST), the preview card shows response actions: Accept, Tentative, Decline. Selecting a response imports the event into the user's selected calendar (with the attendee's PARTSTAT set accordingly) and sends a METHOD:REPLY email to the organizer.
U12.37 - For non-invitation .ics (METHOD:PUBLISH or no METHOD), the preview card shows an "Add to calendar" button that imports the event into the user's selected calendar.
U12.38 - Bidirectional email-calendar link: when an .ics is imported, the calendar event stores the email message ID as metadata and the email cache stores the calendar event UID — both are loose references, not foreign keys. In the email view, the .ics preview card shows a "View in calendar" link after import. In the calendar event detail view, a "View original email" link navigates to the email message. No cross-module database coupling.
U12.39 - .ics import updates: if the same event UID is imported again (e.g., an updated invitation), the existing event is updated in place (using SEQUENCE for conflict resolution) rather than duplicated.
U12.39a - Cancellation handling: when an .ics with METHOD:CANCEL is received for an event already in the user's calendar, the event is marked as cancelled (STATUS:CANCELLED) with a prominent visual indicator (strikethrough title, red "Cancelled" badge). The user can choose to keep or delete the cancelled event. If the event is not in the calendar, the preview card shows "This event has been cancelled" without offering import.
U12.39b - Conflict warning on RSVP: when the user clicks Accept or Tentative on an invitation, the app checks the user's cached calendar events for overlapping time ranges before importing. If a conflict is detected, the preview card shows a warning listing the conflicting events (summary + time). The user can still proceed with accepting. If no conflict is detected, the import proceeds without interruption.
U12.39c - "Create event" from email: a "Create event" action is available on the email toolbar (alongside archive, delete, etc.). Clicking it opens the calendar event creation form (U12.16) pre-populated with: subject → SUMMARY, plain-text body (truncated to 2000 characters) → DESCRIPTION, sender email → ATTENDEE. The user can edit all fields before saving. The action is implemented via client-side navigation to `/app/calendar/events/new` with pre-fill query parameters (subject, body, attendee); no cross-module Python imports.

## Calendar Management

U12.40 - Calendar list: sidebar shows all calendars for the current account, each with a colored checkbox for visibility toggle, the calendar name, and a "⋯" menu with Edit, Delete, and Settings actions.
U12.41 - Create calendar: "New calendar" action creates a new CalDAV calendar collection via MKCALENDAR. User sets name and color.
U12.42 - Edit calendar: rename or change color. Updates the CalDAV calendar properties (calendar-color, displayname).
U12.43 - Delete calendar: removes the entire CalDAV calendar collection and its events from local cache. Requires confirmation dialog since this is destructive. Confirmation must include the calendar name and event count.
U12.44 - Calendar settings per calendar: default reminder, timezone override (if different from user timezone), and color.

## Shared API

U12.45 - Shared calendar search API: a lightweight endpoint (`/app/calendar/api/search?q=`) returns event summaries + dates for use in other modules. No cross-module Python imports; other modules call this endpoint client-side via fetch.

## Out of Scope for MVP

U12.50 - No calendar sharing with other users in MVP.
U12.51 - No calendar subscription (subscribing to external .ics URLs, i.e., WebCal) in MVP.
U12.52 - No task/todo (VTODO) support in MVP.
U12.53 - No journal (VJOURNAL) support in MVP.
U12.54 - No resource booking (rooms, equipment) in MVP.
U12.55 - No delegated access (acting on another user's calendar) in MVP.

# Use Case U13 – Docs (Collabora Online via WOPI)

## Module & Infrastructure

U13.1 - Docs module lives at URL prefix `/app/docs/` with its own full MVC stack under `app/modules/docs/`.
U13.2 - Document editing is provided by Collabora Online (CODE) integrated via the WOPI protocol. Collabora runs as a separate Docker container in the same infrastructure.
U13.3 - WOPI endpoints (`/app/docs/wopi/files/<doc_id>/*`) are served by the docs module. The Flask app acts as WOPI host; Collabora is the WOPI client.
U13.4 - WOPI authentication uses signed JWT tokens scoped to a single document and session with a configurable expiry (default 8 hours). Tokens are generated when the user opens a document and validated on every WOPI callback. Collabora never receives user credentials.
U13.5 - Collabora is accessed by the browser through a reverse proxy path (e.g., `/collabora/`) to avoid CORS issues. The Flask app communicates with Collabora internally on its container port (9980).

## Document Storage & Encryption

U13.6 - Documents are stored as files on disk under `data/docs/<user_id>/<account_id>/<doc_id>/` using the same per-user directory structure as the mail cache.
U13.7 - Document files are stored unencrypted on disk (the file is the source of truth, same pattern as IMAP/CardDAV/CalDAV where the upstream stores data unencrypted). Document metadata (name, type, size, dates) is stored encrypted at rest in the user's SQLCipher database (M6/M6a). Access control is enforced by WOPI token authentication and user session — the per-user key in memory does not provide meaningful additional protection for files on the same host.
U13.8 - Document metadata (name, type, size, created_at, updated_at, account_id, user_id) is stored in the user's individual SQLCipher database, not the main app database.
U13.9 - Supported file types (MVP): ODF text (.odt), ODF spreadsheet (.ods), ODF presentation (.odp), ODF drawing (.odg, produced by PDF conversion). Non-ODF files (e.g., .pdf, .docx, .xlsx, .pptx) are stored in their original format. The user can optionally convert them to ODF via a "Convert to Docs" action (see U13.39a for the per-source target), which creates a separate editable ODF document alongside the original.

## Metadata Embedding & Cache Recovery

U13.85a - **Embedded metadata (ODF)** — every ODF document file on disk carries a JSON metadata blob in its `meta.xml` (`<meta:user-defined meta:name="x-locoroo-meta">` element). This makes the per-user SQLCipher cache fully reconstructable from disk files alone.
U13.85a2 - **Sidecar metadata (non-ODF)** — non-ODF originals (PDF, DOCX, etc.) store metadata in a `meta.json` file alongside the `content` file in the same directory. Since non-ODF files are opened read-only by Collabora, the sidecar never goes stale from WOPI PutFile overwrites. Sidecar metadata is updated atomically with the cache DB on rename, soft-delete, and restore operations.
U13.85a3 - **Sidecar fields** — `meta.json` contains the same fields as the ODF embedded blob plus `original_format` (e.g., `"pdf"`, `"docx"`) and `user_id`.
U13.85b - **Metadata fields** — the embedded JSON object contains: `name`, `doc_type`, `original_format` (non-ODF only), `account_id`, `user_id`, `created_at`, `updated_at`, `deleted_at` (if trashed), `file_size`, `folder_path` (empty string for root, slash-separated nested paths otherwise — see U13.90), and `tags` (JSON array of tag strings — see U13.91).
U13.85c - **Injection points** — metadata is injected into the file at: document creation (empty template), upload (after conversion for ODF; sidecar written for non-ODF originals), WOPI PutFile (Collabora save, ODF only), rename, soft-delete (trash), and restore. This ensures the on-disk file always reflects the latest metadata state.
U13.85d - **Resync** — `resync_docs(user_id, account_id, cache_db)` scans `data/docs/<user_id>/<account_id>/`, extracts metadata from each document file, and rebuilds the `documents` table in the SQLCipher cache. Existing entries (matching `doc_id`) are skipped to avoid overwriting newer data.
U13.85e - **Resync trigger** — resync is triggered explicitly by the user via a "Sync" button in the docs list page, or programmatically via `?resync=1` query parameter. It is NOT auto-triggered on page load (too slow with thousands of production files).
U13.85f - **Fallback for files without metadata** — resync checks in order: (1) `meta.json` sidecar (non-ODF originals), (2) ODF `meta.xml` embedded metadata, (3) heuristics: `doc_type` guessed from ZIP content (keywords "spreadsheet"/"presentation" in `content.xml`), `name` defaults to the directory UUID, timestamps default to file mtime. For non-ODF files without a sidecar, `original_format` is inferred from file magic bytes and `doc_type` follows `target_odf_type` (U13.39a): a bare PDF (`%PDF`) recovers as `doc_type="odg"`, `original_format="pdf"`; Office ZIP formats infer their ODF counterpart; other unknowns default to `"odt"`.
U13.85g - **DocShare reconciliation** — `DocShare` records in the main app DB survive a cache nuke because `doc_id` equals the directory name on disk. After resync, shares reconnect automatically via `doc_id` match. No explicit reconciliation step is needed.

## Account & Privacy

U13.10 - Documents are tied 1:1 to mail accounts, same as contacts and calendar. Each mail account has its own document space. The account switcher from the mail sidebar is reused.
U13.11 - Data privacy follows M4: only the owning user can access document content. Admins and managers cannot see document names, content, or metadata.
U13.12 - Docs are visible in the app launcher (waffle menu) for customer users only.

## Document List

U13.13 - Document list view: paginated grid/list showing document name, type icon, last modified date, and file size. Sorted by last modified descending.
U13.14 - Document type icons distinguish between text, spreadsheet, and presentation.
U13.15 - Empty state: when no documents exist, show a clear "No documents" message with a prominent "Create document" action.
U13.16 - Search/filter by document name (client-side filtering for MVP).

## Create Document

U13.17 - "New document" action shows a dropdown to pick type: Document (.odt), Spreadsheet (.ods), Presentation (.odp).
U13.18 - Creating a document generates a UUID, creates the metadata record, writes an empty ODF template file of the selected type, and redirects to the editor.
U13.19 - The document name defaults to "Untitled Document" / "Untitled Spreadsheet" / "Untitled Presentation" based on type.

## Open / Edit

U13.20 - Clicking a document in the list opens the editor view: a full-page iframe embedding Collabora with the WOPI src URL and access token.
U13.21 - The editor view shows a minimal top bar with: back-to-list link, document name (editable inline), and a dropdown with Download, Rename, and Delete actions. The top bar is always visible (not hover-to-reveal) and the Collabora iframe is sized to fit below it so the app bar never overlaps Collabora's own toolbar.
U13.22 - Collabora auto-saves via WOPI putFile. The docs module updates `updated_at` on every putFile callback.
U13.23 - WOPI CheckFileInfo returns: document name, size, owner user ID, read-only flag (false for owner), user display name (email local-part), and last modified timestamp. For non-ODF originals (`original_format` non-NULL), `ReadOnly` is always `true` and `BaseFileName` includes the original extension so Collabora opens the correct viewer.
U13.24 - If the WOPI token is expired or invalid, Collabora shows an error and the user is prompted to reopen the document from the list.

## Rename

U13.25 - Rename document: inline edit from the editor top bar or a rename action in the document list context menu. Updates metadata and notifies Collabora via WOPI CheckFileInfo on next interaction.
U13.26 - Document name validation: required, max 255 characters, no `/`, `\`, or null bytes.

## Delete

U13.27 - Delete document: immediate with undo option (same pattern as mail delete per U5.11). Moves document to a trash state (soft delete with `deleted_at` timestamp).
U13.28 - Trashed documents are excluded from the main list and shown in a "Trash" section at the bottom of the document list.
U13.29 - Undo within the same session restores the document. Trashed documents are permanently deleted after 30 days or on explicit "Empty trash" action.

## Download

U13.30 - Download document: downloads the file in its native ODF format.
U13.31 - Export as PDF: uses Collabora's WOPI export capability to convert and download as PDF.

## Upload

U13.32 - Upload document: file upload control accepts .odt, .ods, .odp, .docx, .xlsx, .pptx, .pdf. ODF files are stored directly. Non-ODF files are stored in their original format with a `meta.json` sidecar; no automatic conversion occurs on upload.
U13.32a - Upload uses AJAX (XMLHttpRequest) with a visible progress bar showing real-time byte-level progress. ODF files open directly in the Collabora editor. Non-ODF files open in Collabora read-only view.
U13.32b - On successful upload, the document editor opens in a new browser tab. If the popup is blocked, an inline "Open document" link is shown as a fallback so the user can manually navigate to the editor. The upload button is disabled while the request is in-flight.
U13.33 - Upload creates a new document record. For non-ODF files, the `original_format` field records the source format (e.g., `"pdf"`, `"docx"`); `doc_type` is set to the target ODF type that conversion would produce (e.g., `"odt"` for PDF/DOCX, `"ods"` for XLSX). For ODF files, `original_format` is NULL.
U13.34 - Max upload size: 50 MB for MVP (configurable via environment).

## Convert to Docs

U13.38 - Non-ODF documents show a "Convert to Docs" action in the document list (format badge + button) and a "Convert to editable document" button in the Collabora editor toolbar (read-only mode).
U13.39 - Convert action (`POST /app/docs/<doc_id>/convert`) reads the original file, converts it to ODF via Collabora or pandoc (depending on format), and creates a **new** document record. The original non-ODF document is preserved unchanged.
U13.39a - **Per-source ODF target** — the target ODF type is derived from the source extension via `target_odf_type()` (`app/shared/pandoc_formats.py`), the single source of truth shared by upload and convert: Office text (.docx/.doc/.rtf/.html/.txt/.md/etc.) → `.odt`; spreadsheets (.xlsx/.xls/.csv) → `.ods`; presentations (.pptx/.ppt) → `.odp`. **PDF → `.odg` (Drawing)**, because LibreOffice/Collabora imports PDFs as vector Draw documents (one page = one draw layer); requesting `.odt` makes Collabora reject the save (HTTP 401, `X-ERROR-KIND: savefailed`). The converted PDF is a drawing of the rendered pages, not reflowable editable text (no OCR). This mapping is mirrored by the REST API (`POST /api/v1/docs/documents/<id>/convert`) and MCP (`docs_convert_document`) convert paths.
U13.40 - The converted document has the same name as the original (without the original extension), `doc_type` matching the ODF type, and `original_format` set to NULL. It is stored in a separate doc directory with standard ODF embedded metadata.
U13.41 - After conversion, the user is redirected to the converted document's editor view. The original remains in the docs list with its format badge.
U13.42 - The documents table gains an `original_format` column (TEXT, nullable). When non-NULL, the document is a non-ODF original (PDF, DOCX, etc.) and should be opened read-only in Collabora.

## Integration Points

U13.35 - The docs module registers via `register(app)` in `app/modules/docs/__init__.py`, following the same blueprint pattern as mail/contacts/calendar.
U13.36 - The module switcher (app launcher) gains a "Docs" entry with a document icon, linking to `/app/docs/`.
U13.37 - No cross-module Python imports. Any future cross-module interaction (e.g., "save email attachment as document") uses client-side fetch to the docs API.

## Document Sharing

U13.60 - Document sharing allows a document owner to share a document with specific email addresses, granting view or write permission.

U13.60a - **Data model** — a `DocShare` table in the main app.db (not per-user cache) with columns: `id` (PK), `doc_id` (TEXT), `owner_user_id` (FK users.id), `owner_account_id` (INTEGER), `share_token` (TEXT, unique UUID for public access URL), `permission` (TEXT: 'view' or 'write'), `share_type` (TEXT: 'internal' or 'link'), `recipient_email` (TEXT, nullable — set for all shares), `revoked_at` (TEXT, nullable), `view_count` (INTEGER, default 0), `last_accessed_at` (TEXT, nullable), `doc_name` (TEXT, denormalized from owner's cache), `doc_type` (TEXT, denormalized), `doc_size` (INTEGER, denormalized, default 0), `doc_updated_at` (TEXT, denormalized), `created_at` (TEXT).

U13.60b - **Denormalized metadata** — document name, type, size, and updated_at are copied from the owner's per-user cache into the `DocShare` row at share creation time. These fields are updated when the owner renames the document (from the rename controller) and on each WOPI putFile callback (share access). This avoids needing to open the owner's encrypted cache when rendering "Shared with me" lists or WOPI responses for shares.

U13.60c - **Two share modes**:
  - **Internal** (`share_type = 'internal'`): the recipient email domain matches an active domain in the `domains` table. Internal shares appear in the recipient's "Shared with me" docs list section. The recipient authenticates normally and accesses the document through their authenticated session.
  - **External** (`share_type = 'link'`): the recipient email domain does not match any managed domain. External shares use a public access URL `/app/docs/s/<share_token>` that requires no login. The share token is a UUID generated at share creation time.

U13.60d - **Each email gets its own share row** — creating independent access records per recipient. This allows individual revocation without affecting other recipients' access.

U13.60e - **Permissions**: `view` grants read-only access (Collabora loads with `ReadOnly=True`). `write` grants edit access. Both internal and external shares support both permission levels. The owner selects the permission when creating the share.

U13.60f - **Email invitations**: on share creation, if `recipient_email` is provided, the app sends an invitation email via SMTP using the owner's domain SMTP settings. The email contains: owner name/email, document name, permission level, and a link to the document. For internal users, the link is `/app/docs/` (where they see it in "Shared with me"). For external users, the link is `/app/docs/s/<share_token>`.

U13.60g - **Revocation**: the owner can revoke any share at any time. Revoked shares immediately return 404 for external access and are hidden from "Shared with me" for internal users. Revocation sets `revoked_at` on the share row. Manual revocation only (no auto-expiry in MVP).

U13.60h - **Access tracking**: every access to a shared document increments `view_count` and updates `last_accessed_at` on the share row. The owner sees per-share statistics: view count and last access time. No IP addresses or user agents are tracked.

U13.60i - **Unlimited shares per document** — no limit on the number of shares per document.

U13.60j - **Auto-revoke on delete**: when the owner deletes a document (soft or hard delete), all active shares for that document are automatically revoked (set `revoked_at`).

U13.60k - **Auto-update on rename**: when the owner renames a document, the denormalized `doc_name` is updated on all active (non-revoked) shares for that document.

U13.60l - **WOPI share token flow**: share-based WOPI tokens include a `share_access` flag and the `share_id`. The WOPI endpoints handle share tokens by reading denormalized metadata from the `DocShare` row and accessing the document file directly from disk (files are unencrypted per U13.7). No owner key is needed for share-based WOPI access.

U13.60m - **Docs list sidebar**: the document list view (`/app/docs/`) includes a collapsible left sidebar with sections:
  - "My Documents" (default) — the current document list.
  - "Shared with me" — documents shared with the current user, showing document name, type, owner email, permission badge ("Can view" / "Can edit"), and last updated. Clicking opens the document in Collabora.
  - The sidebar is collapsible on mobile, always visible on desktop. Each section shows a count badge.
  - Future sections (starred, templates) will be added to the sidebar. Folders and Tags sections are present per U13.90/U13.91.

U13.60n - **Share UI in editor**: the editor floating bar gains a "Share" button that opens a modal for managing shares. The modal shows: a form to add email addresses (comma-separated) with permission selection (view/write), a list of current shares with stats (view count, last access) and revoke buttons. The modal uses AJAX — no page navigation.

U13.60o - **Share UI in docs list**: the document list hover actions gain a "Share" button that opens the same share modal for the selected document.

U13.60p - **Public share view** (`/app/docs/s/<share_token>`): for external access, this route validates the share token, checks it is not revoked, increments `view_count`, updates `last_accessed_at`, generates a share-based WOPI token, and renders the editor template. No login required. The page shows a minimal top bar with the document name and "Shared by [owner email]".

U13.60q - **Privacy**: shared document access is logged only as aggregate counts and last-access timestamps. No IP addresses, user agents, or personally identifiable access information is stored for share recipients. Document content privacy is maintained through token-based access — only valid, non-revoked share tokens grant access.

U13.60r - **SMTP for invitations**: the docs module sends invitation emails by reading the domain's SMTP settings from the shared `Domain` model and using `smtplib` directly. No cross-module imports — the docs module reads SMTP config from `app.shared.models.core` and `app.shared.keys`, same pattern as the calendar module's iMIP sending.

## Folder Management

U13.90 - Folders are a virtual organizational layer over the (still flat) on-disk storage. A document's folder membership is stored as `folder_path` in the per-user SQLCipher `documents` table and embedded in the document metadata blob (U13.85b) so it survives a cache reset/resync. The on-disk directory layout (`data/docs/<user>/<account>/<doc_id>/`) is unchanged — folders do not create filesystem directories.

U13.90a - **Path model** — folders are represented as slash-separated path strings with no leading slash. The account root is the empty string `""`. Nested folders use `/` as the delimiter (e.g. `"Work/Projects/Alpha"`). Folder names must be non-empty, max 100 characters, and must not contain `/`, `\`, leading/trailing whitespace, or null bytes. Nesting depth is capped at 8 levels.

U13.90b - **Folder table** — a `folders` table in the per-user cache DB records `(id, account_id, path, name, created_at)` with a unique constraint on `(account_id, path)`. This table exists so that empty folders persist within a session. It is NOT the source of truth for the tree; the tree is derived from the union of `documents.folder_path` values plus the `folders` table.

U13.90c - **Resync behavior** — on resync (`resync_docs`), the `folders` table is **merged** with `SELECT DISTINCT folder_path FROM documents WHERE deleted_at IS NULL AND folder_path != ''`: folder rows inferred from recovered documents (and their ancestors) are added, while manually-created empty folders are preserved so a manual Sync never wipes the user's organization. Empty folders are only lost on a true cache **reset** (fresh/empty DB, e.g. after a cache wipe), since there is nothing to merge into. This is an accepted trade-off: documents always retain their `folder_path` via the embedded metadata, so organization is preserved; only genuinely empty folders vanish across a reset.

U13.90d - **Create folder** — `POST /app/docs/folders` (and REST/MCP equivalents) creates a folder row given a `name` and optional `parent` path. Creating a folder whose path already exists is idempotent (returns success). Parent path segments are auto-created if missing.

U13.90e - **Rename folder** — `POST /app/docs/folders/rename` updates the folder row's path/name and cascades to all contained documents and subfolders: any `documents.folder_path` (and `folders.path`) starting with the old prefix is rewritten to the new prefix. Operates as a prefix replacement so the entire subtree moves atomically.

U13.90f - **Delete folder** — `POST /app/docs/folders/delete` removes the folder row(s) for the given path and its subtree. Contained documents are NOT deleted: their `folder_path` is reset to the parent of the deleted folder (i.e. they move up one level), with an undo affordance in the UI. This matches the "delete-folder moves contents to parent" policy.

U13.90g - **Move document** — `POST /app/docs/<doc_id>/move` sets `documents.folder_path` to the target path (default root). The document's embedded metadata is re-injected so the move survives a resync.

U13.90h - **List/tree** — `GET /app/docs/folders` returns the folder tree (nested structure) for the account, computed from the `folders` table plus distinct `documents.folder_path` values. The docs list view (`GET /app/docs/`) accepts an optional `folder` query parameter to scope the visible documents to one folder (exact match on `folder_path`, not recursive).

U13.90i - **Default folder on create/upload** — the create (`POST /app/docs/new`) and upload (`POST /app/docs/upload`) routes accept an optional `folder` parameter; the new document is created with that `folder_path`. The UI passes the currently-selected folder from the sidebar/breadcrumbs.

U13.90j - **Three-layer parity** — folder CRUD, move, and tree-list are exposed via the REST API (`/api/v1/docs/folders`, `/api/v1/docs/documents/<id>/move`) and MCP (`docs_create_folder`, `docs_rename_folder`, `docs_delete_folder`, `docs_move_document`, `docs_list_folders`) alongside the web UI. The document list/detail envelopes (REST + MCP) include `folder_path`.

## Tags

U13.91 - Tags are free-form labels stored as a JSON array (`tags TEXT NOT NULL DEFAULT '[]'`) on each document in the per-user cache, and embedded in the document metadata blob (U13.85b) so they survive a cache reset/resync.

U13.91a - **Tag values** — each tag is a non-empty string, max 50 characters, stripped of surrounding whitespace. A document may have zero or more tags; duplicates are de-duplicated (case-sensitive). Tag order is not significant.

U13.91b - **Add/remove/replace tags** — `POST /app/docs/<doc_id>/tags` accepts an `add` and/or `remove` list and updates `documents.tags` (JSON), then re-injects metadata. The REST API (`PUT /api/v1/docs/documents/<id>/tags`) and MCP (`docs_update_tags`) mirror this. The REST/MCP endpoints additionally accept a `set` list which replaces the full tag list in one call (takes precedence over `add`/`remove`); the web UI uses the same `set` key. Each tag is max 50 chars.

U13.91c - **Tag list** — the sidebar derives the available tag set from `SELECT DISTINCT` over `documents.tags` (active documents only). The list is not stored as a separate registry; there is no tag rename or tag color in MVP (a tag is renamed by adding the new value and removing the old on each affected document, which the UI may batch).

U13.91d - **Filter** — the docs list view accepts an optional `tag` query parameter; the list is filtered to documents whose `tags` JSON array contains the value. Multiple tag filters are not combined in MVP (single-tag filter only). Folder and tag filters compose with name search (logical AND).

U13.91e - **Envelopes** — document list/detail responses (web JSON, REST, MCP, TS client) include a `tags` array. The list query params accept `tag`/`tags` filters.

U13.91f - **List-all-tags endpoint** — the distinct-tag query (U13.91c) is exposed beyond the web UI so agents can discover existing tags before applying them (avoids typo duplicates). The REST API (`GET /api/v1/docs/tags`) and MCP (`docs_list_tags`) return the sorted, case-insensitive list of distinct tags across the account's active documents, with three-layer parity (REST, Python MCP, TS client).

## Out of Scope for MVP

U13.70 - Real-time collaborative editing (multiple simultaneous editors).
U13.71 - Document version history.
U13.72 - Document templates.
U13.73 - Comments and annotations.
U13.74 - Offline editing.
U13.75 - (Moved to U8.8 — now implemented.)
U13.77 - Thumbnail previews in the list.
U13.78 - Share link auto-expiry.

## Additional Considerations

U13.80 - Collabora Docker container is added to the deployment alongside the app. Configured with `aliasgroup1` pointing to the LocoRoomail domain. SSL terminates at the reverse proxy; Collabora runs HTTP internally.
U13.81 - A new configuration item `COLLABORA_URL` is added to `AppConfig` (default `http://collabora:9980`) pointing to the internal Collabora endpoint.
U13.82 - A new configuration item `WOPI_JWT_SECRET` is added to `AppConfig` for signing WOPI access tokens.
U13.83 - The reverse proxy must route `/collabora/` to the Collabora container for browser iframe access.

# Use Case U14 – API Access & Token Management

## Key Architecture

U14.1 - Encryption uses a per-customer random Data Encryption Key (DEK). The DEK is a 256-bit random value generated when API access is first enabled. The DEK replaces the password-derived key as the encryption key for all of the customer's cache databases and IMAP credentials. All accounts belonging to the same customer share a single DEK.

U14.2 - The DEK is never stored in plaintext. It is stored encrypted (wrapped) with each unlock mechanism the customer has:
  - Wrapped with the credential-derived key (password) → stored as `dek_wrapped_cred` in CustomerAccount (for web login).
  - Wrapped with each API token's raw secret → stored as `wrapped_dek` in the ApiToken record (for API access).

U14.3 - Customers who do NOT enable API access continue using the current password-derived key directly (no DEK). The ephemeral, in-memory-only key model is preserved for these users. No migration is needed.

U14.4 - At web login, the system derives the key from the customer's credential (password). If API access is enabled, it unwraps the DEK from `dek_wrapped_cred` and stores the DEK in `_user_keys`. If API access is not enabled, it uses the credential-derived key directly (current behavior, unchanged).

## Opt-In Model

U14.5 - API access is disabled by default. The customer must explicitly opt in via Settings → API Access. Opting in is a privacy-conscious decision: the customer accepts that a persistent decryption capability will exist on the server for as long as API access remains enabled.

U14.6 - Enabling API access requires the customer to re-enter their IMAP password. The system:
  1. Derives the current key from the credential.
  2. Verifies it can decrypt the IMAP credential (confirms the credential is correct).
  3. Generates a random 256-bit DEK.
  4. Re-keys all of the customer's cache databases from the old credential-derived key to the new DEK.
  5. Re-encrypts the IMAP credentials with the new DEK (Fernet).
  6. Wraps the DEK with the credential-derived key → stores `dek_wrapped_cred`.
  7. Sets `api_enabled = true` on CustomerAccount.
  8. On failure, the system deletes all cache databases and the customer re-syncs from scratch on next login.

U14.7 - Disabling API access requires the customer to re-enter their IMAP password. The system:
  1. Verifies the credential.
  2. Deletes all API tokens and their wrapped DEKs.
  3. Deletes all cache databases for all of the customer's accounts.
  4. Clears `dek_wrapped_cred` and sets `api_enabled = false`.
  5. The customer is logged out and must re-login, which re-derives the credential key and re-syncs caches from scratch using the credential-derived key directly.

## Token Management

U14.8 - A customer with API access enabled can create multiple named API tokens (e.g., "Phone app", "Automation", "Backup").

U14.9 - Creating a token requires the customer to re-enter their IMAP password. The system:
  1. Derives the credential key, unwraps the DEK.
  2. Generates a random token value: prefix `lr_` + 32 random bytes encoded as URL-safe base64.
  3. Wraps the DEK with the raw token bytes (AES key wrap or Fernet).
  4. Stores the token hash (SHA-256) and wrapped DEK in the ApiToken record.
  5. Displays the raw token value to the customer exactly once with a clear warning: "Copy this token now. You will not be able to see it again."

U14.10 - Token scopes: each token has granular read/write scopes per module, selected at creation time. Available scopes: `mail:read`, `mail:write`, `contacts:read`, `contacts:write`, `calendar:read`, `calendar:write`, `docs:read`, `docs:write`. At least one scope must be selected. `:read` grants access to GET/list/search endpoints; `:write` grants access to POST/PUT/PATCH/DELETE endpoints. A token with `mail:write` must also include `mail:read` (write implies read access to the same module).

U14.11 - Revoking a single token requires the customer to re-enter their IMAP password. The token record and its wrapped DEK are deleted. Other tokens remain active.

U14.12 - Token authentication: API requests send the raw token as a Bearer token in the `Authorization` header. The server hashes the token, looks up the ApiToken record, verifies the scope covers the requested resource, unwraps the DEK from `wrapped_dek`, and loads it into a per-request context. API requests must not use the global `_user_keys` dict — they work independently of whether the customer has an active web session.

U14.13 - Token rotation: the customer creates a new token and revokes the old one. There is no automatic rotation.

U14.14 - Each token tracks `last_used_at` (updated on every authenticated API request) and `created_at`.

## Token Settings UI

U14.15 - Settings → API Access page shows:
  - Current state: enabled or disabled, with a clear explanation of the privacy implications.
  - If disabled: an "Enable API Access" button that triggers a password prompt (modal), then the enable flow (U14.6). After enabling, the customer is prompted to create their first token.
  - If enabled: list of all active tokens showing name, scopes, created date, and last used date. Actions per token: Revoke (with password confirmation). A "Create Token" button at the top. A "Disable API Access" button at the bottom (destructive, with password confirmation and a warning that all caches will be deleted).

U14.16 - The token creation flow shows a modal/form with: token name (required, max 100 chars), scope checkboxes grouped by module (each module shows read and write checkboxes — write auto-selects read), and a password confirmation field. On submit, the raw token is displayed in a copyable field with the one-time warning. A "Copy & Close" action dismisses the dialog.

## Data Model

U14.17 - ApiToken table (in main app.db):
  - `id` (PK, auto-increment)
  - `customer_id` (FK to users table)
  - `token_hash` (SHA-256 of raw token, unique index)
  - `name` (user-friendly label, max 100 chars, not null)
  - `scopes` (JSON array of scope strings, e.g. `["mail:read", "mail:write", "calendar:read"]`, not null)
  - `wrapped_dek` (blob, the DEK encrypted with the raw token bytes, not null)
  - `created_at` (UTC datetime, not null)
  - `last_used_at` (UTC datetime, nullable)

U14.18 - CustomerAccount additions (in main app.db):
  - `api_enabled` (bool, default false, not null)
  - `dek_wrapped_cred` (blob, nullable — the DEK encrypted with the credential-derived key; present only when `api_enabled = true`)

## Password Changes

U14.19 - When a customer with API access enabled changes their IMAP password:
  1. The old credential-derived key unwraps the DEK.
  2. The new credential-derived key re-wraps the DEK → updates `dek_wrapped_cred`.
  3. Existing API tokens continue to work (their wrapped DEKs are unchanged because the DEK itself has not changed).
  4. The IMAP credential is re-encrypted with the DEK (unchanged — the DEK did not change).
  5. Cache databases do not need re-keying.
  This is an improvement over the non-API mode, where password changes require cache expiry and re-sync (M6).

## Security

U14.20 - The raw token value is never stored on the server. Only the SHA-256 hash is persisted. A lost token cannot be recovered; the customer must revoke and create a new one.

U14.21 - Token values are at least 256 bits of entropy (32 random bytes), making them suitable as encryption keys for wrapping the DEK.

U14.22 - Rate limiting operates at two levels:
  - Per-IP: failed token authentication attempts are rate-limited per IP (same pattern as admin login rate limiting per M10).
  - Per-token: each token has a configurable request rate limit (requests per minute) managed by the admin. Default: 60 requests/minute. When a token exceeds its limit, the API returns `429 Too Many Requests` with a `Retry-After` header. The admin can configure per-token limits globally (default for all tokens) or per-customer.
  U14.22a - Admin UI for API rate limiting: a new setting in the admin back office to configure the default per-token rate limit (requests per minute). Future: per-customer overrides.

U14.23 - API token requests do not interact with the global `_user_keys` dict. Each API request unwraps the DEK into a request-local context and discards it after the request completes. This ensures API traffic does not interfere with session-based key management and vice versa.

U14.24 - When the customer has multiple accounts, all accounts' caches and credentials are encrypted with the same DEK. Revoking a single token removes only that token's wrapped DEK — the DEK itself and other tokens are unaffected.

## Out of Scope for MVP

U14.50 - No automatic token expiry / TTL (tokens live until explicitly revoked).
U14.53 - No token usage analytics beyond `last_used_at`.
U14.55 - No webhooks or push notifications. All data access is request/response via the REST API. Webhook subscriptions and event delivery are deferred to a future version.

# Use Case U15 – REST API

## General

U15.1 - The REST API is versioned under `/api/v1/`. All endpoints require a valid API token via `Authorization: Bearer <token>`. Token authentication and scope enforcement follow U14.12 and U14.10.

U15.2 - The API is a separate Flask Blueprint registered at `/api/v1/` with its own controllers under `app/api/`. API controllers call the same service layer functions used by the web UI controllers — no duplicate business logic. The service layer is shared; controllers are separate.

U15.3 - All API responses are JSON (`Content-Type: application/json`). Request bodies for POST/PUT/PATCH are also JSON (`Content-Type: application/json`).

U15.4 - All timestamps in API requests and responses are ISO 8601 with timezone (e.g., `2026-05-15T14:30:00Z`). The API does not perform timezone conversion — all times are UTC unless otherwise specified.

U15.5 - Multi-account support: most endpoints require an `account_id` query parameter or path parameter. The customer can list their accounts via `GET /api/v1/accounts`. If the customer has only one account, it is used by default.

## Response Format

U15.6 - Successful responses return `200 OK` (GET), `201 Created` (POST), `204 No Content` (DELETE). The response body for single resources is `{ "data": { ... } }`. For collections: `{ "data": [...], "pagination": { ... } }`.

U15.7 - Error responses use a consistent format:
```
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Human-readable description",
    "details": { ... }   // optional, e.g., field-level errors
  }
}
```
HTTP status codes: `400` (validation), `401` (invalid/missing token), `403` (insufficient scope), `404` (resource not found), `409` (conflict, e.g., duplicate), `429` (rate limited), `500` (internal error).

## Pagination

U15.8 - Collection endpoints support cursor-based pagination: `?cursor=<cursor>&limit=<int>`. Default limit is 50, max 200. The response includes `"pagination": { "next_cursor": "...", "has_more": true/false }`. Cursors are opaque strings derived from the last item's sort key.

## List Endpoints — Filtering & Sorting

U15.9 - List endpoints support common query parameters: `sort` (field name, prefix with `-` for descending), `fields` (comma-separated list of fields to include in the response, for partial responses). Each endpoint documents its supported sort fields and filters.

## Bulk Operations

U15.10 - Bulk operations accept an array of items in a single request. Endpoints follow the pattern `POST /api/v1/<module>/bulk/<action>`. Request body: `{ "items": [ ... ] }`. The response lists per-item results:
```
{
  "data": {
    "succeeded": [...],
    "failed": [{ "index": 2, "error": { ... } }]
  }
}
```
Bulk endpoints are atomic on a best-effort basis — items that succeed are committed even if later items fail. Maximum 100 items per bulk request.

## Accounts

U15.11 - `GET /api/v1/accounts` — list the customer's email accounts. Returns account ID, email address, and whether it is the default account. Scope: any (`mail:read`, `contacts:read`, `calendar:read`, or `docs:read`).

## Mail API

U15.20 - All mail endpoints require at least `mail:read`. Write operations require `mail:write`. All endpoints require `account_id` parameter.

U15.21 - `GET /api/v1/mail/folders` — list folders with unread counts.
U15.22 - `GET /api/v1/mail/folders/{folder_id}/messages` — list messages in a folder. Supports `?unread=true`, `?flagged=true`, `?since=<iso8601>` filters. Returns message ID, subject, from, to, date, flags, snippet, thread_id.
U15.23 - `GET /api/v1/mail/messages/{message_id}` — full message: subject, from, to, cc, bcc, date, flags, body (plain text and/or HTML), attachments list (filename, size, content-type). Body content is the cached version (same as web UI).
U15.24 - `GET /api/v1/mail/messages/{message_id}/attachments/{attachment_id}` — download a single attachment.
U15.25 - `GET /api/v1/mail/threads/{thread_id}` — list all messages in a thread.
U15.26 - `POST /api/v1/mail/messages` — send a new email. Body: `{ "to": [...], "cc": [...], "bcc": [...], "subject": "...", "body_plain": "...", "body_html": "...", "attachments": [...] }`. Attachments are uploaded separately via `POST /api/v1/mail/attachments` first, then referenced by ID. Follows the same undo-send pattern as the web UI (U6.6) with a default 5-second grace period. The response includes a `send_id` that can be used to cancel within the grace period.
U15.27 - `POST /api/v1/mail/attachments` — upload an attachment for use in compose. Returns an attachment ID. Supports `Content-Type: multipart/form-data`. Max size per attachment follows SMTP limits (no app-level limit per U6.2).
U15.28 - `PATCH /api/v1/mail/messages/{message_id}` — update message flags: `{ "flags": { "read": true, "flagged": false } }`.
U15.29 - `POST /api/v1/mail/messages/{message_id}/move` — move message to another folder: `{ "folder_id": "..." }`.
U15.30 - `DELETE /api/v1/mail/messages/{message_id}` — move message to Trash (same behavior as web UI U5.9).
U15.31 - `POST /api/v1/mail/bulk/flag` — bulk flag/unflag messages: `{ "items": [{ "message_id": "...", "flags": { "read": true } }] }`.
U15.32 - `POST /api/v1/mail/bulk/move` — bulk move messages: `{ "items": [{ "message_id": "...", "folder_id": "..." }] }`.
U15.33 - `POST /api/v1/mail/bulk/delete` — bulk delete messages (move to Trash): `{ "items": [{ "message_id": "..." }] }`.
U15.34 - `GET /api/v1/mail/search` — search messages. Parameters: `q` (query string), `folder_id` (optional, restrict to folder), `unread` (optional), `flagged` (optional), `since` / `until` (optional date range). Returns same format as message list. Uses the same search backend as the web UI (local cache + IMAP expansion per U7.1).
U15.35 - `GET /api/v1/mail/folders/{folder_id}/messages/{message_id}/raw` — download the raw RFC 822 message source (.eml format).

## Contacts API

U15.40 - All contacts endpoints require at least `contacts:read`. Write operations require `contacts:write`. All endpoints require `account_id` parameter.

U15.41 - `GET /api/v1/contacts` — list contacts. Supports `?q=<search>`, `?sort=name|email`. Returns contact ID, name, emails, phones, organization, title.
U15.42 - `GET /api/v1/contacts/{contact_id}` — full contact detail including all supported vCard fields (U11.7) and raw vCard data.
U15.43 - `POST /api/v1/contacts` — create a contact. Body: `{ "fn": "...", "email_work": "...", "email_home": "...", "phone_work": "...", "phone_cell": "...", "organization": "...", "title": "...", "note": "..." }`.
U15.44 - `PUT /api/v1/contacts/{contact_id}` — update a contact. Same fields as create.
U15.45 - `DELETE /api/v1/contacts/{contact_id}` — delete a contact (via CardDAV DELETE + cache removal).
U15.46 - `POST /api/v1/contacts/bulk/delete` — bulk delete contacts: `{ "items": [{ "contact_id": "..." }] }`.
U15.47 - `GET /api/v1/contacts/search` — search contacts by name, email, or phone. Parameter: `q`. Returns name + email pairs (same as autocomplete API U11.13 but with full contact data).

## Calendar API

U15.50 - All calendar endpoints require at least `calendar:read`. Write operations require `calendar:write`. All endpoints require `account_id` parameter.

U15.51 - `GET /api/v1/calendar/calendars` — list calendars with name, color, is_default.
U15.52 - `POST /api/v1/calendar/calendars` — create a calendar: `{ "name": "...", "color": "#hex" }`.
U15.53 - `PUT /api/v1/calendar/calendars/{calendar_id}` — update calendar name/color.
U15.54 - `DELETE /api/v1/calendar/calendars/{calendar_id}` — delete a calendar and all its events. Requires confirmation: `{ "confirm": true }`.
U15.55 - `GET /api/v1/calendar/calendars/{calendar_id}/events` — list events. Supports `?since=<iso8601>`, `?until=<iso8601>`, `?search=<query>`. Returns event ID, summary, start, end, location, is_all_day, status, recurrence rule summary.
U15.56 - `GET /api/v1/calendar/events/{event_id}` — full event detail: summary, description, location, start/end (with timezone), is_all_day, calendar, recurrence rule, attendees, reminders, categories, status, created, last_modified.
U15.57 - `POST /api/v1/calendar/events` — create an event. Body includes: summary, description, location, start, end (ISO 8601 with timezone), calendar_id, is_all_day, attendees (array of `{email, name?, role?}`), reminders (array of `{type, trigger_minutes}`), recurrence (RRULE string). The server converts start/end times to iCalendar DTSTART/DTEND with TZID. Attendee invitation emails (iMIP) follow U12.25 behavior.
U15.58 - `PUT /api/v1/calendar/events/{event_id}` — update an event. Same fields as create. For recurring events, body must include `scope`: `"instance"` (RECURRENCE-ID), `"future"`, or `"series"` per U12.23.
U15.59 - `DELETE /api/v1/calendar/events/{event_id}` — delete an event. For recurring events, requires `scope` parameter same as update. Returns the deleted event data for undo.
U15.60 - `GET /api/v1/calendar/search` — search events by summary. Parameter: `q`, `since`, `until`. Returns same format as event list.
U15.61 - `GET /api/v1/calendar/free-busy` — free/busy check. Body: `{ "calendar_ids": [...], "start": "...", "end": "..." }`. Returns busy time ranges. Uses cached events (same as U12.29).

## Docs API

U15.70 - All docs endpoints require at least `docs:read`. Write operations require `docs:write`. All endpoints require `account_id` parameter.

U15.71 - `GET /api/v1/docs/documents` — list documents. Supports `?type=odt|ods|odp`, `?search=<name>`. Returns document ID, name, type, size, created_at, updated_at.
U15.72 - `GET /api/v1/docs/documents/{document_id}` — document metadata: name, type, size, created_at, updated_at.
U15.73 - `POST /api/v1/docs/documents` — create a new document. Body: `{ "name": "...", "type": "odt|ods|odp" }`. Creates an empty ODF template and returns the document metadata.
U15.74 - `PUT /api/v1/docs/documents/{document_id}` — rename a document: `{ "name": "..." }`.
U15.75 - `DELETE /api/v1/docs/documents/{document_id}` — soft-delete a document (moves to trash per U13.27).
U15.76 - `GET /api/v1/docs/documents/{document_id}/download` — download the document file in ODF format.
U15.77 - `GET /api/v1/docs/documents/{document_id}/download/pdf` — export the document as PDF via Collabora conversion.
U15.78 - `POST /api/v1/docs/documents/upload` — upload a document. `Content-Type: multipart/form-data` with file field. Accepted types: `.odt`, `.ods`, `.odp`, `.docx`, `.xlsx`, `.pptx`. Non-ODF files are converted to ODF via Collabora. Returns document metadata. Max size: 50 MB (U13.34).
U15.79 - `PUT /api/v1/docs/documents/{document_id}/content` — replace the document's file content. Two input modes:
  - File upload: `Content-Type: multipart/form-data` with file field. Accepted types: `.odt`, `.ods`, `.odp`, `.docx`, `.xlsx`, `.pptx`, `.txt`, `.html`. Non-ODF files are converted to ODF server-side.
  - Markdown: `Content-Type: application/json` with `{ "content": "...", "format": "markdown" }`. Markdown is converted to ODF server-side. This is the primary mode for AI agents.
  The existing document file is overwritten; metadata (name, type) is preserved. Max size: 50 MB (U13.34). Returns updated document metadata.
U15.79a - Collabora is never exposed publicly for API consumers. All document conversion (upload, content replacement, PDF export, markdown conversion) is performed server-side by the Flask app calling Collabora's internal REST conversion endpoint. Collabora remains accessible only via the reverse proxy for browser-based WYSIWYG editing in the web UI.

  - U15.80 - `GET /api/v1/docs/documents/{document_id}/content` — retrieve the document content as text. Supports `?format=text` (default, plain text) or `?format=markdown` (best-effort markdown extraction from ODF). Markdown format is the primary mode for AI agents. Returns `{ "data": { "content": "...", "format": "text|markdown" } }`. Best-effort extraction; complex formatting, embedded objects, and tables may be approximated or lost.

## Document Drafts (AI Workflow)

U15.81 - `POST /api/v1/docs/documents/{document_id}/drafts` — create a draft copy of a document. The draft is a separate document linked to the original via metadata (`source_document_id`, `draft_purpose: "ai-modification"`). The draft's name defaults to the original name + " (AI Draft)". The request body includes the modified content in markdown: `{ "content": "...", "format": "markdown", "summary": "Brief description of changes" }`. Returns the draft document metadata.
U15.82 - `GET /api/v1/docs/documents/{document_id}/drafts` — list drafts for a document. Returns draft ID, name, summary, created_at.
U15.83 - `POST /api/v1/docs/documents/{document_id}/drafts/{draft_id}/apply` — apply (accept) a draft: the draft's file replaces the original document's file, and the draft is deleted. The original document's metadata is preserved. Scope: `docs:write`.
U15.84 - `DELETE /api/v1/docs/documents/{document_id}/drafts/{draft_id}` — discard a draft: deletes the draft document and its file. No effect on the original. Scope: `docs:write`.
U15.85 - In the web UI, documents that have drafts show a notification banner: "AI modifications available" with actions "Review changes" (opens the draft in Collabora for side-by-side comparison) and "Discard". After reviewing, the user can "Accept changes" (apply draft) or "Discard changes" (delete draft).
U15.86 - Drafts are regular documents with additional metadata fields (`source_document_id`, `draft_purpose`, `change_summary`). They appear in a "Drafts" section at the bottom of the document list, visually distinguished from regular documents. Drafts are excluded from the main document list by default (shown only when their source document is viewed or in the dedicated drafts section).

## Async Operations

U15.90 - Long-running operations (bulk delete, document conversion, large search expansion) return `202 Accepted` with an operation resource: `{ "data": { "operation_id": "...", "status": "pending", "poll_url": "/api/v1/operations/{id}" } }`. The client polls the `poll_url` until status is `completed` or `failed`. Completed operations include the result in the response.

## OpenAPI Specification

U15.91 - An OpenAPI 3.1 specification is generated and served at `/api/v1/openapi.json`. This enables non-MCP AI tools (ChatGPT, OpenAI function calling) to discover available endpoints. The spec is auto-generated from the API controller definitions.

## Out of Scope for MVP
 
U15.50 - No webhooks or push notifications (s
ee U14.55).
U15.51 - No cross-module unified search.
U15.52 - No real-time updates (SSE/WebSocket) for API consumers.
U15.53 - No API access for admin/manager roles (customer API tokens only).

# Use Case U16 – MCP Package (locoroosuite-mcp)

## Package Overview

U16.1 - `locoroosuite-mcp` is a standalone npm package distributed via npm. It implements an MCP server (Model Context Protocol) that translates MCP tool calls into LocoRoomail REST API (`/api/v1/`) requests. The package is a thin adapter — no business logic.

U16.2 - Transport: stdio (standard MCP stdio transport). The package is run as a local process by any MCP-capable agent — coding assistants (Claude Desktop, Cursor, Claude Code, opencode), personal assistants (Goose, Hermes), local LLM UIs (LM Studio, AnythingLLM), or automation tools (n8n). Per-client setup is documented in the package README; agents that only speak remote HTTP/SSE MCP (ChatGPT, v0, Replit) cannot use this package and are served by the Python MCP server (U17) instead.

U16.3 - The package is published as `locoroosuite-mcp` on npm. Users install and configure it in their AI client settings (e.g., Claude Desktop `claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "locoroosuite": {
      "command": "npx",
      "args": ["-y", "locoroosuite-mcp", "--api-url=https://your-server.com", "--token=lr_..."]
    }
  }
}
```

## Configuration

U16.4 - The package accepts configuration via command-line arguments or environment variables:
  - `--api-url` / `LOCOROO_API_URL` (required): base URL of the LocoRoomail instance (e.g., `https://mail.example.com`).
  - `--token` / `LOCOROO_API_TOKEN` (required): API token value (the `lr_...` string).
  - `--account-id` / `LOCOROO_ACCOUNT_ID` (optional): default account ID if the user has multiple accounts. If not set, the first account is used automatically.

U16.5 - On startup, the package validates the token by calling `GET /api/v1/accounts`. If the token is invalid or the API is unreachable, it logs a warning to stderr but starts the MCP server anyway. Individual tool calls will surface authentication errors through the MCP protocol, allowing the AI client to display a clear error message (e.g., "Invalid or expired token"). This ensures the client never sees an opaque connection-closed error.

## Tool Definitions

U16.6 - The MCP server exposes tools grouped by module. Each tool maps to one or more REST API endpoints. Tool names are prefixed with the module name to avoid collisions.

U16.7 - Mail tools (require `mail:read` and/or `mail:write`):
  - `mail_list_folders` — list email folders.
  - `mail_list_messages` — list messages in a folder (with pagination).
  - `mail_get_message` — get full message content including body.
  - `mail_search` — search messages.
  - `mail_send` — compose and send a new email.
  - `mail_move_message` — move a message to a different folder.
  - `mail_delete_message` — move a message to Trash.
  - `mail_update_flags` — update read/flagged status.
  - `mail_get_attachment` — download an attachment.
  - `mail_get_thread` — get all messages in a conversation thread.
  - `mail_bulk_move` — move multiple messages.
  - `mail_bulk_delete` — delete multiple messages.
  - `mail_bulk_flag` — update flags on multiple messages.

U16.8 - Contacts tools (require `contacts:read` and/or `contacts:write`):
  - `contacts_list` — list contacts with optional search.
  - `contacts_get` — get full contact detail.
  - `contacts_search` — search contacts by name, email, or phone.
  - `contacts_create` — create a new contact.
  - `contacts_update` — update an existing contact.
  - `contacts_delete` — delete a contact.
  - `contacts_bulk_delete` — delete multiple contacts.

U16.9 - Calendar tools (require `calendar:read` and/or `calendar:write`):
  - `calendar_list_calendars` — list available calendars.
  - `calendar_create_calendar` — create a new calendar.
  - `calendar_update_calendar` — update calendar name/color.
  - `calendar_delete_calendar` — delete a calendar.
  - `calendar_list_events` — list events in a calendar with date range.
  - `calendar_get_event` — get full event detail.
  - `calendar_search_events` — search events by summary.
  - `calendar_create_event` — create a new event.
  - `calendar_update_event` — update an event.
  - `calendar_delete_event` — delete an event.
  - `calendar_check_free_busy` — check free/busy time ranges.

U16.10 - Docs tools (require `docs:read` and/or `docs:write`):
  - `docs_list_documents` — list documents.
  - `docs_get_document` — get document metadata.
  - `docs_create_document` — create a new document (blank or with initial markdown content).
  - `docs_rename_document` — rename a document.
  - `docs_delete_document` — delete a document.
  - `docs_download_document` — download document file (ODF format).
  - `docs_upload_document` — upload a file as a new document.
  - `docs_read_content` — read document content as plain text or markdown.
  - `docs_update_content` — replace document content (file upload or markdown).
  - `docs_create_draft` — create a draft copy with AI-modified content (markdown). The user reviews and accepts/discards in the web UI.
  - `docs_list_drafts` — list pending drafts for a document.
  - `docs_apply_draft` — accept a draft, replacing the original.
  - `docs_discard_draft` — discard a draft.
  - `docs_export_pdf` — export document as PDF.

## Tool Behavior

U16.11 - Each tool validates that the token has the required scope before making the API call. If the scope is insufficient, the tool returns an MCP error: "This action requires the `{scope}` permission. Please create a new API token with this scope."

U16.12 - The package handles pagination automatically for list tools. When an AI agent calls `mail_list_messages`, the package fetches the first page and returns it. If the agent needs more results, it calls the tool again with the `cursor` parameter from the previous response.

U16.13 - Errors from the REST API are translated into MCP error responses with the original error code and message. HTTP 429 responses include the retry-after hint.

U16.14 - The package is stateless — it does not cache data between tool calls. Each tool call makes a fresh API request.

## Versioning & Compatibility

U16.15 - The package version tracks with the REST API version (v1). When the REST API introduces breaking changes (v2), the package major version is bumped. The package's npm version uses semver: major version matches the API major version (e.g., `1.x.y` for API v1).

U16.16 - The package declares its MCP protocol version and tool schema on startup. The MCP protocol version is independent of the REST API version.

## Out of Scope for MVP

U16.50 - No SSE transport (stdio only). Note: HTTP/SSE transport is provided by the Python MCP server (U17).
U16.51 - No hosted/embedded MCP server in the Flask app. Note: Superseded by U17 (Python MCP Server).
U16.52 - No tool-level caching or result deduplication.
U16.53 - No streaming responses for large result sets.
U16.54 - No built-in OAuth2 or interactive login — token must be pre-created in the web UI. Note: OAuth 2.1 is provided by U18 for the Python MCP server (U17).

# Use Case U17 – Python MCP Server (HTTP/SSE)

## Overview

U17.1 - The Python MCP server is an ASGI application that provides remote MCP access over HTTP/SSE transport. It enables cloud-hosted AI clients (ChatGPT, and future MCP-compatible tools) to connect to LocoRoomail without a local process. The TypeScript stdio package (U16) continues to serve local clients (Claude Desktop, opencode, Codex).

U17.2 - The Python MCP server is NOT embedded in the Flask WSGI app. It runs as a separate ASGI process (uvicorn) alongside gunicorn on the same server, sharing the same codebase (models, services, database). Nginx routes `/mcp` to uvicorn and everything else to gunicorn.

U17.3 - The Python MCP server calls the shared service layer directly — it does NOT make HTTP requests to the REST API (`/api/v1/`). This eliminates the network hop and avoids duplicating business logic.

## Architecture

U17.4 - Deployment topology (single server):
```
nginx (port 443, TLS termination)
  ├── /             → gunicorn (Flask WSGI)   port 8000
  ├── /api/v1/      → gunicorn (Flask WSGI)   port 8000
  ├── /oauth/       → gunicorn (Flask WSGI)   port 8000
  ├── /.well-known/ → gunicorn (Flask WSGI)   port 8000
  └── /mcp          → uvicorn   (MCP ASGI)    port 8001
```

U17.5 - Both processes import from the same codebase. The MCP ASGI app initializes the Flask app context (for database access, configuration) at startup, then runs independently.

U17.6 - The MCP ASGI app uses the `mcp` Python SDK (`pip install mcp`) with `StreamableHTTPServerTransport` mounted at `/mcp`.

## Authentication

U17.7 - The Python MCP server supports two authentication methods:
  - **OAuth 2.1 JWT tokens** (primary, for ChatGPT): verified via JWKS, issuer, audience, expiry, and scopes. See U18.
  - **API key tokens** (`lr_...`, fallback for other remote MCP clients): verified the same way as the REST API (U14.12). The MCP server extracts the `Authorization: Bearer lr_...` header, hashes it, looks up the ApiToken, and unwraps the DEK.

U17.8 - Token verification on each MCP request:
  1. Extract `Authorization: Bearer <token>` from the HTTP request headers.
  2. Determine token type: if it starts with `lr_`, treat as API key; otherwise, treat as JWT.
  3. For API keys: hash (SHA-256), look up ApiToken, verify scope, unwrap DEK (same as U14.12).
  4. For JWTs: verify signature against JWKS, verify `iss` (this server), verify `aud` (the MCP server URL), verify `exp`/`nbf`, extract scopes from `scope` claim, resolve customer identity from `sub` claim.
  5. On failure: return MCP error with `_meta["mcp/www_authenticate"]` to trigger the client's OAuth UI.

U17.9 - The resolved identity (customer ID, account ID, scopes, DEK) is stored in a per-request context object, similar to `g.api_context` in the REST API.

## Tool Definitions

U17.10 - The Python MCP server exposes the same tools as the TypeScript package (U16.6-U16.10), with identical names, input schemas, and behavior. Tool definitions are implemented in `app/mcp/tools/` with one file per module.

U17.11 - Each tool declares `securitySchemes` per the MCP authorization spec:
  - Read-only tools (e.g., `mail_list_folders`, `contacts_search`, `calendar_list_events`): `{ type: "oauth2", scopes: ["mail:read"] }` and optionally `{ type: "noauth" }` for mixed-mode tools.
  - Write tools (e.g., `mail_send`, `contacts_create`, `calendar_create_event`): `{ type: "oauth2", scopes: ["mail:write"] }`.
  - Scopes map directly to the existing scope system (U14.10): `mail:read`, `mail:write`, `contacts:read`, `contacts:write`, `calendar:read`, `calendar:write`, `docs:read`, `docs:write`.

U17.12 - Each tool includes MCP tool annotations (`readOnlyHint`, `openWorldHint`, `destructiveHint`) per the MCP spec:
  - All read/list/search/get tools: `readOnlyHint: true`, `openWorldHint: false`, `destructiveHint: false`.
  - Create/update tools: `readOnlyHint: false`, `openWorldHint: false`, `destructiveHint: false`.
  - Delete tools: `readOnlyHint: false`, `openWorldHint: false`, `destructiveHint: true`.

U17.13 - Tool handlers receive the resolved identity (U17.9) and call the shared service layer directly. Error handling mirrors the REST API pattern: validation errors, permission errors, and not-found errors are translated into MCP error responses.

## Tool Behavior

U17.14 - The Python MCP server is stateless between tool calls. Each call independently resolves the identity and makes fresh service-layer calls. No session state is carried between calls.

U17.15 - Pagination follows the same cursor-based pattern as the REST API (U15.8). The tool handler passes the `cursor` and `limit` parameters through to the service layer.

U17.16 - Errors from the service layer are translated into MCP error responses. The mapping preserves the original error category (validation, permission, not-found, rate-limit, internal).

U17.17 - When a tool call fails due to missing or invalid authentication, the response includes `_meta["mcp/www_authenticate"]` with a `WWW-Authenticate` challenge pointing to the protected resource metadata (U18.4). This triggers ChatGPT's OAuth linking UI.

## Protected Resource Metadata

U17.18 - The MCP server exposes protected resource metadata at:
  `GET https://{host}/.well-known/oauth-protected-resource`
  This endpoint is served by the Flask app (not the MCP ASGI process), since it shares the same domain.

U17.19 - The metadata document follows RFC 9728:
```json
{
  "resource": "https://{host}/mcp",
  "authorization_servers": ["https://{host}"],
  "scopes_supported": [
    "mail:read", "mail:write",
    "contacts:read", "contacts:write",
    "calendar:read", "calendar:write",
    "docs:read", "docs:write"
  ]
}
```

U17.20 - Unauthenticated MCP requests receive `401 Unauthorized` with a `WWW-Authenticate` header:
```
WWW-Authenticate: Bearer resource_metadata="https://{host}/.well-known/oauth-protected-resource"
```

## Out of Scope

U17.50 - No stdio transport (the TypeScript package handles this, see U16).
U17.51 - No widget/UI templates or ChatGPT-specific UI features (Apps SDK components). Future iteration.
U17.52 - No tool-level caching or result deduplication (matches U16.52).
U17.53 - No SSE subscription or real-time push to MCP clients.

# Use Case U18 – OAuth 2.1 Authorization Server

## Overview

U18.1 - LocoRoomail acts as its own OAuth 2.1 authorization server to support the MCP authorization spec (https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization). This enables ChatGPT and other MCP clients to authenticate users via the standard OAuth authorization-code flow with PKCE.

U18.2 - The OAuth server is implemented as Flask endpoints (under `app/shared/oauth.py` and registered as Flask routes), running on the same gunicorn process as the web UI. It reuses the existing Customer login flow (IMAP password) for user authentication during the OAuth consent step.

U18.3 - Token format: JWT access tokens signed with RS256 (asymmetric). The signing key pair is generated at setup and stored on disk. The public key is served via JWKS endpoint for client verification.

## Endpoints

U18.4 - Authorization server metadata (RFC 8414):
  `GET https://{host}/.well-known/oauth-authorization-server`
```json
{
  "issuer": "https://{host}",
  "authorization_endpoint": "https://{host}/oauth/authorize",
  "token_endpoint": "https://{host}/oauth/token",
  "registration_endpoint": "https://{host}/oauth/register",
  "jwks_uri": "https://{host}/oauth/jwks.json",
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["none"],
  "client_id_metadata_document_supported": true,
  "scopes_supported": [
    "mail:read", "mail:write",
    "contacts:read", "contacts:write",
    "calendar:read", "calendar:write",
    "docs:read", "docs:write"
  ],
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code"]
}
```

U18.5 - Protected resource metadata (RFC 9728):
  `GET https://{host}/.well-known/oauth-protected-resource`
  (See U17.19 for the document structure.)

U18.6 - Authorization endpoint: `GET /oauth/authorize`
  - Validates `client_id`, `redirect_uri`, `response_type=code`, `code_challenge`, `code_challenge_method=S256`, `resource` parameter, and requested `scope`.
  - If the user does not have an active web session, redirects to the existing login page. After login, returns to the OAuth consent screen.
  - The consent screen shows: the client name (from registration), requested scopes with human-readable descriptions, and approve/deny buttons.
  - On approval: generates an authorization code (cryptographically random, single-use, expires in 10 minutes, bound to `code_challenge` and `redirect_uri`), stores it, and redirects to `redirect_uri?code={code}&state={state}`.
  - The `resource` parameter is echoed into the access token's `aud` claim.

U18.7 - Token endpoint: `POST /oauth/token`
  - Accepts `grant_type=authorization_code`, `code`, `redirect_uri`, `code_verifier` (PKCE), and `client_id`.
  - Validates the authorization code: exists, not expired, not reused (single-use), `code_verifier` matches the stored `code_challenge` (S256).
  - On success: issues a JWT access token (RS256-signed) with claims: `iss`, `sub` (customer ID), `aud` (the `resource` value from the authorization request), `scope`, `iat`, `exp` (1 hour lifetime), `jti` (unique token ID for revocation).
  - Response: `{ "access_token": "...", "token_type": "Bearer", "expires_in": 3600, "scope": "..." }`.
  - No refresh tokens for MVP (U18.50). When the access token expires, the client re-runs the authorization flow.

U18.8 - Dynamic Client Registration (DCR): `POST /oauth/register`
  - Accepts: `client_name`, `redirect_uris` (array), `token_endpoint_auth_method` (must be `none` for public clients).
  - Validates that `redirect_uris` are HTTPS and match the expected ChatGPT redirect pattern (`https://chatgpt.com/connector/oauth/{callback_id}` or the legacy `https://chatgpt.com/connector_platform_oauth_redirect`).
  - Generates a unique `client_id` and `client_secret` (optional, not used for public clients).
  - Stores the client registration in the `OAuthClient` database model.
  - Response follows RFC 7591.

U18.9 - JWKS endpoint: `GET /oauth/jwks.json`
  - Serves the public signing key(s) in JWK Set format for JWT verification by MCP clients.
  - The key pair (RS256) is stored on disk at a configurable path. Key rotation is manual for MVP.

## OAuth Flow (End-to-End)

U18.10 - The complete flow for a ChatGPT user connecting LocoRoomail:
  1. User adds LocoRoomail as a ChatGPT connector/app, providing the MCP server URL (`https://{host}/mcp`).
  2. ChatGPT fetches `/.well-known/oauth-protected-resource` to discover the authorization server.
  3. ChatGPT fetches `/.well-known/oauth-authorization-server` to get the OAuth endpoints.
  4. ChatGPT registers as an OAuth client via DCR (`/oauth/register`) — once per connector instance.
  5. When the user first invokes a tool, ChatGPT redirects to `/oauth/authorize` with PKCE parameters.
  6. User authenticates (locoroomail login page if no session) and sees the consent screen.
  7. User approves. Authorization code is issued; ChatGPT exchanges it for a JWT access token.
  8. ChatGPT attaches `Authorization: Bearer <jwt>` to all subsequent MCP requests.
  9. The MCP server verifies the JWT on each request (U17.8).

## Data Model

U18.11 - OAuthClient table (in main app.db):
  - `id` (PK, auto-increment)
  - `client_id` (unique string, generated at registration)
  - `client_name` (display name, e.g., "ChatGPT Connector")
  - `redirect_uris` (JSON array of allowed redirect URIs)
  - `token_endpoint_auth_method` (string, e.g., `none`)
  - `created_at` (UTC datetime, not null)

U18.12 - OAuthAuthorizationCode table (in main app.db):
  - `id` (PK, auto-increment)
  - `code` (hashed authorization code, unique)
  - `client_id` (FK to OAuthClient)
  - `customer_id` (FK to users table)
  - `redirect_uri` (string)
  - `scope` (string, space-separated)
  - `resource` (string, the MCP server URL)
  - `code_challenge` (string, S256 hash)
  - `code_challenge_method` (string, `S256`)
  - `expires_at` (UTC datetime, not null)
  - `used` (bool, default false — single-use enforcement)

U18.13 - OAuthAccessToken table (in main app.db, for audit/revocation):
  - `id` (PK, auto-increment)
  - `jti` (unique string, JWT ID)
  - `client_id` (FK to OAuthClient)
  - `customer_id` (FK to users table)
  - `scope` (string)
  - `resource` (string)
  - `expires_at` (UTC datetime)
  - `revoked` (bool, default false)
  - `created_at` (UTC datetime)

## Security

U18.14 - Authorization codes are single-use. On exchange, the code is marked as `used=true`. Reuse attempts revoke all tokens issued from that code.

U18.15 - PKCE is mandatory. The authorization endpoint rejects requests without `code_challenge` and `code_challenge_method=S256`.

U18.16 - The JWT signing key (RS256 private key) is stored on disk with restricted file permissions (owner-read-only). It is never committed to version control. Key rotation requires updating the key file and restarting the server; old tokens remain valid until expiry.

U18.17 - JWT access tokens are short-lived (1 hour). No refresh tokens for MVP (U18.50). Clients re-authorize when the token expires.

U18.18 - The `resource` parameter is mandatory in authorization requests and is echoed into the JWT `aud` claim. Tokens with the wrong audience are rejected by the MCP server.

U18.19 - Redirect URI validation: only pre-registered redirect URIs (from the OAuthClient record) are accepted. Wildcard patterns are not supported.

U18.20 - The consent screen clearly shows which scopes are being requested and what they mean (e.g., "mail:read — Read your email messages and folders"). The user must explicitly approve.

U18.21 - OAuth-related audit log entries (added to M9a scope):
  - OAuth client registration (client name, redirect URIs)
  - Authorization code issued (client ID, customer ID, scopes)
  - Token issued (client ID, customer ID, scopes)
  - Token revoked
  - Failed authorization attempts (invalid client, invalid redirect URI, PKCE failure)

## Relationship to Existing Auth

U18.22 - OAuth access tokens and API key tokens (`lr_...`) coexist. Both grant access to the same MCP tools and REST API endpoints. The token type is distinguished at verification time (JWT vs opaque `lr_` prefix).

U18.23 - OAuth tokens identify the customer via the JWT `sub` claim (customer ID). The MCP server resolves the customer's accounts and DEK the same way as API key auth, using the customer ID to look up the CustomerAccount and `dek_wrapped_cred`.

U18.24 - Customers who have not enabled API access (U14.5) cannot use OAuth-scoped tools that require DEK access (mail, contacts, calendar) because the DEK does not exist. The MCP server returns a clear error: "API access must be enabled in Settings before connecting external apps."

## Out of Scope

U18.50 - No refresh tokens (clients re-authorize when access tokens expire).
U18.51 - No OpenID Connect (ID tokens, userinfo endpoint). OAuth 2.1 authorization only.
U18.52 - No client credentials grant or machine-to-machine auth.
U18.53 - No automated JWT signing key rotation (manual for MVP).
U18.54 - No scope-specific consent persistence (user consents each time).
U18.55 - No OAuth token revocation endpoint (`/oauth/revoke`) for MVP. Tokens expire naturally. Revocation tracking via the `revoked` flag exists for future use.

# Risks & Open Questions (API / MCP / Docs)

R15.1 - **Markdown ↔ ODF conversion quality is unproven.** Collabora's internal REST API supports some format conversion, but markdown-to-ODF fidelity is not well-documented. Complex documents (tables, images, embedded objects, multi-column layouts) will not survive the round-trip. If Collabora's conversion is insufficient, an additional dependency (e.g., pandoc) may be needed. This must be validated with a proof-of-concept before committing to the markdown content API (U15.79, U15.80).

R15.2 - **Document drafts add versioning complexity to a module that currently has none.** U13.71 explicitly scopes out document version history. The draft mechanism (U15.81-U15.86) introduces a lightweight versioning concept (`source_document_id`, `draft_purpose`) that could evolve into a full versioning system. There is a risk of scope creep. The draft model should remain deliberately minimal — a draft is a separate document with metadata linking it to the original, not a full revision history.

R15.3 - **Draft review UX depends on Collabora's comparison capabilities.** The "Review changes" workflow (U15.85) assumes the user can visually compare the original and the draft. Collabora does not have a built-in "track changes" or "diff view" mode accessible via a simple URL parameter. The likely UX is the user opens both documents in separate Collabora tabs. This is functional but not as polished as Google Docs' "Suggest edits" mode. A proper diff/comparison view would require either Collabora extensions or a custom diff UI, both of which are out of scope for MVP.

R15.4 - **AI agents editing documents is the weakest use case.** The REST API and MCP integration will deliver the most value for mail (read, search, send, organize), contacts (CRUD), and calendar (CRUD, free/busy, search). These are structured data with well-defined schemas. Document editing via AI is inherently lossy due to the rich-text → text → rich-text round-trip. Product messaging should set expectations accordingly: AI agents are great at reading, summarizing, and creating documents from scratch; in-place editing of existing rich documents is best-effort.

R15.5 - **The MCP package (`locoroosuite-mcp`) lives in a separate ecosystem (npm/TypeScript) from the main application (Python/Flask).** Schema changes to the REST API must be coordinated across two codebases. When a new endpoint is added or a response field changes, both the Flask API controllers and the MCP tool definitions must be updated in lockstep. The package version must track the API version (U16.15). There is no automated mechanism for this — it requires process discipline. Consider generating MCP tool definitions from the OpenAPI spec (U15.91) in a future iteration.

R15.6 - **Per-token rate limiting (U14.22) requires a backend store.** For a single-server deployment, an in-memory rate limiter (per-process dict) is sufficient. If the application scales to multiple processes or servers, a shared store (Redis, database-backed) is needed. This is acceptable for MVP (single-server) but must be revisited if the deployment model changes.

R15.7 - **MCP protocol stability.** The Model Context Protocol is evolving rapidly. The stdio transport is the most stable, but tool schema conventions, error handling, and client compatibility may change. The `locoroosuite-mcp` package pins to a specific MCP SDK version and should be tested against target AI clients (Claude Desktop, Cursor) before release. Breaking changes in the MCP SDK may require package updates independent of the REST API.

R17.1 - **ASGI/WSGI coexistence adds deployment complexity.** Running uvicorn (ASGI) alongside gunicorn (WSGI) requires careful nginx configuration and process management. Both processes must share the same database and codebase. If one process crashes or becomes unresponsive, the other must continue operating independently. Health checks and process supervision (systemd) must cover both processes.

R17.2 - **Two MCP codebases (TypeScript + Python) must stay in sync.** Tool definitions exist in both `packages/locoroosuite-mcp/src/tools/` (TypeScript, calling REST API) and `app/mcp/tools/` (Python, calling service layer directly). Adding or changing a tool requires updating both. The TypeScript package is insulated by the REST API contract, but behavioral differences (error messages, pagination handling, field names) could diverge over time. Consider generating tool definitions from a shared schema in a future iteration.

R17.3 - **MCP Python SDK maturity.** The `mcp` Python SDK is newer than the TypeScript SDK. HTTP transport support, streaming, and error handling may be less battle-tested. The implementation should pin to a specific SDK version and test thoroughly with the MCP Inspector before production deployment.

R18.1 - **Self-hosted OAuth 2.1 is security-sensitive.** Implementing an authorization server correctly (PKCE, authorization code handling, token signing, redirect URI validation) requires careful attention to OAuth security best practices (RFC 6749, RFC 7636, RFC 9728). Bugs in the OAuth flow can expose user data or allow token theft. The implementation should use `authlib` (a mature Python OAuth library) rather than implementing crypto and protocol details from scratch. Consider a security review of the OAuth implementation before enabling it in production.

R18.2 - **JWT signing key management.** The RS256 private key must be stored securely on the server. Key rotation is manual for MVP (U18.53). If the key is compromised, all issued JWTs are compromised until the key is rotated and old tokens expire. The key should be stored outside the application directory with strict file permissions, and a key rotation procedure should be documented before production launch.

R18.3 - **No refresh tokens means frequent re-authorization.** With 1-hour access token expiry and no refresh tokens (U18.50), ChatGPT users will need to re-authorize every hour. This may be disruptive for long ChatGPT sessions. Monitor user feedback; if this is a significant pain point, add refresh tokens in a follow-up iteration.

R18.4 - **OAuth consent screen UX is outside the ChatGPT iframe.** The user is redirected from ChatGPT to the LocoRoomail login/consent page in their browser. This context switch may confuse users who expect everything to happen inside ChatGPT. The consent screen should be minimal, clearly branded as LocoRoomail, and redirect back to ChatGPT immediately after approval.

# Important Considerations
M1 - Make the user interface look modern with Tailwind CSS. 
M2 - Use Python and Flask. 
M3 - Use MVC (Model View Controller) pattern within each module. Each module (mail, contacts, calendar) is a self-contained Flask Blueprint with its own controllers, models, services, and templates. No module may import from another module. Cross-module data access goes through `app.shared` only. 
M4 - Data privacy is a must. Only the final user can access email content; other roles cannot.
M5 - You prefer to create multiple small files rather than single large files. A file is considered large if > 600 lines. Each module is a bounded context; stay within module boundaries when making changes.
M6 - Each user will have an individual SQLite database per account. This database will be used as cache to make the UI work faster, and will include data related with functionality that won't be available in IMAPS. Cache headers + body (not attachments) and encrypt at rest using a per-user key. For customers without API access, the key is derived from their credential (password) and lives only in process memory; if the credential changes, expire caches and re-sync. For customers with API access enabled (U14), a random Data Encryption Key (DEK) is used instead, wrapped with the credential and with each API token — password changes only require re-wrapping the DEK, not cache re-keying. Cache policy (per folder): always include unread messages, plus the most recent 30 days. Ensure at least the most recent page size (100) on folder open even if older than 30 days. 
M6a - Use SQLCipher (encrypted SQLite) to support encrypted-at-rest cache with local full-text search.
M6b - The SQLCipher cache is **ephemeral and fully reconstructable**. For mail, the upstream (IMAP) is the source of truth and the cache can be purged and re-synced at any time. For docs, document metadata is embedded in each ODF file on disk (U13.85) so the cache can be nuked and rebuilt from disk via `resync_docs()`. This principle enables safe cache purge operations (e.g., when changing encryption keys) without data loss.
M6c - `purge_cache(path, key=None)` in the mail module supports two modes: (1) without `key` — deletes the entire SQLCipher file (used for API key rotation, admin purge, account removal); (2) with `key` — drops only mail-related tables (`folders`, `messages`, `threads`, etc.) while preserving non-mail tables like `documents` (used for mail-specific cache reset from Settings).
M7 - If you are going to implement funcionatlity that is not available in IMAPS please call it out. This is a big deal as other applications (i.e standard Android apps to read email) won't support. 
M7a - Mailbox import requests, signed import links, import-run checkpoints, imported-message mappings, and Google-source imports are application-level migration features and not part of standard IMAP client behavior. They must be explicitly documented in the product and UI.
M7b - Google Takeout upload handling, chunked/resumable staging, temporary file storage, and content-hash deduplication are also application-level migration features and not part of standard IMAP client behavior.
M8 - Enforce TLS for all IMAP/SMTP connections; authentication is only permitted over TLS.
M9 - Use a main application SQLite database for admins/managers/customers, domain config, and audit logs.
M9a - Audit logs (common): admin/manager login success/failure (no customer login auditing); domain create/update/disable; IMAP/SMTP setting changes; manager assignment changes; customer create/deactivate/reactivate; customer cache reset; API access enable/disable (U14); API token create/revoke; API request audit (method, path, token ID, response status — no request/response bodies). Include IP address and user agent where available. Audit logs must never include raw token values, wrapped DEKs, email bodies, or attachment content.
M9c - Import audit logs must record privacy-safe lifecycle events only: import request create/update/disable, link use, OAuth start/callback success/failure, import run start/finish/cancel, and aggregate folder/message counts. Never log message content, attachment names, OAuth codes/tokens, or destination passwords.
M9d - Takeout upload audit logs may include privacy-safe file metadata such as upload started/completed, byte counts, chunk counts, parse status, and aggregate message counts, but must never log message content, attachment names, raw file paths visible to end users, or uploaded file contents.
M9b - Main app database can be plaintext for MVP; audit logs do not require encryption at rest.
M10 - Rate limiting for admin/manager login: per-IP and per-username; 5 failed attempts per 10 minutes; exponential backoff (1/5/15 minutes); temporary 30-minute lock after 10 failures.
M11 - Session timeouts: admin/manager idle timeout 30 minutes; customer session duration 1 month.
M12 - Use background workers for IMAP syncing/IDLE and search expansion.
M12a - For MVP, background workers can run within the Flask app process (no separate service).
M12b - Incremental sync must detect moves/expunges via QRESYNC/CONDSTORE when available; otherwise use UID tracking with periodic UID diffs for active folders. Avoid full-folder re-syncs unless UIDVALIDITY changes.
M12c - When IMAP sync fails, retry with bounded exponential backoff per account/folder to reduce repeated failure noise.
M12d - Mailbox imports must also run in background workers with resumable checkpoints and bounded retry/backoff per import run and folder.
M12e - Large Google Takeout uploads must be processed through background workers with resumable upload state, staged-file validation, parse checkpoints, and bounded retry/backoff so interrupted uploads/imports can continue safely without restarting from zero where practical.
M13 - Logging & observability (privacy-first): log operational events and failures without exposing message content or secrets.
M13a - Never log email bodies, subjects, attachments, passwords, OAuth tokens, or raw message payloads. Prefer internal IDs; if an email address must be logged, redact it.
M13b - Log key lifecycle events: background worker start/stop; IMAP sync start/finish with folder/message counts; IMAP/SMTP connection and auth failures (no secrets); cache open/create; SSE subscription start/stop; and unexpected exceptions with stack traces.
M13c - Default log level is INFO in development and WARNING in production, configurable via environment. Logs go to stdout (console) only.
M13d - Dev-only snippet diagnostics may log privacy-safe metadata (content-type availability, text lengths, rule match counts, reason codes), but never message content or subjects.

# UI/UX Guidelines (MVP)
UX1 - List density settings are out of scope for MVP; use the default list density.
UX2 - Message list rows are fully clickable; subtle hover actions are available (archive/delete/mark read) without overwhelming the layout.
UX3 - Message list visual hierarchy: subject is primary; sender and snippet are secondary; date/time is tertiary and right-aligned.
UX3b - Message list row actions are hidden by default and appear on hover/focus. No reserved action column; actions overlay the right side of the row. On touch/mobile, show a “…” toggle on the right to reveal/hide actions.
UX3c - Message list row action hierarchy (Gmail-style):
  - Star toggle: always visible as a star icon in a narrow column to the left of the subject. Filled amber when starred, muted outline when not. Clicking toggles the IMAP flag.
  - Primary hover actions (desktop): Archive and Delete buttons appear on row hover/focus, overlaid on the right side of the row.
  - Secondary actions: a “⋯” button appears alongside the primary actions on hover; clicking it reveals a small dropdown with Mark as Read/Unread and Report Spam.
  - On touch/mobile, the “…” toggle reveals all actions in the overlay (primary and secondary via the dropdown).
UX3a - Message list row layout:
  - Line 1: Subject on the left; Sender and Date on the right. Subject truncates with ellipsis; full subject is available via tooltip (hover/focus).
  - Sender block shows up to two words from the display name; if only an email address is available, use the local-part (before "@"). Sender truncates with ellipsis; full name + email available via tooltip (hover/focus).
  - Date/time display uses the user's configured timezone.
  - If a message is within the last 24 hours, display time in 24-hour format: "HH:mm".
  - Otherwise, display "12 Jan" for messages in the current year and "12 Jan 24" for messages in other years.
  - Unread messages are indicated with bold subject and a subtle background tint.
  - Optional subtle thread count chip appears to the right of the subject.
UX4 - Snippets are sanitized and normalized (no raw HTML fragments); clamp to 1–2 lines with ellipsis.
UX4a - Snippet extraction rules:
  - Prefer the plain-text body part; if missing, derive from HTML via HTML-to-text conversion.
  - Normalize whitespace and strip zero-width characters before extraction.
  - Skip greeting/header lines (e.g., "Hi", "Hello", "Dear", "View in browser", brand-only headers).
  - Skip common boilerplate (unsubscribe links, privacy/terms, tracking prompts, social/link lists).
  - Skip quoted replies and forwards (e.g., lines starting with ">", or "On <date> <name> wrote:").
  - Choose the first paragraph that contains meaningful content (minimum word count, not mostly URLs/brand).
  - Maintain a small, editable pattern list for boilerplate/greeting detection (defaults stored in data/snippet_patterns.json).
UX4b - Dev-only snippet debug view may be enabled via a query param to show rule decisions and the chosen candidate in the UI; must be disabled in production.
UX7 - Empty state behavior: show an in-list syncing status (e.g., "Syncing 2 of 10..." or "Syncing...") while a folder is actively syncing and until we have completed at least one check against IMAP for that folder. Once a completed check confirms the folder has zero messages (cache + IMAP), show a "No messages" (or similar) empty state. Even when "No messages" is shown, continue background syncing for the active folder and surface new messages if they arrive.
UX7a - Loading skeleton: when the message list is empty and a sync is in progress (no cached messages yet, or first-time folder open), show a skeleton loader instead of plain "Syncing..." text. The skeleton displays 5–6 placeholder rows that mimic the exact layout of real message rows (star column, subject bar, sender/date bar, snippet bar) using animated shimmer/gray bars. This ensures the page layout looks complete and professional from the moment it renders. The skeleton is replaced with real content once the first batch of messages arrives via refreshMessages(). The skeleton is not shown when cached messages are already displayed — only for genuinely empty lists during active sync.
UX7b - FLIP animations for message list updates: when refreshMessages() receives new HTML from the server, instead of bluntly replacing innerHTML, use the FLIP technique (First, Last, Invert, Play) to animate message row transitions smoothly:
  - Before applying new HTML, record the bounding rect of each existing message row (keyed by data-message-id).
  - After applying the new HTML, for rows that still exist: calculate the position delta and animate from old position to new position using CSS transforms (translateY) with a ~250ms ease-out transition.
  - For newly appeared rows: fade in with a subtle slide-down entrance (opacity 0→1, translateY +8px→0).
  - For removed rows: not applicable during background refresh (removals are handled by user actions with their own animations).
  - Respect prefers-reduced-motion: skip all animations and fall back to instant updates when the user has reduced motion enabled.
  - This provides Gmail-like smooth reordering when new messages arrive, flag changes reorder threads, or the sort order changes after a sync completes.
UX5 - Preview pane is toggleable: single-pane is default; split view shows list + message with preserved list density.
UX6 - Use clean, high-contrast typography with muted metadata colors; avoid visual noise in dense layouts.
UX3d - Message detail header shows a star toggle and, when the account supports it, a lock toggle to the left of the subject (after the back arrow). Each reflects the current state (filled/active when set, muted outline when not) and toggles in place via the existing flag/lock endpoints with a subtle inline spinner while in flight. These replace the Star/Lock entries previously in the ⋯ overflow menu (no duplicate controls). The star toggle is always shown; the lock toggle is hidden when the account's IMAP server rejects the `$Locked` keyword.

# Use Case U19 – Mail Server Management API

## Overview

U19.1 - The mail server management API (`mail-api`) is a lightweight Flask service that runs on the mail server alongside Dovecot and Postfix. It provides a REST interface for the LocoRoomail admin panel to manage domains, users, and mailboxes without directly accessing configuration files.

U19.2 - The `mail-api` runs as a separate Docker container in development and as a standalone process on the mail server in production. It shares volumes with Dovecot (passwd-file) and Postfix (virtual domain maps) to manage configuration.

U19.3 - Communication between the LocoRoomail app and `mail-api` uses HTTP with Bearer token authentication. In development, this runs over the Docker network. In production, it can run over a VPN, private network, or HTTPS.

## Architecture

U19.4 - Deployment topology (development):
```
LocoRoomail app container
  └── HTTP calls ──► mail-api container (port 8800)
                       ├── DovecotManager (writes /etc/dovecot/users)
                       └── PostfixManager (writes /etc/postfix/virtual_domains)
                              │
                       Shared volumes:
                       ├── dovecot-users → Dovecot container
                       ├── postfix-config → Postfix container
                       └── maildata → both containers
```

U19.5 - Deployment topology (production):
```
LocoRoomail server                        Mail server
  └── HTTP/HTTPS ──────────────────────► mail-api process (port 8800)
                                           ├── DovecotManager
                                           └── PostfixManager
                                                  │
                                           Local FS access:
                                           ├── /etc/dovecot/users
                                           ├── /etc/postfix/virtual_domains
                                           └── /var/mail/vhosts/
```

## Directory Structure

U19.6 - The `mail-api` code lives in the same repository under `mail-api/`:
```
mail-api/
  server.py              # Flask REST API
  managers/
    __init__.py
    dovecot.py            # DovecotManager: passwd-file + maildir management
    postfix.py            # PostfixManager: virtual domain map management
  Dockerfile
  requirements.txt
  settings.cfg            # Configuration (env-based)
```

U19.7 - The client integration lives in the admin module:
```
app/admin/services/mail_server/
  __init__.py             # get_mail_client() factory
  base.py                 # MailServerProvider protocol
  http_client.py          # MailApiClient: HTTP calls to mail-api
```

## Endpoints

U19.8 - `GET /health` — Health check (no auth required).

U19.9 - Domain endpoints:
  - `GET /api/domains` — List all virtual domains.
  - `POST /api/domains` — Add a domain. Body: `{ "domain": "example.com" }`. Idempotent: returns 201 if added, 201 if already exists.
  - `DELETE /api/domains/{domain}` — Remove a domain.

U19.10 - User endpoints:
  - `GET /api/users?domain=example.com` — List users, optionally filtered by domain.
  - `POST /api/users` — Create a mailbox user. Body: `{ "email": "user@example.com", "password": "..." }`. Returns 409 if user already exists.
  - `DELETE /api/users/{email}` — Remove a user. Returns 404 if not found.
  - `PUT /api/users/{email}/password` — Set password. Body: `{ "password": "..." }`. Returns 404 if user not found.
  - `GET /api/users/{email}/check` — Check if a user exists. Returns 200 with `{ "exists": true }` or 404.

U19.11 - All endpoints except `/health` require `Authorization: Bearer <key>` when `MAIL_API_KEY` is configured.

## Security

U19.12 - API key authentication: a shared secret between the LocoRoomail app and `mail-api`. Configured via `MAIL_API_URL` and `MAIL_API_KEY` environment variables.

U19.13 - Passwords are hashed using Dovecot's `doveadm pw` utility with SHA256-CRYPT scheme before storage in the passwd-file.

U19.14 - The `mail-api` only listens on the internal network (port 8800). It is never exposed to the public internet.

## Admin Integration

U19.15 - When the admin creates a domain and it reaches `status=complete` + `is_active=true`, the LocoRoomail app calls `mail-api` `POST /api/domains` to add it to Postfix's virtual domains.

U19.16 - When the admin deactivates a domain, the app calls `DELETE /api/domains/{name}` to remove it.

U19.17 - When the admin creates a customer with a `domain_id` and `password`, the app calls `POST /api/users` to create the mailbox on the mail server.

U19.18 - If the `mail-api` is unreachable (`MAIL_API_URL` not configured or connection fails), the admin action still completes locally (domain/user saved to SQLite) but an error is flashed to the admin. This graceful degradation ensures the app works without a mail server during development or if the mail server is temporarily down.

## Development Environment

U19.19 - The development environment uses `docker-compose.dev.yml` with the following services:
  - **app** — LocoRoomail Flask app (ports 5001, 8001)
  - **mail-api** — Mail server management API (port 8800)
  - **dovecot** — IMAP server (ports 143, 993) with self-signed TLS
  - **postfix** — SMTP server (ports 25, 587) with local-only delivery
  - **opendkim** — DKIM signing (internal port 8891)
  - **collabora** — Document editing (port 9980)
  - **radicale** — CalDAV/CardDAV (port 5232)

U19.20 - All mail infrastructure uses custom Dockerfiles under `dev-infra/`:
  - `dev-infra/dovecot/` — Dovecot with pre-configured virtual mailbox setup matching production (`%d/%n` maildir layout, passwd-file auth, LMTP).
  - `dev-infra/postfix/` — Postfix with virtual domain transport to Dovecot LMTP, DKIM milter, and local-only relay (no outbound delivery).
  - `dev-infra/opendkim/` — OpenDKIM with auto-generated dev key.

U19.21 - Self-signed TLS certificates are auto-generated on first start for Dovecot and Postfix. The IMAP/SMTP clients in the LocoRoomail app skip certificate verification when `APP_ENV=development`.

U19.22 - The `mail-api` and Dovecot share a Docker volume (`dovecot-users`) for the passwd-file. The `mail-api` and Postfix share a Docker volume (`postfix-config`) for virtual domain maps. The `maildata` volume is shared between Dovecot, Postfix, and mail-api for maildir storage.

## Quota & Sending Limits

U19.30 - Dovecot storage quota: the quota plugin is enabled per U19.33. When creating a user via `POST /api/users`, the optional `quota_bytes` field sets the per-user mailbox storage limit. The quota is stored as a userdb extra field in the passwd-file: `userdb_quota_rule=*:bytes=N`. Dovecot enforces the quota on IMAP APPEND and LMTP delivery.

U19.31 - `POST /api/users` accepts an optional `quota_bytes` field (integer, bytes). If omitted, no quota is set. DovecotManager writes the quota as an extra field in the passwd-file entry.

U19.32 - `PUT /api/users/{email}/quota` updates the storage quota for an existing user. Body: `{ "quota_bytes": 5368709120 }`. Rewrites the passwd-file entry with the updated quota field.

## Sending Rate Limit (Policy Daemon)

U19.40 - A Python asyncio TCP policy daemon (`policy_server.py`) runs alongside the mail-api on port 9900. It speaks the Postfix policy delegation protocol and enforces per-user daily sending limits.

U19.41 - The policy daemon reads `sasl_username` from the Postfix policy request and checks a local SQLite database (`sending_limits` table) for the user's daily send count and maximum.

U19.42 - `sending_limits` table schema: `email` (TEXT, PK), `max_per_day` (INTEGER, NOT NULL), `sent_today` (INTEGER, DEFAULT 0), `last_reset_date` (TEXT, ISO 8601 date).

U19.43 - On each policy request: if `last_reset_date` < today (UTC), reset `sent_today` to 0 and update `last_reset_date`. If `sent_today` < `max_per_day`, increment `sent_today` and return `action=DUNNO`. If `sent_today` >= `max_per_day`, return `action=REJECT Daily sending limit reached. Limit resets at midnight UTC.`

U19.44 - The policy daemon only applies to authenticated submission (port 587). Postfix master.cf adds `smtpd_end_of_data_restrictions=check_policy_service inet:localhost:9900` to the submission service. Port 25 (incoming external mail) is not rate-limited.

U19.45 - Counting is per message (not per recipient). One email with multiple recipients counts as 1.

U19.46 - Sending limit CRUD endpoints:
   - `POST /api/users/{email}/sending-limit` — Set sending limit. Body: `{ "max_per_day": 200 }`. Creates or replaces the `sending_limits` row.
   - `GET /api/users/{email}/sending-limit` — Get current sending limit and today's count. Response: `{ "max_per_day": N, "sent_today": N, "last_reset_date": "..." }`.
   - `DELETE /api/users/{email}/sending-limit` — Remove sending limit. The policy daemon returns `DUNNO` for users without a limit row (no enforcement).

U19.47 - The policy daemon uses its own SQLite database (`/var/lib/mail-api/sending_limits.db`) separate from the passwd-file, to avoid locking contention with Dovecot auth lookups.

U19.48 - The mail-api entrypoint runs both the Flask API (port 8800) and the policy daemon (port 9900) as separate processes.

## Out of Scope for MVP

U19.50 - ~~No quota management.~~ (Implemented per U19.30-U19.32.)
U19.51 - No alias management (beyond what Postfix virtual maps provide).
U19.52 - No mailbox migration between domains.
U19.53 - No sieve filter management via the API.
U19.54 - No automatic Let's Encrypt certificate provisioning.

# Use Case U20 – Provisioning API

## Overview

U20.1 - The provisioning API is a set of authenticated REST endpoints in the LocoRoomail app that allow an external system (locoroo.net) to provision mailboxes, manage domains, and validate DNS configuration. This is the integration point between the locoroo subscription system and the locoroomail mail infrastructure.

U20.2 - All provisioning endpoints require `Authorization: Bearer <PROVISIONING_API_KEY>`. The key is configured via the `PROVISIONING_API_KEY` environment variable. This is separate from the existing `MAIL_API_KEY` used for admin-to-mail-api communication.

U20.3 - The provisioning API lives under `/api/provision/` as a Flask blueprint registered in the main app. It calls the existing mail-api client (`app/admin/services/mail_server/`) and DNS check service (`app/admin/services/dns_checks.py`).

## Directory Structure

U20.4 - Provisioning API files:
```
app/provisioning/
  __init__.py        # register(app) — registers blueprint
  controllers.py     # Provisioning API endpoints
  auth.py            # API key authentication decorator
tests/provisioning/
  conftest.py        # Test fixtures
  test_provisioning.py  # Integration tests
```

## Endpoints

U20.5 - `POST /api/provision/check-availability` — Check if an email address already exists as a mailbox. Body: `{ "email": "user@locoroo.net" }`. Response: `{ "available": true }`. Calls `mail-api GET /api/users/{email}/check`.

U20.6 - `POST /api/provision/create-domain` — Add a domain to the mail infrastructure. Body: `{ "domain": "example.com" }`. Calls `mail-api POST /api/domains`. Response: `{ "created": true, "domain": "example.com" }`.

U20.7 - `POST /api/provision/create-mailbox` — Create a mailbox user. Body: `{ "email": "user@example.com", "password": "...", "domain": "example.com", "quota_bytes": 5368709120, "max_emails_per_day": 200 }`. Calls `mail-api POST /api/users` (with `quota_bytes`), then `mail-api POST /api/users/{email}/sending-limit`. If the domain does not exist in Postfix, creates it first. Response: `{ "created": true, "email": "user@example.com" }`.

U20.8 - `DELETE /api/provision/mailbox/{email}` — Remove a mailbox. Calls `mail-api DELETE /api/users/{email}` and `mail-api DELETE /api/users/{email}/sending-limit`. Response: `{ "deleted": true }`.

U20.9 - `GET /api/provision/users/{domain}` — List mailbox users for a domain. Calls `mail-api GET /api/users?domain={domain}`. Response: `{ "data": [{ "email": "..." }] }`.

U20.10 - `POST /api/provision/generate-dkim` — Generate a DKIM key pair for a domain. Body: `{ "domain": "example.com" }`. Calls `mail-api POST /api/dkim/{domain}`. Response: `{ "selector": "default", "public_key": "...", "txt_record": "v=DKIM1; k=rsa; p=..." }`.

U20.11 - `GET /api/provision/dns-records/{domain}` — Return the expected DNS records for a self-hosted domain. Reads the platform DNS configuration (MX servers from `PlatformDnsConfig`), the domain's DKIM key, DMARC policy, and SPF value. Response: `{ "mx": "...", "spf": "...", "dkim": { "selector": "...", "txt_record": "..." }, "dmarc": "..." }`.

U20.12 - `POST /api/provision/validate-dns/{domain}` — Validate all DNS records (MX, SPF, DKIM, DMARC) against the domain's authoritative nameservers. Uses `run_all_dns_checks` from `app/admin/services/dns_checks.py`. Response: `{ "mx": { "status": "verified|not_configured|propagating", "expected": "...", "found": [...] }, "spf": { ... }, "dkim": { ... }, "dmarc": { ... } }`.

U20.13 - `POST /api/provision/validate-ownership/{domain}` — Validate a TXT record for domain ownership proof. Body: `{ "expected_value": "locoroo-verify=abc123" }`. Queries the domain's authoritative nameservers for a TXT record matching the expected value. Response: `{ "verified": true|false, "found": [...] }`.

U20.14 - `PUT /api/provision/mailbox/{email}/quota` — Update mailbox storage quota. Body: `{ "quota_bytes": 5368709120 }`. Calls `mail-api PUT /api/users/{email}/quota`. Response: `{ "updated": true }`.

## Response Format

U20.15 - Successful responses return `200 OK` (GET), `201 Created` (POST), `204 No Content` (DELETE). Response body: `{ "data": { ... } }` for single resources.

U20.16 - Error responses: `{ "error": { "code": "ERROR_CODE", "message": "description" } }` with appropriate HTTP status codes (400 validation, 401 auth, 404 not found, 409 conflict, 500 internal).

## Configuration

U20.17 - New environment variables:
   - `PROVISIONING_API_KEY` — API key for provisioning endpoint authentication. Required. Must be different from `MAIL_API_KEY`.

# Use Case U21 – Dovecot Quota Plugin

## Overview

U21.1 - Dovecot quota plugin is enabled to enforce per-user mailbox storage limits. Quota is set per user via the passwd-file userdb extra field.

U21.2 - The userdb configuration changes from `static` to `passwd-file` so that per-user quota rules can be read from the passwd-file.

## Configuration Changes

U21.3 - Dovecot main config (`dovecot.conf`) enables the quota plugin:
```
mail_plugins = $mail_plugins quota

protocol imap {
  mail_plugins = $mail_plugins imap_quota
}

protocol lmtp {
  mail_plugins = $mail_plugins sieve quota
}

plugin {
  quota = maildir:User quota
  quota_rule = *:storage=0
}
```

U21.4 - Auth config (`auth-passwdfile.conf.ext`) changes userdb from static to passwd-file:
```
passdb {
  driver = passwd-file
  args = scheme=SHA256-CRYPT username_format=%u /var/lib/dovecot-users/passwd
}

userdb {
  driver = passwd-file
  args = scheme=SHA256-CRYPT username_format=%u /var/lib/dovecot-users/passwd
}
```

U21.5 - Passwd-file entries include quota as an extra field:
```
user@domain.com:{SHA256-CRYPT}hash::::::userdb_quota_rule=*:bytes=5368709120
```

U21.6 - When no quota extra field is present, Dovecot falls back to the default `quota_rule = *:storage=0` (no limit). This preserves backward compatibility with existing users created before quota support.

# Use Case U22 – Two-Factor Authentication (2FA)

## Overview

U22.1 - 2FA is an optional, per-user security enhancement. All roles (admin, manager, customer) can individually opt in from their own settings page. There is no admin-mandated or platform-wide enforcement for MVP.

U22.2 - 2FA method: TOTP (RFC 6238) via authenticator apps (Google Authenticator, Authy, 1Password, etc.) plus single-use backup/recovery codes. No SMS, no WebAuthn for MVP.

U22.3 - 2FA scope is per-person (per User row), not per-CustomerAccount. A customer with multiple mailboxes verifies 2FA once at login; all accounts are then accessible.

## Data Model

U22.4 - User table additions (main app.db):
  - totp_secret (VARCHAR(64), nullable) — base32-encoded TOTP shared secret
  - totp_enabled (BOOLEAN, default false, not null) — whether 2FA is active for this user
  - backup_codes (TEXT, nullable) — JSON array of SHA-256 hashed single-use recovery codes

U22.5 - A user with totp_enabled=false has no 2FA requirement and logs in as before.

U22.6 - TrustedDevice table (main app.db):
  - id (PK, auto-increment)
  - user_id (FK to users.id, indexed)
  - token_hash (VARCHAR(64), unique — SHA-256 of raw device cookie token)
  - user_agent (VARCHAR(255), nullable — for display only)
  - ip_address (VARCHAR(64), nullable — last known IP at creation)
  - created_at (UTC datetime, not null)
  - last_used_at (UTC datetime, nullable — updated on each login using this device)
  - expires_at (UTC datetime, not null — 30 days from creation)
  - revoked_at (UTC datetime, nullable)

## Dependencies

U22.7 - New dependencies: pyotp (TOTP generation/verification), qrcode (QR code PNG generation for enrollment). Both are pure-Python with no external service requirements.

## Login Flow — Two-Phase

U22.8 - All login flows (admin/manager at /admin/login, customer at /app/login) become two-phase when 2FA is enabled for the authenticating user:

  Phase 1 (credential verification):
    - Admin/Manager: verify password hash as today. If valid and totp_enabled is true, store session["_pending_2fa_user_id"] = user.id and render the TOTP entry page. Session role is NOT set.
    - Customer: validate IMAP credentials, derive cache key, set_user_key(), find/create user and account, encrypt/store credential as today. If totp_enabled is true, store session["_pending_2fa_user_id"] = customer.id and render the TOTP entry page. Session role is NOT set, sync is NOT enqueued, active_account_id is NOT set.

  Phase 2 (TOTP verification):
    - User submits a 6-digit TOTP code (or a backup code).
    - Valid TOTP: clear pending flag, set session role and remaining session vars, enqueue sync (customer only), redirect to role landing page.
    - Valid backup code: mark that backup code as used (removed from backup_codes array), proceed as valid TOTP.
    - Invalid: re-render TOTP page with error. Rate-limited per M10 pattern (5 failures, then temporary lockout). After lockout, clear_user_key() (customer) and clear session.

U22.9 - During Phase 1 pending state, no routes are accessible:
    - session["role"] is not set, so require_role / require_customer decorators redirect away.
    - For customers, the derived key sits in _user_keys but no sync is enqueued and no account is active. If the user abandons (navigates away, session expires), the key persists in _user_keys but is inaccessible without the session. clear_user_key() is called on any failed/abandoned 2FA attempt and on logout.

U22.10 - TOTP verification window: ±1 time step (30 seconds each, so the previous, current, and next codes are accepted). This matches standard authenticator app behavior and accommodates minor clock drift.

U22.11 - The TOTP entry page includes a "Use a backup code" link that switches the input to accept an 8-character alphanumeric backup code instead of a 6-digit TOTP code.

## Trusted Devices (Remember This Device)

U22.12 - After successfully completing 2FA verification (Phase 2), the TOTP entry page offers a "Remember this device for 30 days" checkbox. If checked, the server issues a trusted-device cookie so subsequent logins from the same browser skip the 2FA step.

U22.13 - Trusted-device token: a cryptographically random 32-byte value (base64url encoded), issued once and stored as an HTTP-only, Secure, SameSite=Lax cookie named "lr_trusted_device" with a 30-day max-age. The cookie is scoped to path="/" so it applies to both /admin/login and /app/login.

U22.14 - On the server side, only the SHA-256 hash of the token is stored (never the raw token). The raw token lives only in the user's browser cookie.

U22.15 - Phase 1 device check: after credential validation, if the user has totp_enabled=true, the server checks for the lr_trusted_device cookie BEFORE rendering the TOTP page:
    1. Read cookie value, compute SHA-256 hash.
    2. Query TrustedDevice by token_hash + user_id where revoked_at IS NULL and expires_at > now.
    3. If found and valid: skip Phase 2 entirely. Complete login (set role, enqueue sync for customers, etc.). Update last_used_at on the TrustedDevice row.
    4. If not found, expired, or revoked: clear the invalid cookie and proceed to Phase 2 (TOTP entry page) as normal.

U22.16 - Trusted devices are per-user (per User row). A trusted device for an admin account does not bypass 2FA for a customer login on a different User row, even if the same email address is used (per N5).

U22.17 - The 30-day trust window is measured from the cookie issuance time, not extended on each login. After 30 days, the cookie expires and the user must complete 2FA again. A new trusted-device cookie can be issued at that point if the checkbox is checked again.

U22.18 - Trusted-device management UI: the Security section (U22.22) shows a list of trusted devices with: device description (parsed from user agent), created date, last used date, and a "Revoke" button per device. A "Revoke all trusted devices" button revokes every device for the user in one action.

U22.19 - Revocation sets revoked_at on the TrustedDevice row. The cookie remains in the user's browser but is rejected on next login (cleared at that point).

U22.20 - Disabling 2FA (U22.23) automatically revokes all trusted devices for that user.

U22.21 - Changing the account password does NOT automatically revoke trusted devices for MVP. Users can manually revoke from the security settings.

## Enrollment / Management

U22.22 - 2FA enrollment is available from:
    - Admin/Manager: a "Security" section in the admin settings area (/admin/settings/security).
    - Customer: the existing customer Settings page (U9), under a new "Security" section.

U22.23 - Enrollment flow (enable 2FA):
    1. User clicks "Enable 2FA".
    2. Server generates a random TOTP secret (pyotp.random_base32()), stores it temporarily in session (NOT yet saved to User row — totp_enabled stays false).
    3. Server renders a QR code (otpauth:// URI per RFC 6238) and the secret as text.
    4. User scans QR with their authenticator app, enters the current 6-digit code to confirm.
    5. On valid code: save totp_secret + totp_enabled=true to User row, generate 10 backup codes, display them once with a clear warning, prompt user to confirm they saved them.
    6. 2FA is now active.

U22.24 - Disabling 2FA requires the user to enter a valid TOTP code (or backup code). On success: clear totp_secret, set totp_enabled=false, clear backup_codes, revoke all trusted devices.

U22.25 - Regenerating backup codes requires a valid TOTP code. Old codes are invalidated. New codes are displayed once.

U22.26 - The 2FA management UI shows the current status (enabled/disabled), a link to view remaining backup code count (codes themselves are never re-displayed), and enable/disable/regenerate-backup-codes actions.

## Backup Codes

U22.27 - 10 single-use alphanumeric codes (8 characters, base32 alphabet) generated at enrollment. Stored as SHA-256 hashes in the backup_codes JSON array on the User row.

U22.28 - Each backup code can be used exactly once. On use, it is removed from the array and the User row is updated.

U22.29 - Backup codes are displayed exactly once at enrollment (and once on regeneration). They are never re-displayed or recoverable. If all codes are lost and the user loses their authenticator device, an admin can disable 2FA for the account via CLI.

## Admin / CLI Reset

U22.30 - Admin password resets remain CLI-only (per U1.1b). The CLI gains a command to disable 2FA for a specific user (e.g., `flask twofa-disable <email>`). This clears totp_secret, totp_enabled, and backup_codes for that user.

U22.31 - Admins cannot see or manage 2FA for customers from the web UI (privacy per M4). Customer 2FA is entirely self-managed.

## Security

U22.32 - TOTP secrets are stored in plaintext in the main app.db (not the encrypted per-user cache). Rationale: the TOTP secret must be readable by the app during login verification, and the main app.db already stores admin password hashes (as werkzeug hashes). The main app.db is plaintext for MVP (M9b).

U22.33 - Rate limiting on the TOTP verification endpoint follows the same M10 pattern as login: 5 failed attempts per 10 minutes, then temporary lockout.

U22.34 - The TOTP entry page does not reveal which credential was incorrect. Failed Phase 1 (password/IMAP) errors are identical whether or not 2FA is enabled, preventing enumeration of which accounts have 2FA active.

## Relationship to Existing Auth

U22.35 - 2FA is orthogonal to API access (U14) and OAuth (U18). API tokens and OAuth JWTs authenticate via bearer tokens that bypass the web login flow entirely. 2FA only applies to interactive web sessions (/admin/login, /app/login).

U22.36 - Enabling/disabling 2FA does not affect existing sessions, API tokens, or cache encryption keys. It only gates future logins.

## Out of Scope for MVP

U22.50 - No WebAuthn / passkey / hardware key support.
U22.51 - No SMS-based 2FA.
U22.52 - No admin-mandated / forced 2FA for roles or domains.
U22.54 - No 2FA for API or OAuth token flows (U14/U18 remain bearer-token only).
