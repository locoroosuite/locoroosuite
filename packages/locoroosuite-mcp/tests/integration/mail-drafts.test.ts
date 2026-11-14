import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { isServerAvailable, createClient, assertSuccess, assertItems } from "./helpers.js";
import { ApiError } from "../../src/client.js";
import type { ApiClient } from "../../src/client.js";

interface DraftResult {
  status: string;
  draft_uid: string;
  draft_id: string | number;
  message_id: string;
  imap_uid?: string;
  thread_id?: string | null;
  snippet?: string;
  subject?: string;
}

interface MessageSummary {
  id: number;
  imap_uid?: string;
  folder?: string;
  subject?: string;
  snippet?: string;
  unread?: boolean;
  flags?: string;
}

/** The client throws ApiError on non-2xx — the sanctioned error pattern. */
async function expectApiError(
  fn: () => Promise<unknown>,
  expectedCode: string,
  label: string,
): Promise<void> {
  try {
    await fn();
  } catch (err) {
    if (err instanceof ApiError) {
      if (err.code !== expectedCode) {
        throw new Error(`${label} expected ${expectedCode} but got ${err.code}: ${err.message}`);
      }
      return;
    }
    throw err;
  }
  throw new Error(`${label} expected ApiError ${expectedCode} but the call succeeded`);
}

