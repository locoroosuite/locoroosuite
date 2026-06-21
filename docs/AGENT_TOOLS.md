# Agent Tools Reference

Everything an AI agent can do when connected to LocoRooSuite via MCP or the REST API. Agents get the same capabilities as the web UI — they send emails, create calendar events, edit documents, manage contacts. Not just reading.

## Connecting

### Coding agents (Claude Code, Cursor, Windsurf, Cline, Zed, Codex, Gemini CLI)

```bash
npx locoroosuite-mcp --api-url=https://your-instance.com --token=lr_YOUR_TOKEN
```

### Web-based agents (ChatGPT, etc.)

Connect to `https://your-instance.com/mcp` with a Bearer token.

### Getting a token

Settings → API Access → Enable → Create Token. Select scopes per module. Copy the token — it's shown once.

## Accounts

Most tools accept an optional `account_id` parameter. If you have one email account, omit it. If you have multiple, list them first.

| Tool | Description |
|------|-------------|
| `accounts_list` | List the customer's email accounts with IDs and email addresses |

## Mail

Full IMAP-based email. Send, read, search, move, delete, manage folders and drafts, download attachments.

| Tool | Scope | Description |
|------|-------|-------------|
| `mail_list_folders` | read | List folders with unread counts |
| `mail_create_folder` | write | Create a folder (idempotent; optional `parent` for nesting) |
| `mail_rename_folder` | write | Rename a folder (system folders refused) |
| `mail_delete_folder` | write | Delete a folder (system/protected folders refused) |
| `mail_list_messages` | read | Messages in a folder. Filter by unread, flagged, date. Paginated. |
| `mail_get_message` | read | Full message: subject, from, to, cc, bcc, body (plain + HTML), date, flags |
| `mail_get_raw_message` | read | Raw RFC 822 source (`.eml`) |
| `mail_search` | read | Search by query. Filter by folder, unread, flagged, date range. |
| `mail_send` | write | Send email to/cc/bcc with plain text and/or HTML body |
| `mail_save_draft` | write | Save a draft. Optionally replace an existing draft by passing `draft_id`. |
| `mail_delete_draft` | write | Delete a draft from the Drafts folder |
| `mail_move_message` | write | Move a message to another folder (refuses move to Trash if protected) |
| `mail_delete_message` | write | Move a message to Trash (refuses if protected) |
| `mail_update_flags` | write | Set read/unread, flagged/unflagged, locked/unlocked on a message |
| `mail_get_thread` | read | All messages in a conversation thread |
| `mail_get_attachment` | read | Download an attachment (base64-encoded) |
| `mail_view_attachment` | read | Convert a pandoc-supported attachment to HTML |
| `mail_bulk_move` | write | Move up to 100 messages at once (skips protected on Trash moves) |
| `mail_bulk_delete` | write | Delete up to 100 messages at once (skips protected) |
| `mail_bulk_flag` | write | Update flags on up to 100 messages at once |

### Protection

Messages and folders can be protected from accidental deletion. A message is protected when it carries the `$Locked` flag, or when it is starred (`\Flagged`) while "protect starred" is enabled (on by default). System folders (INBOX, Sent, Drafts, Trash, Junk) are always protected.

- `mail_delete_message`, `mail_bulk_delete`, and move-to-Trash via `mail_move_message` / `mail_bulk_move` **refuse** protected items with a `PROTECTED` error.
- Every message and folder response includes a read-only `protected` boolean so you can see this state before acting.
- You can toggle a message's lock with `mail_update_flags` (`locked: true/false`), but the protection **policy** settings (which folders are protected, the "protect starred" toggle) are only configurable in the web UI — the API/MCP only enforce protection, they will not bypass it.

## Contacts

CardDAV-backed address book. Create, update, delete, search.

| Tool | Scope | Description |
|------|-------|-------------|
| `contacts_list` | read | List contacts. Filter by query, sort by name or email. Paginated. |
| `contacts_get` | read | Full contact detail including raw vCard |
| `contacts_search` | read | Search by name, email, or phone. Returns name + email pairs. |
| `contacts_create` | write | Create a contact with name, emails, phones, org, title, notes |
| `contacts_update` | write | Update any field on an existing contact |
| `contacts_delete` | write | Delete a contact |
| `contacts_bulk_delete` | write | Delete up to 100 contacts at once |

Contact fields: `fn` (full name), `email_work`, `email_home`, `phone_work`, `phone_cell`, `phone_home`, `organization`, `title`, `note`.

## Calendar

CalDAV-based calendar. Events, recurring events, attendees, reminders, free/busy.

