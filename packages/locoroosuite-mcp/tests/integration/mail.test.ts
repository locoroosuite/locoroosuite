import { describe, it, expect, beforeAll } from "vitest";
import { isServerAvailable, createClient, assertSuccess } from "./helpers.js";
import { ApiError } from "../../src/client.js";
import type { ApiClient } from "../../src/client.js";

describe.skipIf(!(await isServerAvailable()))("MCP Client → API: Mail", () => {
  let client: ApiClient;
  let accountId: string;

  beforeAll(async () => {
    client = createClient();
    const accountsRes = await client.get("/api/v1/accounts") as { data: Array<{ id: number; email: string }> };
    const accounts = accountsRes.data!;
    expect(accounts.length).toBeGreaterThan(0);
    accountId = String(accounts[0].id);
    client = createClient(accountId);
  });

  describe("mail_list_folders", () => {
    it("returns folders array with required fields", async () => {
      const res = await client.get("/api/v1/mail/folders");
      const r = assertSuccess<Array<{ id: string; name: string; unread_count: number }>>(res, "list_folders");
      expect(Array.isArray(r.data)).toBe(true);
      if (r.data!.length > 0) {
        const f = r.data![0];
        expect(f).toHaveProperty("id");
        expect(f).toHaveProperty("name");
        expect(f).toHaveProperty("unread_count");
      }
    });
  });

  describe("mail_list_messages", () => {
    it("returns paginated messages with all required fields", async () => {
      const res = await client.get("/api/v1/mail/folders/INBOX/messages", { max_results: "5" });
      const r = assertSuccess<Array<Record<string, unknown>>>(res, "list_messages");
      expect(Array.isArray(r.data)).toBe(true);
      expect(r).toHaveProperty("pagination");
      expect(r.pagination).toHaveProperty("has_more");
      expect(r.pagination).toHaveProperty("next_cursor");
      if (r.data!.length > 0) {
        const msg = r.data![0];
        for (const key of ["id", "folder", "subject", "from", "to", "date", "flags", "snippet", "thread_id", "unread", "flagged"]) {
          expect(msg).toHaveProperty(key);
        }
      }
    });

    it("supports cursor pagination", async () => {
      const page1 = await client.get("/api/v1/mail/folders/Sent/messages", { max_results: "2" }) as Record<string, unknown>;
      const p1 = assertSuccess<Array<Record<string, unknown>>>(page1, "page1");
      if (!p1.pagination?.next_cursor) return;
      const page2 = await client.get("/api/v1/mail/folders/Sent/messages", { max_results: "2", cursor: p1.pagination.next_cursor }) as Record<string, unknown>;
      const p2 = assertSuccess<Array<Record<string, unknown>>>(page2, "page2");
      expect(p2.pagination).toHaveProperty("has_more");
      expect(Array.isArray(p2.data)).toBe(true);
    });
  });

  describe("mail_get_message", () => {
    it("returns full message detail with body_plain and body_html", async () => {
      const listRes = await client.get("/api/v1/mail/folders/Sent/messages", { max_results: "1" });
      const list = assertSuccess<Array<{ id: number }>>(listRes, "list_for_get");
      if (list.data!.length === 0) return;
      const msgId = String(list.data![0].id);
      const res = await client.get(`/api/v1/mail/messages/${msgId}`);
      const r = assertSuccess<Record<string, unknown>>(res, "get_message");
      const msg = r.data!;
      for (const key of ["id", "folder", "subject", "from", "to", "date", "flags", "snippet", "thread_id", "unread", "flagged", "body_plain", "body_html"]) {
        expect(msg).toHaveProperty(key);
      }
      expect(typeof msg["body_plain"]).toBe("string");
      expect(typeof msg["body_html"]).toBe("string");
    });

    it("returns 404 for non-existent message", async () => {
      try {
        await client.get("/api/v1/mail/messages/999999");
        expect.unreachable("Should have thrown");
      } catch (e) {
        expect(e).toBeInstanceOf(ApiError);
        expect((e as ApiError).code).toBe("NOT_FOUND");
      }
    });
  });

  describe("mail_get_raw_message", () => {
    it("returns JSON with plain text raw email", async () => {
      const listRes = await client.get("/api/v1/mail/folders/Sent/messages", { max_results: "1" });
      const list = assertSuccess<Array<{ id: number }>>(listRes, "list_for_raw");
      if (list.data!.length === 0) return;
      const msgId = String(list.data![0].id);
      const res = await client.get(`/api/v1/mail/messages/${msgId}/raw`);
      const r = assertSuccess<Record<string, unknown>>(res, "get_raw");
      expect(r.data).toHaveProperty("mime_type", "message/rfc822");
      expect(r.data).toHaveProperty("data");
      expect(typeof r.data!["data"]).toBe("string");
      expect(r.data).not.toHaveProperty("encoding");
    });
  });

  describe("mail_search", () => {
    it("requires query parameter", async () => {
      try {
        await client.get("/api/v1/mail/search");
        expect.unreachable("Should have thrown");
      } catch (e) {
        expect(e).toBeInstanceOf(ApiError);
        expect((e as ApiError).code).toBe("VALIDATION_ERROR");
      }
    });

    it("returns results for a valid query", async () => {
      const res = await client.get("/api/v1/mail/search", { q: "test" });
      const r = assertSuccess<Array<Record<string, unknown>>>(res, "search");
      expect(Array.isArray(r.data)).toBe(true);
    });
  });

  describe("mail_get_thread", () => {
    it("returns thread messages with body_plain and body_html", async () => {
      const listRes = await client.get("/api/v1/mail/folders/Sent/messages", { max_results: "10" });
      const list = assertSuccess<Array<{ id: number; thread_id: string | null }>>(listRes, "list_for_thread");
      const withThread = list.data!.filter((m) => m.thread_id !== null);
      if (withThread.length === 0) return;
      const threadId = withThread[0].thread_id;
      const res = await client.get(`/api/v1/mail/threads/${threadId}`);
      const r = assertSuccess<Array<Record<string, unknown>>>(res, "get_thread");
      expect(Array.isArray(r.data)).toBe(true);
      if (r.data!.length > 0) {
        const msg = r.data![0];
        for (const key of ["id", "subject", "from", "to", "body_plain", "body_html"]) {
          expect(msg).toHaveProperty(key);
        }
      }
    });
  });

  describe("mail_update_flags", () => {
    it("sends account_id in body and it is respected", async () => {
      const listRes = await client.get("/api/v1/mail/folders/Sent/messages", { max_results: "1" });
      const list = assertSuccess<Array<{ id: number; unread: boolean }>>(listRes, "list_for_flags");
      if (list.data!.length === 0) return;
      const msgId = list.data![0].id;
      const res = await client.patch(`/api/v1/mail/messages/${msgId}`, {
        account_id: accountId,
        flags: { read: true },
      });
      const r = assertSuccess<Record<string, unknown>>(res, "update_flags");
      expect(r.data).toHaveProperty("id");
    });
  });

  describe("account_id in body for write operations", () => {
    it("account_id in PATCH body is not ignored", async () => {
      const res = await client.get("/api/v1/mail/folders/Sent/messages", { max_results: "1" });
      const list = assertSuccess<Array<{ id: number }>>(res, "list_for_body_account");
      if (list.data!.length === 0) return;
      const msgId = list.data![0].id;
      const patchRes = await client.patch(`/api/v1/mail/messages/${msgId}`, {
        account_id: accountId,
        flags: { flagged: true },
      });
      assertSuccess(patchRes, "patch_with_body_account_id");
    });
  });

  describe("folder management", () => {
    const unique = () => `MCP-Test-${Date.now()}`;

    it("creates a folder idempotently and it appears in list_folders", async () => {
      const name = unique();
      const createRes = await client.post("/api/v1/mail/folders", { name });
      const created = assertSuccess<{ id: string; name: string; created: boolean }>(createRes, "create_folder");
      expect(created.data!.name).toBe(name);
      expect(created.data!.created).toBe(true);

      const againRes = await client.post("/api/v1/mail/folders", { name });
      const again = assertSuccess<{ created: boolean }>(againRes, "create_folder_idempotent");
      expect(again.data!.created).toBe(false);

      const listRes = await client.get("/api/v1/mail/folders");
      const list = assertSuccess<Array<{ name: string }>>(listRes, "list_after_create");
      expect(list.data!.map((f) => f.name)).toContain(name);

      const delRes = await client.delete(`/api/v1/mail/folders/${encodeURIComponent(name)}`);
      const deleted = assertSuccess<{ id: string; deleted: boolean }>(delRes, "delete_folder");
      expect(deleted.data!.deleted).toBe(true);
    });

    it("refuses to delete a system folder", async () => {
      try {
        await client.delete("/api/v1/mail/folders/INBOX");
        expect.unreachable("Should have thrown");
      } catch (e) {
        expect(e).toBeInstanceOf(ApiError);
        expect((e as ApiError).code).toBe("PROTECTED");
      }
    });

    it("refuses to rename a system folder", async () => {
      try {
        await client.post("/api/v1/mail/folders/INBOX/rename", { name: "Other" });
        expect.unreachable("Should have thrown");
      } catch (e) {
        expect(e).toBeInstanceOf(ApiError);
        expect((e as ApiError).code).toBe("PROTECTED");
      }
    });
  });
});
