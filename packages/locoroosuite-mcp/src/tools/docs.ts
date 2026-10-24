import type { McpServer } from "@modelcontextprotocol/server";
import { z } from "zod";
import type { ApiClient } from "../client.js";

function json(data: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
}

export function registerDocsTools(server: McpServer, client: ApiClient) {
  server.registerTool("docs_list_documents", { description: "List documents with optional type, folder, tag, and name filters", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              type: z.enum(["odt", "ods", "odp"]).optional().describe("Filter by document type"),
              search: z.string().optional().describe("Search by document name"),
              folder: z.string().optional().describe("Filter to documents directly in this folder path (exact match)"),
              tag: z.string().optional().describe("Filter to documents carrying this tag"),
              cursor: z.string().optional().describe("Pagination cursor from previous response"),
              max_results: z.number().min(1).max(200).optional().describe("Maximum number of documents to return (1–200, default 50)"),
            }) }, async ({ account_id, type, search, folder, tag, cursor, max_results }) => {
              const data = await client.get("/api/v1/docs/documents", {
                ...client.accountId(account_id),
                type,
                search,
                folder,
                tag,
                cursor,
                max_results: max_results?.toString(),
              });
              return json(data);
            });

  server.registerTool("docs_get_document", { description: "Get document metadata (name, type, size, dates)", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Document ID"),
            }) }, async ({ account_id, document_id }) => {
              const data = await client.get(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}`,
                client.accountId(account_id),
              );
              return json(data);
            });

  server.registerTool("docs_create_document", { description: "Create a new empty document", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              name: z.string().describe("Document name"),
              type: z.enum(["odt", "ods", "odp"]).describe("Document type"),
              folder: z.string().optional().describe("Folder path to create the document in (empty/omitted = root)"),
            }) }, async ({ account_id, name, type, folder }) => {
              const data = await client.post("/api/v1/docs/documents", {
                ...client.accountId(account_id),
                name,
                type,
                ...(folder ? { folder } : {}),
              });
              return json(data);
            });

  server.registerTool("docs_rename_document", { description: "Rename a document", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Document ID"),
              name: z.string().describe("New document name"),
            }) }, async ({ account_id, document_id, name }) => {
              const data = await client.put(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}`,
                { ...client.accountId(account_id), name },
              );
              return json(data);
            });

  server.registerTool("docs_delete_document", { description: "Soft-delete a document (moves to trash)", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Document ID"),
            }) }, async ({ account_id, document_id }) => {
              const data = await client.delete(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}`,
                client.accountId(account_id) as Record<string, string> | undefined,
              );
              return json(data);
            });

  server.registerTool("docs_download_document", { description: "Download document file in ODF format (returns metadata with download info)", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Document ID"),
            }) }, async ({ account_id, document_id }) => {
              const data = await client.get(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}/download`,
                client.accountId(account_id),
              );
              return json(data);
            });

  server.registerTool("docs_upload_document", { description: "Upload a file as a new document (note: MCP does not support file uploads — use the REST API directly)", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            }) }, async ({ account_id }) => {
              return {
                content: [{
                  type: "text" as const,
                  text: "File uploads are not supported via MCP. Use POST /api/v1/docs/documents/upload directly with multipart/form-data.",
                }],
                isError: true,
              };
            });

  server.registerTool("docs_read_content", { description: "Read document content as plain text or markdown", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Document ID"),
              format: z.enum(["text", "markdown"]).optional().describe("Output format (default: text). Use markdown for AI agents."),
            }) }, async ({ account_id, document_id, format }) => {
              const data = await client.get(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}/content`,
                {
                  ...client.accountId(account_id),
                  format: format || "markdown",
                },
              );
              return json(data);
            });

  server.registerTool("docs_update_content", { description: "Replace document content using markdown (primary mode for AI agents)", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Document ID"),
              content: z.string().describe("New document content in markdown format"),
            }) }, async ({ account_id, document_id, content }) => {
              const data = await client.put(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}/content`,
                {
                  ...client.accountId(account_id),
                  content,
                  format: "markdown",
                },
              );
              return json(data);
            });

  server.registerTool("docs_create_draft", { description: "Create a draft copy of a document with AI-modified content", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Source document ID"),
              content: z.string().describe("Modified content in markdown format"),
              summary: z.string().optional().describe("Brief description of changes"),
            }) }, async ({ account_id, document_id, content, summary }) => {
              const data = await client.post(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}/drafts`,
                {
                  ...client.accountId(account_id),
                  content,
                  format: "markdown",
                  summary,
                },
              );
              return json(data);
            });

  server.registerTool("docs_list_drafts", { description: "List pending drafts for a document", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Source document ID"),
            }) }, async ({ account_id, document_id }) => {
              const data = await client.get(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}/drafts`,
                client.accountId(account_id),
              );
              return json(data);
            });

  server.registerTool("docs_apply_draft", { description: "Accept a draft — replaces the original document's content with the draft", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Source document ID"),
              draft_id: z.string().describe("Draft ID to apply"),
            }) }, async ({ account_id, document_id, draft_id }) => {
              const data = await client.post(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}/drafts/${encodeURIComponent(draft_id)}/apply`,
                client.accountId(account_id),
              );
              return json(data);
            });

  server.registerTool("docs_discard_draft", { description: "Discard a draft — deletes it without affecting the original", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Source document ID"),
              draft_id: z.string().describe("Draft ID to discard"),
            }) }, async ({ account_id, document_id, draft_id }) => {
              const data = await client.delete(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}/drafts/${encodeURIComponent(draft_id)}`,
                client.accountId(account_id) as Record<string, string> | undefined,
              );
              return json(data);
            });

  server.registerTool("docs_export_pdf", { description: "Export document as PDF (returns metadata with download info)", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Document ID"),
            }) }, async ({ account_id, document_id }) => {
              const data = await client.get(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}/download/pdf`,
                client.accountId(account_id),
              );
              return json(data);
            });

  server.registerTool("docs_convert_document", { description: "Convert a non-ODF document (PDF, DOCX, etc.) to an editable ODF document. The original is preserved.", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Document ID to convert"),
            }) }, async ({ account_id, document_id }) => {
              const data = await client.post(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}/convert`,
                client.accountId(account_id),
              );
              return json(data);
            });

  // ---------------------------------------------------------------------
  // Folders
  // ---------------------------------------------------------------------

  server.registerTool("docs_list_folders", { description: "List all folders (explicit rows plus paths inferred from documents)", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            }) }, async ({ account_id }) => {
              const data = await client.get("/api/v1/docs/folders", client.accountId(account_id));
              return json(data);
            });

  server.registerTool("docs_create_folder", { description: "Create a folder (and any missing ancestors). Idempotent.", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              name: z.string().describe("Folder name (leaf segment)"),
              parent: z.string().optional().describe("Parent folder path (empty/omitted = top-level)"),
            }) }, async ({ account_id, name, parent }) => {
              const data = await client.post("/api/v1/docs/folders", {
                ...client.accountId(account_id),
                name,
                ...(parent ? { parent } : {}),
              });
              return json(data);
            });

  server.registerTool("docs_rename_folder", { description: "Rename a folder and its entire subtree", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              path: z.string().describe("Existing folder path to rename"),
              name: z.string().describe("New leaf folder name"),
            }) }, async ({ account_id, path, name }) => {
              const data = await client.post("/api/v1/docs/folders/rename", {
                ...client.accountId(account_id),
                path,
                name,
              });
              return json(data);
            });

  server.registerTool("docs_delete_folder", { description: "Delete a folder subtree. Contained documents move to the deleted folder's parent.", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              path: z.string().describe("Folder path to delete"),
            }) }, async ({ account_id, path }) => {
              const data = await client.post("/api/v1/docs/folders/delete", {
                ...client.accountId(account_id),
                path,
              });
              return json(data);
            });

  server.registerTool("docs_move_document", { description: "Move a document to a folder (empty/omitted folder = root)", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Document ID to move"),
              folder: z.string().optional().describe("Target folder path (empty/omitted = root)"),
            }) }, async ({ account_id, document_id, folder }) => {
              const data = await client.post(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}/move`,
                {
                  ...client.accountId(account_id),
                  ...(folder ? { folder } : {}),
                },
              );
              return json(data);
            });

  // ---------------------------------------------------------------------
  // Tags
  // ---------------------------------------------------------------------

  server.registerTool("docs_get_tags", { description: "Get the tags applied to a document", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Document ID"),
            }) }, async ({ account_id, document_id }) => {
              const data = await client.get(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}/tags`,
                client.accountId(account_id),
              );
              return json(data);
            });

  server.registerTool("docs_update_tags", { description: "Add/remove tags on a document, or replace the full tag list with `set` (each tag max 50 chars)", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
              document_id: z.string().describe("Document ID to tag"),
              add: z.array(z.string()).optional().describe("Tags to add"),
              remove: z.array(z.string()).optional().describe("Tags to remove"),
              set: z.array(z.string()).optional().describe("Replace the full tag list with this list (takes precedence over add/remove)"),
            }) }, async ({ account_id, document_id, add, remove, set }) => {
              const data = await client.put(
                `/api/v1/docs/documents/${encodeURIComponent(document_id)}/tags`,
                {
                  ...client.accountId(account_id),
                  ...(add ? { add } : {}),
                  ...(remove ? { remove } : {}),
                  ...(set ? { set } : {}),
                },
              );
              return json(data);
            });

  server.registerTool("docs_list_tags", { description: "List the distinct tags in use across the account's active documents (sorted, case-insensitive)", inputSchema: z.object({
              account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
            }) }, async ({ account_id }) => {
              const data = await client.get("/api/v1/docs/tags", client.accountId(account_id));
              return json(data);
            });
}
