import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import type { ApiClient } from "../client.js";

function json(data: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
}

export function registerContactsTools(server: McpServer, client: ApiClient) {
  server.tool(
    "contacts_list",
    "List contacts in the user's address book. Read-only: returns contact objects (name, email, phone, organization) without modifying any data.",
    {
      account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
      q: z.string().optional().describe("Search query to filter contacts by name or email"),
      sort: z.enum(["name", "email"]).optional().describe("Sort order: 'name' or 'email'"),
      cursor: z.string().optional().describe("Pagination cursor from previous response"),
      max_results: z.number().min(1).max(200).optional().describe("Maximum number of contacts to return (1–200, default 50)"),
    },
    async ({ account_id, q, sort, cursor, max_results }) => {
      const data = await client.get("/api/v1/contacts", {
        ...client.accountId(account_id),
        q,
        sort,
        cursor,
        max_results: max_results?.toString(),
      });
      return json(data);
    },
  );

  server.tool(
    "contacts_get",
    "Get full contact detail including all vCard fields. Read-only.",
    {
      account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
      contact_id: z.string().describe("Contact ID"),
    },
    async ({ account_id, contact_id }) => {
      const data = await client.get(
        `/api/v1/contacts/${encodeURIComponent(contact_id)}`,
        client.accountId(account_id),
      );
      return json(data);
    },
  );

  server.tool(
    "contacts_search",
    "Search contacts by name, email, or phone. Read-only: returns name and email pairs without modifying any data.",
    {
      account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
      q: z.string().describe("Search query string"),
      cursor: z.string().optional().describe("Pagination cursor from previous response"),
      max_results: z.number().min(1).max(200).optional().describe("Maximum number of results to return (1–200, default 50)"),
    },
    async ({ account_id, q, cursor, max_results }) => {
      const data = await client.get("/api/v1/contacts/search", {
        ...client.accountId(account_id),
        q,
        cursor,
        max_results: max_results?.toString(),
      });
      return json(data);
    },
  );

  server.tool(
    "contacts_create",
    "Create a new contact",
    {
      account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
      fn: z.string().describe("Full name"),
      email_work: z.string().optional().describe("Work email"),
      email_home: z.string().optional().describe("Home email"),
      phone_work: z.string().optional().describe("Work phone"),
      phone_cell: z.string().optional().describe("Cell phone"),
      phone_home: z.string().optional().describe("Home phone"),
      organization: z.string().optional().describe("Organization/company"),
      title: z.string().optional().describe("Job title"),
      note: z.string().optional().describe("Notes"),
    },
    async ({ account_id, fn, email_work, email_home, phone_work, phone_cell, phone_home, organization, title, note }) => {
      const data = await client.post("/api/v1/contacts", {
        ...client.accountId(account_id),
        fn,
        email_work,
        email_home,
        phone_work,
        phone_cell,
        phone_home,
        organization,
        title,
        note,
      });
      return json(data);
    },
  );

  server.tool(
    "contacts_update",
    "Update an existing contact",
    {
      account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
      contact_id: z.string().describe("Contact ID"),
      fn: z.string().optional().describe("Full name"),
      email_work: z.string().optional().describe("Work email"),
      email_home: z.string().optional().describe("Home email"),
      phone_work: z.string().optional().describe("Work phone"),
      phone_cell: z.string().optional().describe("Cell phone"),
      phone_home: z.string().optional().describe("Home phone"),
      organization: z.string().optional().describe("Organization/company"),
      title: z.string().optional().describe("Job title"),
      note: z.string().optional().describe("Notes"),
    },
    async ({ account_id, contact_id, fn, email_work, email_home, phone_work, phone_cell, phone_home, organization, title, note }) => {
      const body: Record<string, unknown> = { ...client.accountId(account_id) };
      if (fn !== undefined) body.fn = fn;
      if (email_work !== undefined) body.email_work = email_work;
      if (email_home !== undefined) body.email_home = email_home;
      if (phone_work !== undefined) body.phone_work = phone_work;
      if (phone_cell !== undefined) body.phone_cell = phone_cell;
      if (phone_home !== undefined) body.phone_home = phone_home;
      if (organization !== undefined) body.organization = organization;
      if (title !== undefined) body.title = title;
      if (note !== undefined) body.note = note;
      const data = await client.put(
        `/api/v1/contacts/${encodeURIComponent(contact_id)}`,
        body,
      );
      return json(data);
    },
  );

  server.tool(
    "contacts_delete",
    "Delete a contact",
    {
      account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
      contact_id: z.string().describe("Contact ID"),
    },
    async ({ account_id, contact_id }) => {
      const data = await client.delete(
        `/api/v1/contacts/${encodeURIComponent(contact_id)}`,
        client.accountId(account_id) as Record<string, string> | undefined,
      );
      return json(data);
    },
  );

  server.tool(
    "contacts_bulk_delete",
    "Delete multiple contacts",
    {
      account_id: z.string().optional().describe("Account ID (uses default if omitted)"),
      items: z.array(z.object({
        contact_id: z.string().describe("Contact ID"),
      })).describe("Array of contacts to delete").max(100),
    },
    async ({ account_id, items }) => {
      const data = await client.post("/api/v1/contacts/bulk/delete", {
        ...client.accountId(account_id),
        items,
      });
      return json(data);
    },
  );
}
