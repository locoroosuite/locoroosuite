import { describe, it, expect, beforeAll } from "vitest";
import { isServerAvailable, createClient, assertSuccess, assertItems } from "./helpers.js";
import { ApiError } from "../../src/client.js";
import type { ApiClient } from "../../src/client.js";

describe.skipIf(!(await isServerAvailable()))("MCP Client → API: Mail Spam", () => {
  let client: ApiClient;
  let accountId: string;

  beforeAll(async () => {
    client = createClient();
    const accountsRes = await client.get("/api/v1/accounts");
    const accounts = assertItems<{ id: number; email: string }>(accountsRes, "accounts");
    expect(accounts.length).toBeGreaterThan(0);
    accountId = String(accounts[0].id);
    client = createClient(accountId);
  });

  describe("mail_report_spam", () => {
    it("reports spam then restores via mail_not_spam", async () => {
      const listRes = await client.get("/api/v1/mail/folders/INBOX/messages", { max_results: "1" });
      const list = assertItems<{ id: number }>(listRes, "list_for_spam");
      if (list.length === 0) return; // nothing to move in this environment

      const msgId = list[0].id;
      try {
        const res = await client.post(`/api/v1/mail/messages/${msgId}/spam`);
        const data = assertSuccess<Record<string, unknown>>(res, "report_spam");
        expect(data["id"]).toBe(msgId);
        expect(typeof data["moved_to"]).toBe("string");
        expect(data["junk"]).toBe(true);

        // restore the message so the shared dev mailbox is unchanged
        const undoRes = await client.post(`/api/v1/mail/messages/${msgId}/not-spam`);
        const undo = assertSuccess<Record<string, unknown>>(undoRes, "not_spam");
        expect(undo["id"]).toBe(msgId);
        expect(undo["junk"]).toBe(false);
      } catch (e) {
        // U15.35a: the per-account Spam action setting gates the move; on
        // accounts with it disabled the honest outcome is 409 with the
        // setting untouched (not a silent no-op).
        if (e instanceof ApiError && e.code === "SPAM_ACTION_DISABLED") {
          expect(e.status).toBe(409);
          return;
        }
        throw e;
      }
    });

    it("returns 404 for non-existent message", async () => {
      try {
        await client.post("/api/v1/mail/messages/999999999/spam");
        expect.unreachable("Should have thrown");
      } catch (e) {
        expect(e).toBeInstanceOf(ApiError);
        const err = e as ApiError;
        // 404 when the message lookup runs first; 409 SPAM_ACTION_DISABLED
        // when the per-account Spam action setting is off on this account
        // (the gate fires before the message lookup — both are honest).
        if (err.code === "SPAM_ACTION_DISABLED") {
          expect(err.status).toBe(409);
        } else {
          expect(err.status).toBe(404);
          expect(err.code).toBe("NOT_FOUND");
        }
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