describe.skipIf(!(await isServerAvailable()))("MCP Client → API: Mail drafts & threading", () => {
  let client: ApiClient;
  const createdDraftIds: string[] = [];

  beforeAll(async () => {
    client = createClient();
    const accounts = assertItems<{ id: number; email: string }>(
      await client.get("/api/v1/accounts"),
      "accounts",
    );
    expect(accounts.length).toBeGreaterThan(0);
    client = createClient(String(accounts[0].id));
  });

  afterAll(async () => {
    // Best-effort cleanup: delete every draft this suite created.
    for (const id of createdDraftIds) {
      try {
        await client.delete(`/api/v1/mail/drafts/${encodeURIComponent(id)}`);
      } catch {
        // already gone or never persisted — fine
      }
    }
  });

  describe("mail_save_draft", () => {
    it("saves a draft and returns a flat envelope with both ID spaces", async () => {
      const res = (await client.post("/api/v1/mail/drafts", {
        to: ["drafts-integration@example.com"],
        subject: "TS integration draft",
        body_plain: "draft body",
      })) as DraftResult;
      const draft = assertSuccess<DraftResult>(res, "save_draft");
      // U16.17: no .data envelope — fields are top-level
      expect(draft.status).toBe("draft");
      expect(draft.draft_uid).toBeTruthy();
      expect(draft.message_id).toBeTruthy();
      // U6.5b: the cache message id is exposed as draft_id (read tools' id space)
      expect(draft.draft_id).toBeTruthy();
      createdDraftIds.push(String(draft.draft_id));
    });

    it("replaces a draft and the old draft_id never resurrects it", async () => {
      const first = assertSuccess<DraftResult>(
        await client.post("/api/v1/mail/drafts", {
          to: ["drafts-integration@example.com"],
          subject: "v1",
          body_plain: "v1 body",
        }),
        "save_draft_v1",
      );
      createdDraftIds.push(String(first.draft_id));
      const second = assertSuccess<DraftResult>(
        await client.post("/api/v1/mail/drafts", {
          to: ["drafts-integration@example.com"],
          subject: "v2",
          body_plain: "v2 body",
          // U6.5b: replace by the cache message id read tools expose
          replace_uid: String(first.draft_id),
        }),
        "save_draft_v2",
      );
      createdDraftIds.push(String(second.draft_id));
      expect(String(second.draft_id)).not.toBe(String(first.draft_id));
      // the replaced draft is gone: deleting its old id must DRAFT_NOT_FOUND
      await expectApiError(
        () => client.delete(`/api/v1/mail/drafts/${encodeURIComponent(String(first.draft_id))}`),
        "DRAFT_NOT_FOUND",
        "delete_replaced_draft",
      );
    });

    it("rejects an unknown replace_uid with DRAFT_NOT_FOUND instead of duplicating", async () => {
      await expectApiError(
        () =>
          client.post("/api/v1/mail/drafts", {
            to: ["drafts-integration@example.com"],
            subject: "should not be created",
            body_plain: "x",
            replace_uid: "999999999",
          }),
        "DRAFT_NOT_FOUND",
        "save_draft_unknown_replace",
      );
    });
  });

  describe("mail_delete_draft", () => {
    it("deletes a draft by draft_id and then reports DRAFT_NOT_FOUND on repeat", async () => {
      const draft = assertSuccess<DraftResult>(
        await client.post("/api/v1/mail/drafts", {
          to: ["drafts-integration@example.com"],
          subject: "to delete",
          body_plain: "x",
        }),
        "save_for_delete",
      );
      const del = assertSuccess<{ status: string }>(
        await client.delete(`/api/v1/mail/drafts/${encodeURIComponent(String(draft.draft_id))}`),
        "delete_draft",
      );
      expect(del.status).toBe("deleted");
      await expectApiError(
        () => client.delete(`/api/v1/mail/drafts/${encodeURIComponent(String(draft.draft_id))}`),
        "DRAFT_NOT_FOUND",
        "delete_draft_repeat",
      );
    });

    it("deletes by the imap_uid from the save response (U6.5b: both spaces)", async () => {
      const draft = assertSuccess<DraftResult>(
        await client.post("/api/v1/mail/drafts", {
          to: ["drafts-integration@example.com"],
          subject: "delete by uid",
          body_plain: "x",
        }),
        "save_for_uid_delete",
      );
      const del = assertSuccess<{ status: string }>(
        await client.delete(`/api/v1/mail/drafts/${encodeURIComponent(String(draft.draft_uid))}`),
        "delete_draft_by_uid",
      );
      expect(del.status).toBe("deleted");
      // and the listing no longer contains it
      const items = assertItems<MessageSummary>(
        await client.get("/api/v1/mail/folders/Drafts/messages", { max_results: "50" }),
        "list_drafts_after_uid_delete",
      );
      const stillThere = items.filter(
        (m) => m.subject === "delete by uid" && String(m.imap_uid) === String(draft.draft_uid),
      );
      expect(stillThere.length).toBe(0);
    });
  });

  describe("mail_send", () => {
    it("threads a reply via reply_to_message_id and reports draft_deleted", async () => {
      // find any INBOX message to reply to
      const items = assertItems<MessageSummary>(
        await client.get("/api/v1/mail/folders/INBOX/messages", { max_results: "1" }),
        "list_inbox",
      );
      if (items.length === 0) return; // nothing to thread against — skip body

      const draft = assertSuccess<DraftResult>(
        await client.post("/api/v1/mail/drafts", {
          to: ["test2@test.localhost"],
          subject: "TS reply draft",
          body_plain: "reply body",
        }),
        "save_reply_draft",
      );
      const sent = assertSuccess<{
        status: string;
        draft_deleted?: boolean;
        thread_id?: string | null;
      }>(
        await client.post("/api/v1/mail/messages", {
          to: ["test2@test.localhost"],
          subject: "TS reply",
          body_plain: "reply body",
          draft_id: String(draft.draft_id),
          reply_to_message_id: String(items[0].id),
        }),
        "send_reply",
      );
      expect(sent.status).toBe("sent");
      expect(sent.draft_deleted).toBe(true);
      // the draft is really gone
      await expectApiError(
        () => client.delete(`/api/v1/mail/drafts/${encodeURIComponent(String(draft.draft_id))}`),
        "DRAFT_NOT_FOUND",
        "draft_gone_after_send",
      );
    });
  });

  describe("mail_get_thread body options", () => {
    it("strips bodies with include_bodies=false and honors body_format=plain", async () => {
      const items = assertItems<{ thread_id?: string | null }>(
        await client.get("/api/v1/mail/folders/INBOX/messages", { max_results: "1" }),
        "list_for_thread",
      );
      const withThread = items.find((m) => m.thread_id);
      if (!withThread?.thread_id) return;

      const noBodies = assertItems<Record<string, unknown>>(
        await client.get(`/api/v1/mail/threads/${encodeURIComponent(withThread.thread_id)}`, {
          include_bodies: "false",
        }),
        "thread_no_bodies",
      );
      expect(noBodies.length).toBeGreaterThan(0);
      expect("body_plain" in noBodies[0]).toBe(false);
      expect("body_html" in noBodies[0]).toBe(false);

      const plain = assertItems<Record<string, unknown>>(
        await client.get(`/api/v1/mail/threads/${encodeURIComponent(withThread.thread_id)}`, {
          body_format: "plain",
        }),
        "thread_plain",
      );
      expect("body_html" in plain[0]).toBe(false);
      expect("imap_uid" in plain[0]).toBe(true);
    });
  });
});
