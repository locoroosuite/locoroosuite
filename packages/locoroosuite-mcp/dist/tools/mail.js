import { z } from "zod";
function json(data) {
    return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
}
export function registerMailTools(server, client) {
    server.tool("mail_list_folders", "List email folders with unread counts", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
    }, async ({ account_id }) => {
        const data = await client.get("/api/v1/mail/folders", client.accountId(account_id));
        return json(data);
    });
    server.tool("mail_create_folder", "Create a new mail folder. Idempotent: creating an existing mailbox returns success with created=false. Optional parent nests the folder using the server hierarchy delimiter.", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        name: z.string().describe("Mailbox/folder name to create"),
        parent: z.string().optional().describe("Optional parent folder for nesting (e.g. parent<delim>name)"),
    }, async ({ account_id, name, parent }) => {
        const data = await client.post("/api/v1/mail/folders", {
            ...client.accountId(account_id),
            name,
            parent,
        });
        return json(data);
    });
    server.tool("mail_rename_folder", "Rename a mail folder. System folders (INBOX, Sent, Drafts, Trash, Junk, Bookings) cannot be renamed.", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        folder_id: z.string().describe("Current folder name"),
        name: z.string().describe("New folder name"),
    }, async ({ account_id, folder_id, name }) => {
        const data = await client.post(`/api/v1/mail/folders/${encodeURIComponent(folder_id)}/rename`, { ...client.accountId(account_id), name });
        return json(data);
    });
    server.tool("mail_delete_folder", "Delete a mail folder. System folders and folders marked protected cannot be deleted.", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        folder_id: z.string().describe("Folder name to delete"),
    }, async ({ account_id, folder_id }) => {
        const data = await client.delete(`/api/v1/mail/folders/${encodeURIComponent(folder_id)}`, client.accountId(account_id));
        return json(data);
    });
    server.tool("mail_list_messages", "List messages in a folder with pagination and optional filters", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        folder_id: z.string().describe("Folder ID to list messages from"),
        cursor: z.string().optional().describe("Pagination cursor from previous response"),
        max_results: z.number().min(1).max(200).optional().describe("Maximum number of messages to return (1–200, default 50)"),
        unread: z.boolean().optional().describe("Filter to unread messages only"),
        flagged: z.boolean().optional().describe("Filter to flagged messages only"),
        since: z.string().optional().describe("ISO 8601 datetime — only messages after this time"),
    }, async ({ account_id, folder_id, cursor, max_results, unread, flagged, since }) => {
        const data = await client.get(`/api/v1/mail/folders/${encodeURIComponent(folder_id)}/messages`, { ...client.accountId(account_id), cursor, max_results: max_results?.toString(), unread: unread?.toString(), flagged: flagged?.toString(), since });
        return json(data);
    });
    server.tool("mail_get_message", "Get full message content including body and attachments list", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        message_id: z.string().describe("Message ID"),
    }, async ({ account_id, message_id }) => {
        const data = await client.get(`/api/v1/mail/messages/${encodeURIComponent(message_id)}`, client.accountId(account_id));
        return json(data);
    });
    server.tool("mail_search", "Search messages with query string and optional filters", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        q: z.string().describe("Search query string"),
        folder_id: z.string().optional().describe("Restrict search to a specific folder"),
        unread: z.boolean().optional().describe("Filter to unread messages"),
        flagged: z.boolean().optional().describe("Filter to flagged messages"),
        since: z.string().optional().describe("ISO 8601 datetime — only messages after this time"),
        until: z.string().optional().describe("ISO 8601 datetime — only messages before this time"),
        cursor: z.string().optional().describe("Pagination cursor from previous response"),
        max_results: z.number().min(1).max(200).optional().describe("Maximum number of results to return (1–200, default 50)"),
    }, async ({ account_id, q, folder_id, unread, flagged, since, until, cursor, max_results }) => {
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
    server.tool("mail_send", "Compose and send a new email", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        to: z.array(z.string()).describe("Recipient email addresses"),
        cc: z.array(z.string()).optional().describe("CC recipients"),
        bcc: z.array(z.string()).optional().describe("BCC recipients"),
        subject: z.string().describe("Email subject"),
        body_plain: z.string().optional().describe("Plain text body"),
        body_html: z.string().optional().describe("HTML body"),
        draft_id: z.string().optional().describe("Draft ID to delete after sending"),
    }, async ({ account_id, to, cc, bcc, subject, body_plain, body_html, draft_id }) => {
        const data = await client.post("/api/v1/mail/messages", {
            ...client.accountId(account_id),
            to,
            cc,
            bcc,
            subject,
            body_plain,
            body_html,
            draft_id,
        });
        return json(data);
    });
    server.tool("mail_move_message", "Move a message to a different folder", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        message_id: z.string().describe("Message ID to move"),
        folder_id: z.string().describe("Destination folder ID"),
    }, async ({ account_id, message_id, folder_id }) => {
        const data = await client.post(`/api/v1/mail/messages/${encodeURIComponent(message_id)}/move`, { ...client.accountId(account_id), folder_id });
        return json(data);
    });
    server.tool("mail_delete_message", "Move a message to Trash", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        message_id: z.string().describe("Message ID to delete"),
    }, async ({ account_id, message_id }) => {
        const data = await client.delete(`/api/v1/mail/messages/${encodeURIComponent(message_id)}`, client.accountId(account_id));
        return json(data);
    });
    server.tool("mail_update_flags", "Update read/flagged/locked status of a message", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        message_id: z.string().describe("Message ID"),
        read: z.boolean().optional().describe("Mark as read/unread"),
        flagged: z.boolean().optional().describe("Mark as flagged/unflagged"),
        locked: z.boolean().optional().describe("Toggle delete-protection lock ($Locked) on the message"),
    }, async ({ account_id, message_id, read, flagged, locked }) => {
        const flags = {};
        if (read !== undefined)
            flags.read = read;
        if (flagged !== undefined)
            flags.flagged = flagged;
        if (locked !== undefined)
            flags.locked = locked;
        const data = await client.patch(`/api/v1/mail/messages/${encodeURIComponent(message_id)}`, { ...client.accountId(account_id), flags });
        return json(data);
    });
    server.tool("mail_get_attachment", "Download a message attachment (returns metadata with download URL)", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        message_id: z.string().describe("Message ID"),
        attachment_id: z.string().describe("Attachment ID"),
    }, async ({ account_id, message_id, attachment_id }) => {
        const data = await client.get(`/api/v1/mail/messages/${encodeURIComponent(message_id)}/attachments/${encodeURIComponent(attachment_id)}`, client.accountId(account_id));
        return json(data);
    });
    server.tool("mail_view_attachment", "Convert a pandoc-supported attachment to HTML for viewing", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        message_id: z.string().describe("Message ID"),
        attachment_id: z.string().describe("Attachment ID"),
    }, async ({ account_id, message_id, attachment_id }) => {
        const data = await client.get(`/api/v1/mail/messages/${encodeURIComponent(message_id)}/attachments/${encodeURIComponent(attachment_id)}/view`, client.accountId(account_id));
        return json(data);
    });
    server.tool("mail_get_thread", "Get all messages in a conversation thread", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        thread_id: z.string().describe("Thread ID"),
    }, async ({ account_id, thread_id }) => {
        const data = await client.get(`/api/v1/mail/threads/${encodeURIComponent(thread_id)}`, client.accountId(account_id));
        return json(data);
    });
    server.tool("mail_bulk_move", "Move multiple messages to a different folder", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        folder_id: z.string().describe("Destination folder ID"),
        items: z.array(z.object({
            message_id: z.string().describe("Message ID"),
        })).describe("Array of messages to move").max(100),
    }, async ({ account_id, folder_id, items }) => {
        const data = await client.post("/api/v1/mail/bulk/move", {
            ...client.accountId(account_id),
            folder_id,
            items,
        });
        return json(data);
    });
    server.tool("mail_bulk_delete", "Delete multiple messages (move to Trash)", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        items: z.array(z.object({
            message_id: z.string().describe("Message ID"),
        })).describe("Array of messages to delete").max(100),
    }, async ({ account_id, items }) => {
        const data = await client.post("/api/v1/mail/bulk/delete", {
            ...client.accountId(account_id),
            items,
        });
        return json(data);
    });
    server.tool("mail_bulk_flag", "Update flags on multiple messages", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        items: z.array(z.object({
            message_id: z.string().describe("Message ID"),
            flags: z.object({
                read: z.boolean().optional(),
                flagged: z.boolean().optional(),
                locked: z.boolean().optional(),
            }).describe("Flags to set"),
        })).describe("Array of message-flag updates").max(100),
    }, async ({ account_id, items }) => {
        const data = await client.post("/api/v1/mail/bulk/flag", {
            ...client.accountId(account_id),
            items,
        });
        return json(data);
    });
    server.tool("mail_save_draft", "Save an email as a draft in the Drafts folder", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        to: z.array(z.string()).optional().describe("Recipient email addresses"),
        cc: z.array(z.string()).optional().describe("CC recipients"),
        bcc: z.array(z.string()).optional().describe("BCC recipients"),
        subject: z.string().optional().describe("Email subject"),
        body_plain: z.string().optional().describe("Plain text body"),
        body_html: z.string().optional().describe("HTML body"),
        replace_uid: z.string().optional().describe("UID of an existing draft to replace"),
    }, async ({ account_id, to, cc, bcc, subject, body_plain, body_html, replace_uid }) => {
        const data = await client.post("/api/v1/mail/drafts", {
            ...client.accountId(account_id),
            to,
            cc,
            bcc,
            subject,
            body_plain,
            body_html,
            replace_uid,
        });
        return json(data);
    });
    server.tool("mail_delete_draft", "Delete a draft email by UID", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        draft_uid: z.string().describe("Draft UID to delete"),
    }, async ({ account_id, draft_uid }) => {
        const data = await client.delete(`/api/v1/mail/drafts/${encodeURIComponent(draft_uid)}`, client.accountId(account_id));
        return json(data);
    });
    server.tool("mail_get_raw_message", "Get the raw RFC 822 source of an email message as plain text", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        message_id: z.string().describe("Message ID"),
    }, async ({ account_id, message_id }) => {
        const data = await client.get(`/api/v1/mail/messages/${encodeURIComponent(message_id)}/raw`, client.accountId(account_id));
        return json(data);
    });
}
//# sourceMappingURL=mail.js.map