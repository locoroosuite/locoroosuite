import { describe, it, expect, beforeAll } from "vitest";
import { isServerAvailable, createClient, assertSuccess, assertError } from "./helpers.js";
import type { ApiClient } from "../../src/client.js";

describe.skipIf(!(await isServerAvailable()))("MCP Client → API: Docs folders & tags", () => {
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

  it("creates a folder and lists it", async () => {
    const uniq = `F${Date.now()}`;
    const created = await client.post("/api/v1/docs/folders", { name: uniq });
    const c = assertSuccess<{ path: string }>(created, "create_folder");
    expect(c.data!.path).toBe(uniq);

    const list = await client.get("/api/v1/docs/folders");
    const l = assertSuccess<Array<{ path: string }>>(list, "list_folders");
    expect(Array.isArray(l.data)).toBe(true);
    expect(l.data!.some((f) => f.path === uniq)).toBe(true);
  });

  it("rejects an invalid folder name", async () => {
    try {
      await client.post("/api/v1/docs/folders", { name: "bad/name" });
      throw new Error("expected validation error");
    } catch (e) {
      expect(e).toBeInstanceOf(Error);
    }
  });

  it("creates a document in a folder, moves it, and tags it", async () => {
    const folder = `T${Date.now()}`;
    await client.post("/api/v1/docs/folders", { name: folder });

    const created = await client.post("/api/v1/docs/documents", {
      name: "Foldered Doc",
      type: "odt",
      folder,
    });
    const c = assertSuccess<{ id: string; folder_path: string; tags: string[] }>(created, "create_document");
    expect(c.data!.folder_path).toBe(folder);
    const docId = c.data!.id;

    // Move to root.
    const moved = await client.post(`/api/v1/docs/documents/${encodeURIComponent(docId)}/move`, { folder: "" });
    const m = assertSuccess<{ folder_path: string }>(moved, "move_document");
    expect(m.data!.folder_path).toBe("");

    // Tags: add then read.
    const tagged = await client.put(`/api/v1/docs/documents/${encodeURIComponent(docId)}/tags`, { add: ["urgent"] });
    const t = assertSuccess<{ tags: string[] }>(tagged, "update_tags");
    expect(t.data!.tags).toContain("urgent");

    const got = await client.get(`/api/v1/docs/documents/${encodeURIComponent(docId)}/tags`);
    const g = assertSuccess<{ tags: string[] }>(got, "get_tags");
    expect(g.data!.tags).toContain("urgent");
  });

  it("renames a folder and deletes it (contents flatten to parent)", async () => {
    const parent = `P${Date.now()}`;
    await client.post("/api/v1/docs/folders", { name: parent });
    await client.post("/api/v1/docs/folders", { name: "Child", parent });

    const renamed = await client.post("/api/v1/docs/folders/rename", { path: `${parent}/Child`, name: "Kid" });
    const r = assertSuccess<{ path: string }>(renamed, "rename_folder");
    expect(r.data!.path).toBe(`${parent}/Kid`);

    const deleted = await client.post("/api/v1/docs/folders/delete", { path: `${parent}/Kid` });
    const d = assertSuccess<{ moved_to: string }>(deleted, "delete_folder");
    expect(d.data!.moved_to).toBe(parent);
  });
});