| Tool | Scope | Description |
|------|-------|-------------|
| `calendar_list_calendars` | read | List calendars with name, color, default flag |
| `calendar_create_calendar` | write | Create a new calendar with name and color |
| `calendar_update_calendar` | write | Rename or recolor a calendar |
| `calendar_delete_calendar` | write | Delete a calendar and all events (requires `confirm=true`) |
| `calendar_list_events` | read | Events in a calendar. Filter by date range. Paginated. |
| `calendar_get_event` | read | Full event detail: summary, description, location, times, attendees, status |
| `calendar_search_events` | read | Search by summary with optional date range |
| `calendar_create_event` | write | Create event with summary, times, location, attendees, reminders, recurrence |
| `calendar_update_event` | write | Update any field on an existing event |
| `calendar_delete_event` | write | Delete an event |
| `calendar_check_free_busy` | read | Find conflicting events in a time range |

Event times are ISO 8601 with timezone (e.g. `2026-06-15T14:00:00+10:00`). Attendees are objects with `email`, optional `cn` (display name), `role`, `partstat`, `rsvp`. Reminders are objects with `type` (`DISPLAY` or `EMAIL`) and `trigger_minutes`. Recurrence uses RRULE strings (e.g. `FREQ=WEEKLY;BYDAY=MO,WE,FR`).

## Documents

Collabora-based document editing via WOPI. Create, read, write, convert, organize into folders, tag, and use a draft/review workflow for AI modifications.

Every document includes `folder_path` (slash-separated, empty = root) and a `tags` array.

| Tool | Scope | Description |
|------|-------|-------------|
| `docs_list_documents` | read | List documents. Filter by type (`odt`, `ods`, `odp`), `folder`, or `tag`; or search by name. |
| `docs_get_document` | read | Document metadata: name, type, size, dates, folder_path, tags |
| `docs_create_document` | write | Create a blank document (`odt`, `ods`, or `odp`); optional `folder` |
| `docs_rename_document` | write | Rename a document |
| `docs_delete_document` | write | Soft-delete (moves to trash) |
| `docs_move_document` | write | Move a document into a folder (empty = root) |
| `docs_read_content` | read | Read document content as text |
| `docs_update_content` | write | Replace document content (markdown input, converted to ODF server-side) |
| `docs_create_draft` | write | Create a draft with modified content — original unchanged |
| `docs_list_drafts` | read | List pending drafts for a document |
| `docs_apply_draft` | write | Accept a draft (replaces original) |
| `docs_discard_draft` | write | Discard a draft |
| `docs_download_document` | read | Download in ODF format (base64-encoded) |
| `docs_export_pdf` | read | Export as PDF (base64-encoded) |
| `docs_convert_document` | write | Convert a non-ODF file (PDF, DOCX) to an editable ODF document |
| `docs_list_folders` | read | List all folders (explicit rows + paths inferred from documents) |
| `docs_create_folder` | write | Create a folder and any missing ancestors (idempotent) |
| `docs_rename_folder` | write | Rename a folder subtree (rewrites document paths) |
| `docs_delete_folder` | write | Delete a folder subtree (contents move to parent) |
| `docs_get_tags` | read | Get the tags applied to a document |
| `docs_update_tags` | write | Add/remove tags, or replace the full tag list with `set` (each tag max 50 chars) |
| `docs_list_tags` | read | List all distinct tags in use across the account (sorted) |

### Draft/review workflow

The draft workflow is designed for AI agents:

1. `docs_read_content` — read the current document
2. `docs_create_draft` — propose changes (markdown content, optional summary)
3. The user sees "AI modifications available" in the web UI
4. User reviews and either accepts (`docs_apply_draft`) or discards (`docs_discard_draft`)

The agent can also apply the draft directly if the use case calls for it. The original document is never overwritten by `docs_create_draft` — only `docs_apply_draft` modifies the original.

## Pagination

List tools return up to 50 items by default. Pass `max_results` (1–200) to adjust. When `has_more` is true in the response, call again with the `cursor` value from the previous response.

## Error handling

All tools return structured errors:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Message not found"
  }
}
```

Common error codes: `NOT_FOUND`, `VALIDATION_ERROR`, `NOT_CONFIGURED`, `PROTECTED`, `IMAP_ERROR`, `CARDDAV_ERROR`, `CALDAV_ERROR`, `CONVERSION_ERROR`, `DEK_MISMATCH`.

`PROTECTED` means the message or folder is protected from deletion (see [Protection](#protection) under Mail). Inform the user — the API/MCP will not bypass it.

`DEK_MISMATCH` means the API token's encryption key doesn't match the stored data — the user needs to reset API access in Settings.
