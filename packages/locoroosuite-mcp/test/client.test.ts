import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ApiClient, ApiError } from "../src/client.js";

describe("ApiClient", () => {
  describe("error handling", () => {
    it("throws ApiError with status 0 on connection failure", async () => {
      const client = new ApiClient("http://127.0.0.1:1", "lr_test", null);
      await expect(client.get("/api/v1/accounts")).rejects.toThrow(ApiError);
      await expect(client.get("/api/v1/accounts")).rejects.toMatchObject({
        status: 0,
        code: "CONNECTION_ERROR",
      });
    });

    it("throws ApiError for 401 responses", async () => {
      const body = JSON.stringify({
        error: { code: "AUTH_INVALID", message: "Invalid or expired token" },
      });
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          ok: false,
          status: 401,
          statusText: "Unauthorized",
          text: () => Promise.resolve(body),
          headers: new Headers(),
        }),
      );

      const client = new ApiClient("http://localhost:5001", "lr_bad_token", null);
      try {
        await client.get("/api/v1/accounts");
        expect.unreachable("Should have thrown");
      } catch (err) {
        expect(err).toBeInstanceOf(ApiError);
        expect((err as ApiError).status).toBe(401);
        expect((err as ApiError).code).toBe("AUTH_INVALID");
        expect((err as ApiError).message).toBe("Invalid or expired token");
      }
    });

    it("throws ApiError for 403 responses with permission hint", async () => {
      const body = JSON.stringify({
        error: {
          code: "SCOPE_DENIED",
          message: "This action requires the 'mail:read' permission.",
        },
      });
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          ok: false,
          status: 403,
          statusText: "Forbidden",
          text: () => Promise.resolve(body),
          headers: new Headers(),
        }),
      );

      const client = new ApiClient("http://localhost:5001", "lr_test", null);
      try {
        await client.get("/api/v1/mail/folders");
        expect.unreachable("Should have thrown");
      } catch (err) {
        expect(err).toBeInstanceOf(ApiError);
        expect((err as ApiError).status).toBe(403);
        expect((err as ApiError).message).toContain(
          "additional API permissions",
        );
      }
    });

    it("throws ApiError for non-JSON responses", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue({
          ok: false,
          status: 502,
          statusText: "Bad Gateway",
          text: () => Promise.resolve("<html>Gateway Timeout</html>"),
          headers: new Headers(),
        }),
      );

      const client = new ApiClient("http://localhost:5001", "lr_test", null);
      try {
        await client.get("/api/v1/accounts");
        expect.unreachable("Should have thrown");
      } catch (err) {
        expect(err).toBeInstanceOf(ApiError);
        expect((err as ApiError).status).toBe(502);
        expect((err as ApiError).code).toBe("INVALID_RESPONSE");
      }
    });
  });

  describe("accountId helpers", () => {
    const client = new ApiClient("http://localhost:5001", "lr_test", "42");

    it("accountId() returns account_id when set", () => {
      expect(client.accountId()).toEqual({ account_id: "42" });
    });

    it("accountId() uses override when provided", () => {
      expect(client.accountId("99")).toEqual({ account_id: "99" });
    });

    it("accountId() returns empty object when no id and no override", () => {
      const noId = new ApiClient("http://localhost:5001", "lr_test", null);
      expect(noId.accountId()).toEqual({});
    });
  });
});
