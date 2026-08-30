import { describe, it, expect, beforeAll } from "vitest";
import { isServerAvailable, createClient, assertSuccess } from "./helpers.js";
import { ApiError } from "../../src/client.js";
import type { ApiClient } from "../../src/client.js";

describe.skipIf(!(await isServerAvailable()))("MCP Client → API: Chat", () => {
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

  describe("chat_list_rooms", () => {
    it("returns rooms array with required fields", async () => {
      const res = await client.get("/api/v1/chat/rooms");
      const r = assertSuccess<Array<Record<string, unknown>>>(res, "list_rooms");
      expect(Array.isArray(r.data)).toBe(true);
      for (const room of r.data!) {
        expect(room).toHaveProperty("room_id");
        expect(room).toHaveProperty("display_name");
        expect(room).toHaveProperty("is_direct");
        expect(room).toHaveProperty("membership");
        expect(room).toHaveProperty("notification_count");
      }
    });

    it("returns 404 error for messages of an unknown room", async () => {
      await expect(
        client.get("/api/v1/chat/rooms/!doesnotexist:locoroo.test/messages"),
      ).rejects.toMatchObject({ code: "ROOM_NOT_FOUND" } satisfies Partial<ApiError>);
    });
  });

  describe("chat_create_room + send + list + react + edit + redact", () => {
    let roomId = "";
    let eventId = "";

    it("rejects a room without a name", async () => {
      await expect(client.post("/api/v1/chat/rooms", {})).rejects.toMatchObject({
        code: "VALIDATION",
      } satisfies Partial<ApiError>);
    });

    it("creates a room", async () => {
      const res = await client.post("/api/v1/chat/rooms", {
        name: "MCP integration test room",
        topic: "created by vitest",
      });
      const r = assertSuccess<Record<string, unknown>>(res, "create_room");
      expect(r.data).toHaveProperty("room_id");
      roomId = r.data!.room_id as string;
      expect(roomId.length).toBeGreaterThan(0);
    });

    it("sends a message", async () => {
      const res = await client.post(`/api/v1/chat/rooms/${encodeURIComponent(roomId)}/messages`, {
        body: "hello from the MCP integration test",
      });
      const r = assertSuccess<{ event_id: string }>(res, "send_message");
      expect(r.data!.event_id).toBeTruthy();
      eventId = r.data!.event_id;
    });

    it("lists messages including the one just sent", async () => {
      const res = await client.get(`/api/v1/chat/rooms/${encodeURIComponent(roomId)}/messages`);
      const r = assertSuccess<Array<Record<string, unknown>>>(res, "list_messages");
      expect(Array.isArray(r.data)).toBe(true);
      const found = r.data!.find((m) => m.event_id === eventId);
      expect(found).toBeDefined();
      expect(found).toHaveProperty("sender");
      expect(found).toHaveProperty("origin_server_ts");
      expect(found).toHaveProperty("content");
    });

    it("reacts to the message", async () => {
      const res = await client.post(`/api/v1/chat/messages/${encodeURIComponent(eventId)}/react`, {
        key: "👍",
      });
      assertSuccess(res, "react");
    });

    it("edits own message", async () => {
      const res = await client.post(`/api/v1/chat/messages/${encodeURIComponent(eventId)}/edit`, {
        body: "edited by the MCP integration test",
      });
      assertSuccess(res, "edit_message");
    });

    it("marks the room read", async () => {
      const res = await client.post(`/api/v1/chat/rooms/${encodeURIComponent(roomId)}/read`, {
        event_id: eventId,
      });
      assertSuccess(res, "mark_read");
    });

    it("redacts (deletes) own message", async () => {
      const res = await client.post(`/api/v1/chat/messages/${encodeURIComponent(eventId)}/redact`);
      assertSuccess(res, "redact");
    });

    it("leaves and forgets the room", async () => {
      const res = await client.post(`/api/v1/chat/rooms/${encodeURIComponent(roomId)}/leave`);
      assertSuccess(res, "leave_room");
    });
  });
});
