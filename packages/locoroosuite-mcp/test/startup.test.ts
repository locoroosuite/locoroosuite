import { describe, it, expect } from "vitest";
import { spawn } from "child_process";
import * as path from "path";
import * as fs from "fs";

const projectRoot = path.resolve(__dirname, "..");
const distDir = path.join(projectRoot, "dist");
const indexPath = path.join(distDir, "index.js");

describe("startup behavior", () => {
  it("exits with code 1 when --api-url is missing", async () => {
    const child = spawn("node", [indexPath, "--token=lr_test"], {
      stdio: ["pipe", "pipe", "pipe"],
    });
    const code = await new Promise<number | null>((resolve) =>
      child.on("close", resolve),
    );
    expect(code).toBe(1);
  });

  it("exits with code 1 when --token is missing", async () => {
    const child = spawn("node", [indexPath, "--api-url=http://localhost:5001"], {
      stdio: ["pipe", "pipe", "pipe"],
    });
    const code = await new Promise<number | null>((resolve) =>
      child.on("close", resolve),
    );
    expect(code).toBe(1);
  });

  it("starts the server even when token validation fails (network error)", async () => {
    const child = spawn(
      "node",
      [
        indexPath,
        "--api-url=http://127.0.0.1:1",
        "--token=lr_invalid_token_for_test",
      ],
      { stdio: ["pipe", "pipe", "pipe"] },
    );

    const stderrChunks: Buffer[] = [];
    child.stderr!.on("data", (chunk: Buffer) => stderrChunks.push(chunk));

    const result = await new Promise<{ code: number | null; stderr: string }>(
      (resolve) => {
        const timeout = setTimeout(() => {
          child.kill();
          resolve({
            code: null,
            stderr: Buffer.concat(stderrChunks).toString(),
          });
        }, 5000);

        child.on("close", (code) => {
          clearTimeout(timeout);
          resolve({
            code,
            stderr: Buffer.concat(stderrChunks).toString(),
          });
        });
      },
    );

    expect(result.stderr).toContain("token validation failed");
    expect(result.stderr).toContain("starting anyway");
    expect(result.stderr).not.toContain("token validated successfully");
    expect(result.code).toBeNull();
  }, 10000);

  it("starts the server even when token validation fails (401)", async () => {
    const serverScript = `
      const http = require("http");
      const server = http.createServer((req, res) => {
        res.writeHead(401, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: { code: "AUTH_INVALID", message: "Invalid or expired token" } }));
      });
      server.listen(0, "127.0.0.1", () => {
        const port = server.address().port;
        process.stdout.write(port.toString());
      });
    `;

    const mockServer = spawn("node", ["-e", serverScript], {
      stdio: ["pipe", "pipe", "pipe"],
    });

    const portStr = await new Promise<string>((resolve) => {
      mockServer.stdout!.on("data", (chunk: Buffer) => resolve(chunk.toString().trim()));
    });

    const child = spawn(
      "node",
      [
        indexPath,
        `--api-url=http://127.0.0.1:${portStr}`,
        "--token=lr_bad_token",
      ],
      { stdio: ["pipe", "pipe", "pipe"] },
    );

    const stderrChunks: Buffer[] = [];
    child.stderr!.on("data", (chunk: Buffer) => stderrChunks.push(chunk));

    const result = await new Promise<{ code: number | null; stderr: string }>(
      (resolve) => {
        const timeout = setTimeout(() => {
          child.kill();
          resolve({
            code: null,
            stderr: Buffer.concat(stderrChunks).toString(),
          });
        }, 5000);

        child.on("close", (code) => {
          clearTimeout(timeout);
          resolve({
            code,
            stderr: Buffer.concat(stderrChunks).toString(),
          });
        });
      },
    );

    mockServer.kill();

    expect(result.stderr).toContain("token validation failed");
    expect(result.stderr).toContain("Invalid or expired token");
    expect(result.stderr).toContain("starting anyway");
    expect(result.code).toBeNull();
  }, 10000);

  it("logs success when token validation passes", async () => {
    const serverScript = `
      const http = require("http");
      const server = http.createServer((req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ data: [{ id: 1, email: "test@test.localhost" }] }));
      });
      server.listen(0, "127.0.0.1", () => {
        const port = server.address().port;
        process.stdout.write(port.toString());
      });
    `;

    const mockServer = spawn("node", ["-e", serverScript], {
      stdio: ["pipe", "pipe", "pipe"],
    });

    const portStr = await new Promise<string>((resolve) => {
      mockServer.stdout!.on("data", (chunk: Buffer) => resolve(chunk.toString().trim()));
    });

    const child = spawn(
      "node",
      [
        indexPath,
        `--api-url=http://127.0.0.1:${portStr}`,
        "--token=lr_valid_token",
      ],
      { stdio: ["pipe", "pipe", "pipe"] },
    );

    const stderrChunks: Buffer[] = [];
    child.stderr!.on("data", (chunk: Buffer) => stderrChunks.push(chunk));

    const result = await new Promise<{ code: number | null; stderr: string }>(
      (resolve) => {
        const timeout = setTimeout(() => {
          child.kill();
          resolve({
            code: null,
            stderr: Buffer.concat(stderrChunks).toString(),
          });
        }, 5000);

        child.on("close", (code) => {
          clearTimeout(timeout);
          resolve({
            code,
            stderr: Buffer.concat(stderrChunks).toString(),
          });
        });
      },
    );

    mockServer.kill();

    expect(result.stderr).toContain("token validated successfully");
    expect(result.stderr).not.toContain("token validation failed");
    expect(result.code).toBeNull();
  }, 10000);
});
