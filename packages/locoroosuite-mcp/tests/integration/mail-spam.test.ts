import { describe, it, expect, beforeAll } from "vitest";
import { isServerAvailable, createClient, assertSuccess } from "./helpers.js";
import { ApiError } from "../../src/client.js";
import type { ApiClient } from "../../src/client.js";

describe.skipIf(!(await isServerAvailable()))("MCP Client → API: Mail Spam", () => {
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

  describe("mail_report_spam", () => {
    it("reports spam then restores via mail_not_spam", async () => {
      const listRes = await client.get("/api/v1/mail/folders/INBOX/messages", { max_results: "1" });
      const list = assertSuccess<Array<{ id: number }>>(listRes, "list_for_spam");
      if (list.data!.length === 0) return; // nothing to move in this environment

      const msgId = list.data![0].id;
      const res = await client.post(`/api/v1/mail/messages/${msgId}/spam`);
      const r = assertSuccess<Record<string, unknown>>(res, "report_spam");
      const data = r.data!;
      expect(data["id"]).toBe(msgId);
      expect(typeof data["moved_to"]).toBe("string");
      expect(data["junk"]).toBe(true);

      // restore the message so the shared dev mailbox is unchanged
      const undoRes = await client.post(`/api/v1/mail/messages/${msgId}/not-spam`);
      const undo = assertSuccess<Record<string, unknown>>(undoRes, "not_spam");
      expect(undo.data!["id"]).toBe(msgId);
      expect(undo.data!["junk"]).toBe(false);
    });

    it("returns 404 for non-existent message", async () => {
      try {
        await client.post("/api/v1/mail/messages/999999999/spam");
        expect.unreachable("Should have thrown");
      } catch (e) {
        expect(e).toBeInstanceOf(ApiError);
        const err = e as ApiError;
        expect(err.status).toBe(404);
        expect(err.code).toBe("NOT_FOUND");
      }
    });
  });

  describe("mail_not_spam", () => {
    it("returns 404 for non-existent message", async () => {
      try {
        await client.post("/api/v1/mail/messages/999999999/not-spam");
        expect.unreachable("Should have thrown");
      } catch (e) {
        expect(e).toBeInstanceOf(ApiError);
        const err = e as ApiError;
        expect(err.status).toBe(404);
        expect(err.code).toBe("NOT_FOUND");
      }
    });
  });
});
