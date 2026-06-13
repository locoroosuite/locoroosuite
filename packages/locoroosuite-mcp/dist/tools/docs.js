import { z } from "zod";
function json(data) {
    return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
}
export function registerDocsTools(server, client) {
    server.tool("docs_list_documents", "List documents with optional type filter and search", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        type: z.enum(["odt", "ods", "odp"]).optional().describe("Filter by document type"),
        search: z.string().optional().describe("Search by document name"),
        cursor: z.string().optional().describe("Pagination cursor from previous response"),
        max_results: z.number().min(1).max(200).optional().describe("Maximum number of documents to return (1–200, default 50)"),
    }, async ({ account_id, type, search, cursor, max_results }) => {
        const data = await client.get("/api/v1/docs/documents", {
            ...client.accountId(account_id),
            type,
            search,
            cursor,
            max_results: max_results?.toString(),
        });
        return json(data);
    });
    server.tool("docs_get_document", "Get document metadata (name, type, size, dates)", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        document_id: z.string().describe("Document ID"),
    }, async ({ account_id, document_id }) => {
        const data = await client.get(`/api/v1/docs/documents/${encodeURIComponent(document_id)}`, client.accountId(account_id));
        return json(data);
    });
    server.tool("docs_create_document", "Create a new empty document", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        name: z.string().describe("Document name"),
        type: z.enum(["odt", "ods", "odp"]).describe("Document type"),
    }, async ({ account_id, name, type }) => {
        const data = await client.post("/api/v1/docs/documents", {
            ...client.accountId(account_id),
            name,
            type,
        });
        return json(data);
    });
    server.tool("docs_rename_document", "Rename a document", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        document_id: z.string().describe("Document ID"),
        name: z.string().describe("New document name"),
    }, async ({ account_id, document_id, name }) => {
        const data = await client.put(`/api/v1/docs/documents/${encodeURIComponent(document_id)}`, { ...client.accountId(account_id), name });
        return json(data);
    });
    server.tool("docs_delete_document", "Soft-delete a document (moves to trash)", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        document_id: z.string().describe("Document ID"),
    }, async ({ account_id, document_id }) => {
        const data = await client.delete(`/api/v1/docs/documents/${encodeURIComponent(document_id)}`, client.accountId(account_id));
        return json(data);
    });
    server.tool("docs_download_document", "Download document file in ODF format (returns metadata with download info)", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        document_id: z.string().describe("Document ID"),
    }, async ({ account_id, document_id }) => {
        const data = await client.get(`/api/v1/docs/documents/${encodeURIComponent(document_id)}/download`, client.accountId(account_id));
        return json(data);
    });
    server.tool("docs_upload_document", "Upload a file as a new document (note: MCP does not support file uploads — use the REST API directly)", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
    }, async ({ account_id }) => {
        return {
            content: [{
                    type: "text",
                    text: "File uploads are not supported via MCP. Use POST /api/v1/docs/documents/upload directly with multipart/form-data.",
                }],
            isError: true,
        };
    });
    server.tool("docs_read_content", "Read document content as plain text or markdown", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        document_id: z.string().describe("Document ID"),
        format: z.enum(["text", "markdown"]).optional().describe("Output format (default: text). Use markdown for AI agents."),
    }, async ({ account_id, document_id, format }) => {
        const data = await client.get(`/api/v1/docs/documents/${encodeURIComponent(document_id)}/content`, {
            ...client.accountId(account_id),
            format: format || "markdown",
        });
        return json(data);
    });
    server.tool("docs_update_content", "Replace document content using markdown (primary mode for AI agents)", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        document_id: z.string().describe("Document ID"),
        content: z.string().describe("New document content in markdown format"),
    }, async ({ account_id, document_id, content }) => {
        const data = await client.put(`/api/v1/docs/documents/${encodeURIComponent(document_id)}/content`, {
            ...client.accountId(account_id),
            content,
            format: "markdown",
        });
        return json(data);
    });
    server.tool("docs_create_draft", "Create a draft copy of a document with AI-modified content", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        document_id: z.string().describe("Source document ID"),
        content: z.string().describe("Modified content in markdown format"),
        summary: z.string().optional().describe("Brief description of changes"),
    }, async ({ account_id, document_id, content, summary }) => {
        const data = await client.post(`/api/v1/docs/documents/${encodeURIComponent(document_id)}/drafts`, {
            ...client.accountId(account_id),
            content,
            format: "markdown",
            summary,
        });
        return json(data);
    });
    server.tool("docs_list_drafts", "List pending drafts for a document", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        document_id: z.string().describe("Source document ID"),
    }, async ({ account_id, document_id }) => {
        const data = await client.get(`/api/v1/docs/documents/${encodeURIComponent(document_id)}/drafts`, client.accountId(account_id));
        return json(data);
    });
    server.tool("docs_apply_draft", "Accept a draft — replaces the original document's content with the draft", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        document_id: z.string().describe("Source document ID"),
        draft_id: z.string().describe("Draft ID to apply"),
    }, async ({ account_id, document_id, draft_id }) => {
        const data = await client.post(`/api/v1/docs/documents/${encodeURIComponent(document_id)}/drafts/${encodeURIComponent(draft_id)}/apply`, client.accountId(account_id));
        return json(data);
    });
    server.tool("docs_discard_draft", "Discard a draft — deletes it without affecting the original", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        document_id: z.string().describe("Source document ID"),
        draft_id: z.string().describe("Draft ID to discard"),
    }, async ({ account_id, document_id, draft_id }) => {
        const data = await client.delete(`/api/v1/docs/documents/${encodeURIComponent(document_id)}/drafts/${encodeURIComponent(draft_id)}`, client.accountId(account_id));
        return json(data);
    });
    server.tool("docs_export_pdf", "Export document as PDF (returns metadata with download info)", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        document_id: z.string().describe("Document ID"),
    }, async ({ account_id, document_id }) => {
        const data = await client.get(`/api/v1/docs/documents/${encodeURIComponent(document_id)}/download/pdf`, client.accountId(account_id));
        return json(data);
    });
    server.tool("docs_convert_document", "Convert a non-ODF document (PDF, DOCX, etc.) to an editable ODF document. The original is preserved.", {
        account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
        document_id: z.string().describe("Document ID to convert"),
    }, async ({ account_id, document_id }) => {
        const data = await client.post(`/api/v1/docs/documents/${encodeURIComponent(document_id)}/convert`, client.accountId(account_id));
        return json(data);
    });
}
//# sourceMappingURL=docs.js.map