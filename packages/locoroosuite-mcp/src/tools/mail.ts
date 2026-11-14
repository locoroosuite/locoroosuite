import type { McpServer } from "@modelcontextprotocol/server";
import { z } from "zod";
import type { ApiClient } from "../client.js";

function json(data: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
}

export function registerMailTools(server: McpServer, client: ApiClient) {
  server.registerTool("mail_list_folders", { description: "List email folders with unread counts", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            }) }, async ({ account_id }) => {
              const data = await client.get("/api/v1/mail/folders", client.accountId(account_id));
              return json(data);
            });

  server.registerTool("mail_create_folder", { description: "Create a new mail folder. Idempotent: creating an existing mailbox returns success with created=false. Optional parent nests the folder using the server hierarchy delimiter.", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              name: z.string().describe("Mailbox/folder name to create"),
              parent: z.string().optional().describe("Optional parent folder for nesting (e.g. parent<delim>name)"),
            }) }, async ({ account_id, name, parent }) => {
              const data = await client.post("/api/v1/mail/folders", {
                ...client.accountId(account_id),
                name,
                parent,
              });
              return json(data);
            });

  server.registerTool("mail_rename_folder", { description: "Rename a mail folder. System folders (INBOX, Sent, Drafts, Trash, Junk, Bookings) cannot be renamed.", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              folder_id: z.string().describe("Current folder name"),
              name: z.string().describe("New folder name"),
            }) }, async ({ account_id, folder_id, name }) => {
              const data = await client.post(
                `/api/v1/mail/folders/${encodeURIComponent(folder_id)}/rename`,
                { ...client.accountId(account_id), name },
              );
              return json(data);
            });

  server.registerTool("mail_delete_folder", { description: "Delete a mail folder. System folders and folders marked protected cannot be deleted.", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              folder_id: z.string().describe("Folder name to delete"),
            }) }, async ({ account_id, folder_id }) => {
              const data = await client.delete(
                `/api/v1/mail/folders/${encodeURIComponent(folder_id)}`,
                client.accountId(account_id) as Record<string, string> | undefined,
              );
              return json(data);
            });

  server.registerTool("mail_list_messages", { description: "List messages in a folder with pagination and optional filters", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              folder_id: z.string().describe("Folder ID to list messages from"),
              cursor: z.string().optional().describe("Pagination cursor from previous response"),
              max_results: z.number().min(1).max(200).optional().describe("Maximum number of messages to return (1–200, default 50)"),
              unread: z.boolean().optional().describe("Filter to unread messages only"),
              flagged: z.boolean().optional().describe("Filter to flagged messages only"),
              since: z.string().optional().describe("ISO 8601 datetime — only messages after this time"),
            }) }, async ({ account_id, folder_id, cursor, max_results, unread, flagged, since }) => {
              const data = await client.get(
                `/api/v1/mail/folders/${encodeURIComponent(folder_id)}/messages`,
                { ...client.accountId(account_id), cursor, max_results: max_results?.toString(), unread: unread?.toString(), flagged: flagged?.toString(), since },
              );
              return json(data);
            });

  server.registerTool("mail_get_message", { description: "Get full message content including body and attachments list", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              message_id: z.string().describe("Message ID"),
            }) }, async ({ account_id, message_id }) => {
              const data = await client.get(
                `/api/v1/mail/messages/${encodeURIComponent(message_id)}`,
                client.accountId(account_id),
              );
              return json(data);
            });

  server.registerTool("mail_search", { description: "Search messages with query string and optional filters", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              q: z.string().describe(
                "Search query string; supports operators: from:, to:, subject:, folder:, is:unread, is:starred, is:draft, has:attachment, filename:, before:YYYY-MM-DD, after:YYYY-MM-DD",
              ),
              folder_id: z.string().optional().describe("Restrict search to a specific folder"),
              unread: z.boolean().optional().describe("Filter to unread messages"),
              flagged: z.boolean().optional().describe("Filter to flagged messages"),
              since: z.string().optional().describe("ISO 8601 datetime — only messages after this time"),
              until: z.string().optional().describe("ISO 8601 datetime — only messages before this time"),
              cursor: z.string().optional().describe("Pagination cursor from previous response"),
              max_results: z.number().min(1).max(200).optional().describe("Maximum number of results to return (1–200, default 50)"),
            }) }, async ({ account_id, q, folder_id, unread, flagged, since, until, cursor, max_results }) => {
              const data = await client.get("/api/v1/mail/search", {
                ...client.accountId(account_id),
                q,
                folder_id,
                unread: unread?.toString(),
                flagged: flagged?.toString(),
                since,
                until,
                cursor,
                max_results: max_results?.toString(),
              });
              return json(data);
            });

  server.registerTool("mail_send", { description: "Compose and send a new email. For replies, pass reply_to_message_id (message id from read tools) or in_reply_to (raw RFC 5322 Message-ID) so the reply threads into the source conversation. Optionally pass draft_id to delete that draft after sending (the response reports draft_deleted).", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              to: z.array(z.string()).describe("Recipient email addresses"),
              cc: z.array(z.string()).optional().describe("CC recipients"),
              bcc: z.array(z.string()).optional().describe("BCC recipients"),
              subject: z.string().describe("Email subject"),
              body_plain: z.string().optional().describe("Plain text body"),
              body_html: z.string().optional().describe("HTML body"),
              draft_id: z.string().optional().describe("Draft to delete after sending — cache message id or IMAP UID (both accepted)"),
              in_reply_to: z.string().optional().describe("Raw RFC 5322 Message-ID being replied to (threads the reply)"),
              reply_to_message_id: z.string().optional().describe("Cache message id being replied to (server derives threading headers)"),
            }) }, async ({ account_id, to, cc, bcc, subject, body_plain, body_html, draft_id, in_reply_to, reply_to_message_id }) => {
              const data = await client.post("/api/v1/mail/messages", {
                ...client.accountId(account_id),
                to,
                cc,
                bcc,
                subject,
                body_plain,
                body_html,
                draft_id,
                in_reply_to,
                reply_to_message_id,
              });
              return json(data);
            });

  server.registerTool("mail_move_message", { description: "Move a message to a different folder", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              message_id: z.string().describe("Message ID to move"),
              folder_id: z.string().describe("Destination folder ID"),
            }) }, async ({ account_id, message_id, folder_id }) => {
              const data = await client.post(
                `/api/v1/mail/messages/${encodeURIComponent(message_id)}/move`,
                { ...client.accountId(account_id), folder_id },
              );
              return json(data);
            });

  server.registerTool("mail_delete_message", { description: "Move a message to Trash", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              message_id: z.string().describe("Message ID to delete"),
            }) }, async ({ account_id, message_id }) => {
              const data = await client.delete(
                `/api/v1/mail/messages/${encodeURIComponent(message_id)}`,
                client.accountId(account_id) as Record<string, string> | undefined,
              );
              return json(data);
            });

  server.registerTool("mail_update_flags", { description: "Update read/flagged/locked status of a message", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              message_id: z.string().describe("Message ID"),
              read: z.boolean().optional().describe("Mark as read/unread"),
              flagged: z.boolean().optional().describe("Mark as flagged/unflagged"),
              locked: z.boolean().optional().describe("Toggle delete-protection lock ($Locked) on the message"),
            }) }, async ({ account_id, message_id, read, flagged, locked }) => {
              const flags: Record<string, boolean> = {};
              if (read !== undefined) flags.read = read;
              if (flagged !== undefined) flags.flagged = flagged;
              if (locked !== undefined) flags.locked = locked;
              const data = await client.patch(
                `/api/v1/mail/messages/${encodeURIComponent(message_id)}`,
                { ...client.accountId(account_id), flags },
              );
              return json(data);
            });

  server.registerTool("mail_report_spam", { description: "Report a message as spam: sets the IMAP `Junk` keyword and moves the message to the server's Junk/Spam folder (alias-aware, never auto-created). Returns SPAM_ACTION_DISABLED when the per-account Spam action is off; SPAM_FOLDER_MISSING / SPAM_FLAG_UNSUPPORTED (with the setting auto-disabled, recoverable — it re-enables once the server lists a junk folder again) when a successful LIST shows no junk folder or the server rejects the Junk keyword for an existing message; SPAM_ACTION_TRANSIENT (502) for transient upstream failures, which never change the setting.", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              message_id: z.string().describe("Message ID to report as spam"),
            }) }, async ({ account_id, message_id }) => {
              const data = await client.post(
                `/api/v1/mail/messages/${encodeURIComponent(message_id)}/spam`,
                client.accountId(account_id),
              );
              return json(data);
            });

  server.registerTool("mail_not_spam", { description: "Mark a message as not spam: clears the IMAP `Junk` keyword and moves the message to INBOX when it is currently in a Junk/Spam folder; otherwise clears the keyword only. Recovery action — not gated by the Spam action setting.", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              message_id: z.string().describe("Message ID to mark as not spam"),
            }) }, async ({ account_id, message_id }) => {
              const data = await client.post(
                `/api/v1/mail/messages/${encodeURIComponent(message_id)}/not-spam`,
                client.accountId(account_id),
              );
              return json(data);
            });

  server.registerTool("mail_get_attachment", { description: "Download a message attachment (returns metadata with download URL)", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              message_id: z.string().describe("Message ID"),
              attachment_id: z.string().describe("Attachment ID"),
            }) }, async ({ account_id, message_id, attachment_id }) => {
              const data = await client.get(
                `/api/v1/mail/messages/${encodeURIComponent(message_id)}/attachments/${encodeURIComponent(attachment_id)}`,
                client.accountId(account_id),
              );
              return json(data);
            });

  server.registerTool("mail_view_attachment", { description: "Convert a pandoc-supported attachment to HTML for viewing", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              message_id: z.string().describe("Message ID"),
              attachment_id: z.string().describe("Attachment ID"),
            }) }, async ({ account_id, message_id, attachment_id }) => {
              const data = await client.get(
                `/api/v1/mail/messages/${encodeURIComponent(message_id)}/attachments/${encodeURIComponent(attachment_id)}/view`,
                client.accountId(account_id),
              );
              return json(data);
            });

  server.registerTool("mail_get_thread", { description: "Get all messages in a conversation thread. Use include_bodies=false or body_format=plain to keep payloads small (HTML quote chains are the usual bloat).", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              thread_id: z.string().describe("Thread ID"),
              include_bodies: z.boolean().optional().describe("Set false to return header fields and snippet only (default true)"),
              body_format: z.enum(["plain", "html", "both"]).optional().describe("Body format filter (default both)"),
            }) }, async ({ account_id, thread_id, include_bodies, body_format }) => {
              const data = await client.get(
                `/api/v1/mail/threads/${encodeURIComponent(thread_id)}`,
                { ...client.accountId(account_id), include_bodies: include_bodies?.toString(), body_format },
              );
              return json(data);
            });

  server.registerTool("mail_bulk_move", { description: "Move multiple messages to a different folder", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              folder_id: z.string().describe("Destination folder ID"),
              items: z.array(z.object({
                message_id: z.string().describe("Message ID"),
              })).describe("Array of messages to move").max(100),
            }) }, async ({ account_id, folder_id, items }) => {
              const data = await client.post("/api/v1/mail/bulk/move", {
                ...client.accountId(account_id),
                folder_id,
                items,
              });
              return json(data);
            });

  server.registerTool("mail_bulk_delete", { description: "Delete multiple messages (move to Trash)", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              items: z.array(z.object({
                message_id: z.string().describe("Message ID"),
              })).describe("Array of messages to delete").max(100),
            }) }, async ({ account_id, items }) => {
              const data = await client.post("/api/v1/mail/bulk/delete", {
                ...client.accountId(account_id),
                items,
              });
              return json(data);
            });

  server.registerTool("mail_bulk_flag", { description: "Update flags on multiple messages", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              items: z.array(z.object({
                message_id: z.string().describe("Message ID"),
                flags: z.object({
                  read: z.boolean().optional(),
                  flagged: z.boolean().optional(),
                  locked: z.boolean().optional(),
                }).describe("Flags to set"),
              })).describe("Array of message-flag updates").max(100),
            }) }, async ({ account_id, items }) => {
              const data = await client.post("/api/v1/mail/bulk/flag", {
                ...client.accountId(account_id),
                items,
              });
              return json(data);
            });

  server.registerTool("mail_save_draft", { description: "Save an email as a draft in the Drafts folder. Pass replace_uid to replace an existing draft — both the message id returned by read tools and the IMAP UID (imap_uid) are accepted; unknown ids return DRAFT_NOT_FOUND instead of creating a duplicate. The response includes draft_uid, draft_id (cache id), imap_uid and thread_id. For a reply draft, pass reply_to_message_id or in_reply_to.", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              to: z.array(z.string()).optional().describe("Recipient email addresses"),
              cc: z.array(z.string()).optional().describe("CC recipients"),
              bcc: z.array(z.string()).optional().describe("BCC recipients"),
              subject: z.string().optional().describe("Email subject"),
              body_plain: z.string().optional().describe("Plain text body"),
              body_html: z.string().optional().describe("HTML body"),
              replace_uid: z.string().optional().describe("Existing draft to replace — cache message id or IMAP UID (both accepted)"),
              in_reply_to: z.string().optional().describe("Raw RFC 5322 Message-ID being replied to (threads the draft)"),
              reply_to_message_id: z.string().optional().describe("Cache message id being replied to (server derives threading headers)"),
            }) }, async ({ account_id, to, cc, bcc, subject, body_plain, body_html, replace_uid, in_reply_to, reply_to_message_id }) => {
              const data = await client.post("/api/v1/mail/drafts", {
                ...client.accountId(account_id),
                to,
                cc,
                bcc,
                subject,
                body_plain,
                body_html,
                replace_uid,
                in_reply_to,
                reply_to_message_id,
              });
              return json(data);
            });

  server.registerTool("mail_delete_draft", { description: "Delete a draft email. Accepts the cache message id (draft_id) or the IMAP UID (draft_uid/imap_uid); unknown or already-deleted drafts return DRAFT_NOT_FOUND. A successful response means the deletion was verified.", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              draft_uid: z.string().describe("Draft to delete — cache message id or IMAP UID (both accepted)"),
            }) }, async ({ account_id, draft_uid }) => {
              const data = await client.delete(
                `/api/v1/mail/drafts/${encodeURIComponent(draft_uid)}`,
                client.accountId(account_id) as Record<string, string> | undefined,
              );
              return json(data);
            });

  server.registerTool("mail_get_raw_message", { description: "Get the raw RFC 822 source of an email message as plain text", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              message_id: z.string().describe("Message ID"),
            }) }, async ({ account_id, message_id }) => {
              const data = await client.get(
                `/api/v1/mail/messages/${encodeURIComponent(message_id)}/raw`,
                client.accountId(account_id),
              );
              return json(data);
            });
}
