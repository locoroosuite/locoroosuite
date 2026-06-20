# locoroosuite-mcp

MCP server for [LocoRooSuite](https://locoroo.net) — the self-hosted email, contacts, calendar, and documents platform built around data sovereignty.

This package lets your AI ag6ent talk directly to your LocoRooSuite instance. It connects over the Model Context Protocol (MCP) and exposes your mail, contacts, calendar, and documents as tools the agent can use. Read email, send replies, check your calendar, look up a contact, edit a document — all from within your agent of choice: a coding assistant (Cursor, Claude Code, opencode), a personal assistant (Goose, Hermes), a local LLM UI (LM Studio, AnythingLLM), or an automation tool (n8n).

A managed hosted version is available at [locoroo.net/suite](https://locoroo.net/suite/) if you don't want to run your own instance. The open-source project is at [codeberg.org/locoroo/locoroosuite](https://codeberg.org/locoroo/locoroosuite).

## Quick start

```bash
npx locoroosuite-mcp --api-url=https://your-instance.locoroo.net --token=lr_YOUR_API_TOKEN
```

Or install it globally:

```bash
npm install -g locoroosuite-mcp
locoroosuite-mcp --api-url=https://your-instance.locoroo.net --token=lr_your_api_token
```

## Setup guides

`locoroosuite-mcp` works with any agent that can launch a local stdio MCP server. Pick your client:

**General & personal assistants**
- [Claude Desktop](#claude-desktop) · [Goose](#goose) · [Hermes](#hermes)

**IDEs & code editors**
- [Cursor](#cursor) · [Windsurf](#windsurf-formerly-codeium) · [Cline](#cline-vs-code-extension) · [Continue](#continue) · [VS Code + GitHub Copilot](#vs-code--github-copilot) · [Zed](#zed) · [Augment Code](#augment-code) · [Roo Code](#roo-code-formerly-roo-cline) · [Kilo Code](#kilo-code) · [JetBrains AI Assistant / Junie](#jetbrains-ai-assistant--junie)

**Terminal & CLI agents**
- [Claude Code](#claude-code-cli) · [GitHub Copilot CLI](#github-copilot-cli) · [Codex](#codex-openai-cli) · [Gemini CLI](#gemini-cli) · [opencode](#opencode) · [Amazon Q CLI](#amazon-q-cli)

**Local LLM & chat UIs**
- [LM Studio](#lm-studio) · [AnythingLLM](#anythingllm) · [Cherry Studio](#cherry-studio) · [LibreChat](#librechat)

**Automation**
- [n8n](#n8n)

Using a cloud/remote-only client (ChatGPT, v0, Replit, OpenClaw)? See [Remote-only clients](#remote-only-clients).

### Claude Desktop

Open your `claude_desktop_config.json` file (macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`, Windows: `%APPDATA%\Claude\claude_desktop_config.json`) and add:

```json
{
  "mcpServers": {
    "locoroosuite": {
      "command": "npx",
      "args": ["-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token=lr_your_api_token"]
    }
  }
}
```

Restart Claude Desktop after saving. You should see an MCP server indicator (the hammer icon) in the bottom-right of the input box. Click it to verify the tools are loaded.

### Goose

Goose is a general-purpose local agent by Block. The easiest way to add an MCP server is the interactive configurator, which writes the correct entry to `~/.config/goose/config.yaml` for you:

```bash
goose configure
```

Choose **Add extension**, then enter:

- Type: **stdio**
- Name: `locoroosuite`
- Command: `npx`
- Args: `-y locoroosuite-mcp --api-url=https://your-instance.locoroo.net --token=lr_your_api_token`

Restart Goose (or run `/extensions reload` in a session) and ask: "use the locoroosuite tool to list my unread emails".

### Hermes

[Hermes Agent](https://hermes-agent.nousresearch.com/) is Nous Research's personal AI assistant (Telegram/Discord/Slack/email/CLI). MCP support ships in the standard install. Edit `~/.hermes/config.yaml` and add the server under `mcp_servers`:

```yaml
mcp_servers:
  locoroosuite:
    command: "npx"
    args: ["-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token=lr_your_api_token"]
```

Hermes supports `${VAR}` substitution in `args`, so you can keep the token out of the file with `"--token=${LOCOROO_API_TOKEN}"` (set in `~/.hermes/.env`). Run `hermes chat` and Hermes discovers the tools at startup. Reload after a config change with `/reload-mcp`.

### Cursor

1. Open **Settings** → **MCP** (or search for "MCP" in the settings search bar)
2. Click **Add new MCP server**
3. Set the type to **stdio**, name it `locoroosuite`, and use this command:
   - Command: `npx`
   - Args: `-y locoroosuite-mcp --api-url=https://your-instance.locoroo.net --token=lr_your_api_token`

Alternatively, add it directly to your project's `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "locoroosuite": {
      "command": "npx",
      "args": ["-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token=lr_your_api_token"]
    }
  }
}
```

### Windsurf (formerly Codeium)

1. Open the Cascade panel and click the **MCPs** icon in the top-right toolbar
2. Open the **Configure** tab and click **Configure MCP Servers**
3. Add this to your `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "locoroosuite": {
      "command": "npx",
      "args": ["-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token=lr_your_api_token"]
    }
  }
}
```

### Cline (VS Code extension)

1. In the Cline panel, click the **MCP Servers** icon in the top toolbar
2. Open the **Configure** tab and click **Configure MCP Servers**
3. Add this to your `~/.cline/mcp.json`:

```json
{
  "mcpServers": {
    "locoroosuite": {
      "command": "npx",
      "args": ["-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token=lr_your_api_token"]
    }
  }
}
```

### Continue

Continue is an open-source coding agent for VS Code and JetBrains. Add the server to `~/.continue/config.yaml` (create it if missing) under `mcpServers`:

```yaml
mcpServers:
  locoroosuite:
    command: npx
    args:
      - "-y"
      - "locoroosuite-mcp"
      - "--api-url=https://your-instance.locoroo.net"
      - "--token=lr_your_api_token"
```

Reload the VS Code window (or run the **Continue: Reload** command) to pick up the new server.

### VS Code + GitHub Copilot

VS Code's agent mode reads MCP servers from a workspace `.vscode/mcp.json` file. Note the key is **`servers`**, not `mcpServers`:

```json
{
  "servers": {
    "locoroosuite": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token=lr_your_api_token"]
    }
  }
}
```

Open Chat in agent mode, select the **locoroosuite** server in the tools menu, and ask Copilot to read or send mail.

### Zed

Add this to your Zed settings JSON (`~/.config/zed/settings.json` on Linux/macOS):

```json
{
  "context_servers": {
    "locoroosuite": {
      "command": "npx",
      "args": ["-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token=lr_your_api_token"]
    }
  }
}
```

### Augment Code

1. Open Augment settings in VS Code or JetBrains
2. Navigate to **MCP Servers** in the configuration
3. Add a new stdio server with the npx command above, or edit your MCP config to include the same JSON block as shown for other clients

### Roo Code (formerly Roo Cline)

1. Open Roo Code settings in VS Code
2. Go to the **MCP Servers** section
3. Click **Edit MCP Settings** and add the `locoroosuite` entry with the same npx command and args

### Kilo Code

Kilo Code is a VS Code extension (a Cline/Roo Code fork) with the same MCP settings shape.

1. Open Kilo Code settings in VS Code
2. Go to the **MCP Servers** section
3. Click **Edit MCP Settings** and add the `locoroosuite` entry using the standard `mcpServers` block:

```json
{
  "mcpServers": {
    "locoroosuite": {
      "command": "npx",
      "args": ["-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token=lr_your_api_token"]
    }
  }
}
```

### JetBrains AI Assistant / Junie

1. Open **Settings** → **Tools** → **AI Assistant** → **MCP Servers** (or **Junie** → **MCP**)
2. Add a new server with command `npx` and the args above
3. For Junie, you can also edit `~/.junie/mcp.json` (global) or `.junie/mcp/` (project-level)

### Claude Code (CLI)

```bash
claude mcp add locoroosuite -- npx -y locoroosuite-mcp --api-url=https://your-instance.locoroo.net --token=lr_your_api_token
```

### GitHub Copilot CLI

Run `/mcp` in Copilot CLI and add the server, or configure it in your `.github/copilot/mcp.json`:

```json
{
  "mcpServers": {
    "locoroosuite": {
      "command": "npx",
      "args": ["-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token=lr_your_api_token"]
    }
  }
}
```

### Codex (OpenAI CLI)

Add to your Codex MCP configuration (`~/.codex/config.toml`):

```toml
[mcp_servers.locoroosuite]
command = "npx"
args = ["-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token=lr_your_api_token"]
```

### Gemini CLI

Add to your Gemini CLI MCP settings (`~/.gemini/settings.json`):

```json
{
  "mcpServers": {
    "locoroosuite": {
      "command": "npx",
      "args": ["-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token=lr_your_api_token"]
    }
  }
}
```

### opencode

[opencode](https://opencode.ai) uses a single config file (`opencode.json` or `opencode.jsonc`) instead of a per-client `mcp.json`. Add the server under the top-level `mcp` key. Global config lives at `~/.config/opencode/opencode.json`; project-level at `.opencode/opencode.json`.

opencode supports inline `{env:VAR}` interpolation, so prefer this form to keep your token out of the file:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "locoroosuite": {
      "type": "local",
      "command": ["npx", "-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token={env:LOCOROO_API_TOKEN}"]
    }
  }
}
```

(Export `LOCOROO_API_TOKEN` in your shell, or paste the literal `lr_...` token if you prefer.) After saving, run `opencode mcp list` to confirm the server connected, then prompt with "use the locoroosuite tool to …".

### Amazon Q CLI

Amazon Q CLI (AWS) reads MCP servers from `~/.aws/amazonq/mcp-config.json`:

```json
{
  "mcpServers": {
    "locoroosuite": {
      "command": "npx",
      "args": ["-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token=lr_your_api_token"]
    }
  }
}
```

Restart any running Q session, then `/tools` to confirm the locoroosuite tools loaded.

### LM Studio

LM Studio is a local-model runner with built-in MCP support.

1. Open **Developer** → **Tools** → **MCP Servers**
2. Click **Add Server**, set transport to **stdio**, name it `locoroosuite`
3. Command: `npx`
4. Args: `-y locoroosuite-mcp --api-url=https://your-instance.locoroo.net --token=lr_your_api_token`
5. Start the server, then in a chat with any loaded model, ask it to use the locoroosuite tools

LM Studio persists the entry in its own bundled `mcp.json`; you only configure it through the UI.

### AnythingLLM

AnythingLLM is a self-hosted "chat with anything" UI.

1. Open **Settings** → **Agent Skills** → **MCP Server**
2. Add a new **stdio** server named `locoroosuite`
3. Command: `npx`
4. Args: `-y locoroosuite-mcp --api-url=https://your-instance.locoroo.net --token=lr_your_api_token`
5. Save, set an agent to use the @locoroosuite skill, and start chatting

### Cherry Studio

Cherry Studio is a cross-platform desktop chat client that supports stdio MCP.

1. Open **Settings** → **MCP Servers** → **Add**
2. Transport: **stdio**, name: `locoroosuite`
3. Command: `npx`
4. Args: `-y locoroosuite-mcp --api-url=https://your-instance.locoroo.net --token=lr_your_api_token`
5. Enable the server, then enable it per-model in the chat settings

### LibreChat

LibreChat is a self-hosted ChatGPT-style UI. It launches MCP servers server-side from `librechat.yaml`:

```yaml
mcpServers:
  locoroosuite:
    command: npx
    args: ["-y", "locoroosuite-mcp", "--api-url=https://your-instance.locoroo.net", "--token=lr_your_api_token"]
```

Restart LibreChat after saving. The locoroosuite tools appear as available actions for agents that have MCP enabled.

### n8n

n8n is an automation/workflow platform with an **MCP Client** node that can launch stdio servers.

1. Add an **MCP Client** node to a workflow
2. Set **Transport** to **Command (STDIO)**
3. Command: `npx`
4. Arguments: `-y locoroosuite-mcp --api-url=https://your-instance.locoroo.net --token=lr_your_api_token`
5. The node enumerates the locoroosuite tools as outputs — wire them into the rest of your workflow (e.g. a daily "triage my inbox" flow on a Schedule Trigger)

### Remote-only clients

The following clients support MCP but only over **remote** HTTP/SSE transport — they cannot launch a local `npx` process, so they can't use this package directly. Point them at the **LocoRooSuite Python MCP server** (HTTP/SSE with OAuth 2.1, documented separately) instead, using your instance's `/mcp` URL:

- **ChatGPT** (OpenAI) — remote Connectors / developer mode only; no local stdio.
- **v0, Replit, Microsoft Copilot Studio, MCPJam** — cloud-hosted, remote MCP only.

> **OpenClaw** is a popular personal AI assistant, but it acts as an MCP **server**, not a client — other agents connect *to* it. It cannot consume `locoroosuite-mcp` as a tool provider, so it isn't listed above.

## Configuration

All options can be passed as CLI flags or set as environment variables:

| Flag | Env variable | Required | Description |
|------|-------------|----------|-------------|
| `--api-url` | `LOCOROO_API_URL` | Yes | Your LocoRooSuite instance URL |
| `--token` | `LOCOROO_API_TOKEN` | Yes | API token (generate one in Settings → API Tokens) |
| `--account-id` | `LOCOROO_ACCOUNT_ID` | No | Default account ID if you have multiple email accounts |

You can mix and match — pass `--api-url` as a flag and set the token via an environment variable, or the other way around.

**Keeping the token out of config files.** Several clients support inline environment-variable substitution so you don't paste the secret into a JSON/YAML file: opencode uses `{env:LOCOROO_API_TOKEN}`, Hermes uses `${LOCOROO_API_TOKEN}`, and Codex/JetBrains read from your shell environment automatically. Prefer one of these where available.

## What it can do

On startup the server validates your token against the `/api/v1/accounts` endpoint. If the token is invalid or the server is unreachable, it exits immediately with a clear error. Once validated, it exposes the following tools:

### Mail

Full IMAP-based email access. Browse folders, read and send messages, search your inbox, manage drafts, handle attachments — everything you'd expect from a mail client, exposed as tools for your AI assistant.

You can ask your AI things like "do I have any unread emails from Sarah?", "reply to the thread about the Q3 budget with the attached numbers", or "find all emails about the server migration from last week and move them to a folder".

| Tool | What it does |
|------|-------------|
| `mail_list_folders` | List email folders with unread counts |
| `mail_list_messages` | List messages in a folder (paginated, filterable) |
| `mail_get_message` | Read a full message including body and attachments |
| `mail_get_raw_message` | Get the raw RFC 822 source (base64 encoded) |
| `mail_search` | Search messages by query with date and flag filters |
| `mail_send` | Compose and send a new email |
| `mail_save_draft` | Save a draft |
| `mail_delete_draft` | Delete a draft |
| `mail_move_message` | Move a message to another folder |
| `mail_delete_message` | Move a message to Trash |
| `mail_update_flags` | Toggle read/flagged status |
| `mail_get_thread` | Get all messages in a conversation thread |
| `mail_get_attachment` | Download an attachment |
| `mail_view_attachment` | Convert an attachment to HTML for viewing |
| `mail_bulk_move` | Move multiple messages at once |
| `mail_bulk_delete` | Delete multiple messages at once |
| `mail_bulk_flag` | Update flags on multiple messages |

### Contacts

CardDAV-backed address book management. Look up a phone number, add a new contact from an email signature, update someone's job title — your AI can handle contact housekeeping that you'd normally put off.

| Tool | What it does |
|------|-------------|
| `contacts_list` | List contacts (filterable, sortable) |
| `contacts_get` | Get full contact detail |
| `contacts_search` | Search by name, email, or phone |
| `contacts_create` | Create a new contact |
| `contacts_update` | Update an existing contact |
| `contacts_delete` | Delete a contact |
| `contacts_bulk_delete` | Delete multiple contacts |

### Calendar

CalDAV-based calendar with full event management. Check today's schedule, create meetings, find free time slots, update recurring events. Useful for "when am I free next Tuesday?" or "schedule a 30-minute call with the team next week".

| Tool | What it does |
|------|-------------|
| `calendar_list_calendars` | List your calendars |
| `calendar_create_calendar` | Create a new calendar |
| `calendar_update_calendar` | Rename or recolor a calendar |
| `calendar_delete_calendar` | Delete a calendar |
| `calendar_list_events` | List events in a date range |
| `calendar_get_event` | Get full event detail |
| `calendar_search_events` | Search events by summary |
| `calendar_create_event` | Create an event |
| `calendar_update_event` | Update an event |
| `calendar_delete_event` | Delete an event |
| `calendar_check_free_busy` | Check free/busy time ranges |

### Documents

Collabora Online-based document editing via the WOPI protocol. Create and edit text documents, spreadsheets, and presentations in OpenDocument format. The AI can read a document's content, make edits via markdown, and even work through a draft/review workflow — create a proposed change as a draft, then you or your team can accept or discard it.

| Tool | What it does |
|------|-------------|
| `docs_list_documents` | List documents (filterable by type) |
| `docs_get_document` | Get document metadata |
| `docs_create_document` | Create a new empty document |
| `docs_rename_document` | Rename a document |
| `docs_delete_document` | Soft-delete (moves to trash) |
| `docs_read_content` | Read document content as text or markdown |
| `docs_update_content` | Replace document content (markdown input) |
| `docs_create_draft` | Create a draft with AI-modified content |
| `docs_list_drafts` | List pending drafts for a document |
| `docs_apply_draft` | Accept a draft (replaces original) |
| `docs_discard_draft` | Discard a draft without changing the original |
| `docs_download_document` | Download in ODF format |
| `docs_export_pdf` | Export as PDF |
| `docs_convert_document` | Convert a non-ODF file to an editable document |

### Accounts

If you have multiple email accounts configured, most tools accept an optional `account_id` parameter. Use this to find the right ID.

| Tool | What it does |
|------|-------------|
| `accounts_list` | List the customer's email accounts |

## Requirements

- Node.js 22 or later
- A LocoRooSuite instance with API access enabled
- An API token (generate one in your LocoRooSuite settings under API Tokens)

## License

MIT. The main LocoRooSuite application is [AGPL-3.0](https://codeberg.org/locoroo/locoroosuite/src/branch/main/LICENSE).
