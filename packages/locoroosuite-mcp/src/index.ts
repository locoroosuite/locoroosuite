#!/usr/bin/env node

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { ApiClient } from "./client.js";
import { registerMailTools } from "./tools/mail.js";
import { registerContactsTools } from "./tools/contacts.js";
import { registerCalendarTools } from "./tools/calendar.js";
import { registerDocsTools } from "./tools/docs.js";

function parseArgs(argv: string[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const arg of argv) {
    if (arg.startsWith("--")) {
      const idx = arg.indexOf("=");
      if (idx !== -1) {
        result[arg.slice(2, idx)] = arg.slice(idx + 1);
      }
    }
  }
  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  const apiUrl = args["api-url"] || process.env.LOCOROO_API_URL;
  const token = args["token"] || process.env.LOCOROO_API_TOKEN;
  const accountId = args["account-id"] || process.env.LOCOROO_ACCOUNT_ID;

  if (!apiUrl) {
    console.error("Error: --api-url or LOCOROO_API_URL is required");
    process.exit(1);
  }

  if (!token) {
    console.error("Error: --token or LOCOROO_API_TOKEN is required");
    process.exit(1);
  }

  const baseUrl = apiUrl.replace(/\/+$/, "");
  const client = new ApiClient(baseUrl, token, accountId || null);

  console.error(`LocoRooSuite MCP: validating token against ${baseUrl}/api/v1/accounts ...`);
  try {
    await client.get("/api/v1/accounts");
    console.error("LocoRooSuite MCP: token validated successfully");
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`Warning: token validation failed — ${message}`);
    console.error("LocoRooSuite MCP: starting anyway; tool calls will surface the error");
  }

  const server = new McpServer({
    name: "locoroosuite",
    version: "0.5.1",
  });

  server.tool(
    "accounts_list",
    "List the customer's email accounts",
    {},
    async () => {
      const data = await client.get("/api/v1/accounts");
      return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
    },
  );

  registerMailTools(server, client);
  registerContactsTools(server, client);
  registerCalendarTools(server, client);
  registerDocsTools(server, client);

  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("LocoRooSuite MCP: server running on stdio");
}

main().catch((err) => {
  console.error("Fatal:", err);
  process.exit(1);
});
