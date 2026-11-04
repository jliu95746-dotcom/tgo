# tgo-cli Command Reference

> **IMPORTANT**: This document must stay in sync with the source code.
> When adding, removing, or modifying any CLI command or MCP tool,
> update the corresponding section below.

## Global Options

| Flag | Description |
|------|-------------|
| `-s, --server <url>` | API server URL |
| `-t, --token <token>` | Auth token |
| `-o, --output <format>` | Output format: `json` (default), `table`, `compact` |
| `-v, --verbose` | Verbose output |

---

## CLI Commands

### auth

Source: `src/commands/auth.ts`

| Command | Description | Options / Arguments |
|---------|-------------|---------------------|
| `tgo auth login` | Login and save token | `-u, --user <username>` (required), `-p, --pass <password>` (required) |
| `tgo auth logout` | Clear saved token | — |
| `tgo auth whoami` | Show current login info | — |

登录响应仅在本次请求仍是最新登录操作时保存。若等待响应期间执行了新的登录或登出，旧登录会报错且不会改写当前令牌。登出只清除本地令牌并取消尚未完成的本地登录，不代表服务端令牌已被撤销；MCP 的 `tgo_auth_login` 和 `tgo_auth_logout` 使用同一规则。

### conversation (alias: `conv`)

Source: `src/commands/conversation.ts`

| Command | Description | Options / Arguments |
|---------|-------------|---------------------|
| `tgo conversation list` | List conversations | `--scope <mine\|waiting\|all>` (default: mine), `--limit <n>`, `--offset <n>`, `--msg-count <n>` |
| `tgo conversation accept <visitor-id>` | Accept a visitor from waiting queue | `visitor-id` (required) |
| `tgo conversation transfer <visitor-id>` | Transfer visitor to another staff; persist and print the operation ID before submitting | `visitor-id` (required), `--to <staff-id>` (required), `--reason <text>`, `--operation-id <uuid>` (explicit retry of the original), `--new-operation` (start another action only after the last is resolved) |
| `tgo conversation close <visitor-id>` | Close a visitor session; persist and print the operation ID before submitting | `visitor-id` (required), `--operation-id <uuid>` (explicit retry of the original), `--new-operation` (start another action only after the last is resolved) |
| `tgo conversation receipt <operation-id>` | Query the original close or transfer receipt and reconcile the matching local record | `operation-id` (UUIDv4, required) |
| `tgo conversation waiting-count` | Get waiting queue count | — |

Close and transfer keep the latest operation pointer per server, project, staff member and visitor in `~/.tgo/session-commands/`. The file contains only IDs, command kind, target staff ID, status and timestamps; it contains no token, password, transfer reason or message. If the CLI exits after reserving an ID, rerun the same command to query its receipt. A pending operation with no receipt is never resent automatically: first verify the intended action, then pass the **same** `--operation-id` to retry. Use `--new-operation` only after the prior operation is confirmed and a separate close or transfer is intended. MCP tools require the caller to retain their own supplied operation ID; they do not use the CLI's local journal.

### chat

Source: `src/commands/chat.ts`

| Command | Description | Options / Arguments |
|---------|-------------|---------------------|
| `tgo chat send` | Send message via WuKongIM WebSocket | `--channel <id>` (required), `--type <n>` (required), `--message <text>` (required) |
| `tgo chat agent` | Chat with AI agent | `--message <text>` (required), `--agent <id>` (required) |
| `tgo chat clear-memory` | Clear AI memory for a channel | `--channel <id>` (required), `--type <n>` (required) |
| `tgo chat listen` | Listen for incoming messages (JSONL) | `--channel <id>`, `--events` |

### visitor

Source: `src/commands/visitor.ts`

| Command | Description | Options / Arguments |
|---------|-------------|---------------------|
| `tgo visitor list` | List visitors | `--online`, `--search <q>`, `--tag <id>` (repeatable), `--platform <id>`, `--limit <n>`, `--offset <n>` |
| `tgo visitor get <id>` | Get visitor details | `id` (required) |
| `tgo visitor update <id>` | Update visitor attributes | `id` (required), `--name`, `--email`, `--phone`, `--company`, `--note` |
| `tgo visitor enable-ai <id>` | Enable AI for visitor | `id` (required) |
| `tgo visitor disable-ai <id>` | Disable AI for visitor | `id` (required) |

### agent

Source: `src/commands/agent.ts`

| Command | Description | Options / Arguments |
|---------|-------------|---------------------|
| `tgo agent list` | List AI agents | `--limit <n>`, `--offset <n>` |
| `tgo agent get <id>` | Get agent details | `id` (required) |
| `tgo agent create` | Create a new AI agent | `--name <n>` (required), `--model <m>` (required), `--instructions <text>`, `--provider-id <id>` |
| `tgo agent update <id>` | Update an AI agent | `id` (required), `--name`, `--model`, `--instructions` |
| `tgo agent delete <id>` | Delete an AI agent | `id` (required) |

### provider

Source: `src/commands/provider.ts`

| Command | Description | Options / Arguments |
|---------|-------------|---------------------|
| `tgo provider list` | List AI providers | — |
| `tgo provider create` | Create a new AI provider | `--name <n>` (required), `--provider <type>` (required), `--api-key <key>` (required), `--api-base <url>` |
| `tgo provider test <id>` | Test provider connection | `id` (required) |
| `tgo provider enable <id>` | Enable a provider | `id` (required) |
| `tgo provider disable <id>` | Disable a provider | `id` (required) |

### knowledge (alias: `kb`)

Source: `src/commands/knowledge.ts`

| Command | Description | Options / Arguments |
|---------|-------------|---------------------|
| `tgo knowledge list` | List knowledge collections | `--limit <n>`, `--offset <n>` |
| `tgo knowledge get <id>` | Get collection details | `id` (required) |
| `tgo knowledge create` | Create a collection | `--name <n>` (required), `--description <d>` |
| `tgo knowledge search <collection-id>` | Search documents | `collection-id` (required), `--query <text>` (required), `--limit <n>` |
| `tgo knowledge upload <collection-id> <file>` | Upload a file | `collection-id` (required), `file` (required) |
| `tgo knowledge delete <id>` | Delete a collection | `id` (required) |

### workflow (alias: `wf`)

Source: `src/commands/workflow.ts`

| Command | Description | Options / Arguments |
|---------|-------------|---------------------|
| `tgo workflow list` | List workflows | `--limit <n>`, `--offset <n>` |
| `tgo workflow get <id>` | Get workflow details | `id` (required) |
| `tgo workflow execute <id>` | Execute a workflow | `id` (required), `--input <json>` |
| `tgo workflow validate <id>` | Validate a workflow | `id` (required) |

### staff

Source: `src/commands/staff.ts`

| Command | Description | Options / Arguments |
|---------|-------------|---------------------|
| `tgo staff list` | List staff members | `--role <r>`, `--status <s>`, `--limit <n>`, `--offset <n>` |
| `tgo staff get <id>` | Get staff details | `id` (required) |
| `tgo staff pause [id]` | Pause service (self if no ID) | `id` (optional) |
| `tgo staff resume [id]` | Resume service (self if no ID) | `id` (optional) |

### platform

Source: `src/commands/platform.ts`

| Command | Description | Options / Arguments |
|---------|-------------|---------------------|
| `tgo platform list` | List platforms | — |
| `tgo platform get <id>` | Get platform details | `id` (required) |

### tag

Source: `src/commands/tag.ts`

| Command | Description | Options / Arguments |
|---------|-------------|---------------------|
| `tgo tag list` | List tags | `--category <cat>` |
| `tgo tag create` | Create a tag | `--name <n>` (required), `--category <cat>`, `--color <hex>` |
| `tgo tag delete <id>` | Delete a tag | `id` (required) |

### system

Source: `src/commands/system.ts`

| Command | Description | Options / Arguments |
|---------|-------------|---------------------|
| `tgo system info` | Get system information | — |

### mcp

Source: `src/index.ts`, `src/mcp/server.ts`

| Command | Description | Options / Arguments |
|---------|-------------|---------------------|
| `tgo mcp serve` | Start MCP Server (stdio) | — |

---

## MCP Tools

Source: `src/mcp/server.ts`

Each tool is named `tgo_<resource>_<action>` and maps to the corresponding CLI command.

| MCP Tool | Description | Parameters |
|----------|-------------|------------|
| `tgo_auth_login` | Login to TGO and save token | `server` (string), `username` (string), `password` (string) |
| `tgo_auth_logout` | Clear saved auth token | — |
| `tgo_auth_whoami` | Get current logged-in user info | — |
| `tgo_conversation_list` | List conversations | `scope` (mine/waiting/all), `limit`, `offset`, `msg_count` |
| `tgo_conversation_accept` | Accept visitor from waiting queue | `visitor_id` (string) |
| `tgo_conversation_transfer` | Transfer visitor to another staff | `visitor_id`, `target_staff_id`, `reason?`, `operation_id` (caller-owned UUIDv4, required) |
| `tgo_conversation_close` | Close a visitor session | `visitor_id`, `operation_id` (caller-owned UUIDv4, required) |
| `tgo_conversation_receipt` | Query the original close or transfer receipt | `operation_id` (UUIDv4, required) |
| `tgo_conversation_waiting_count` | Get waiting queue count | — |
| `tgo_chat_send` | Send message via WuKongIM WebSocket | `channel_id`, `channel_type` (number), `message` |
| `tgo_chat_send_platform` | Send message via HTTP API (non-website) | `channel_id`, `channel_type` (number), `message` |
| `tgo_chat_agent` | Chat with AI agent | `message`, `agent_id` |
| `tgo_chat_clear_memory` | Clear AI memory for a channel | `channel_id`, `channel_type` (number) |
| `tgo_visitor_list` | List visitors with filtering | `is_online?`, `search?`, `tag_ids?`, `platform_id?`, `limit`, `offset` |
| `tgo_visitor_get` | Get visitor details | `id` (string) |
| `tgo_visitor_update` | Update visitor attributes | `id`, `name?`, `email?`, `phone_number?`, `company?`, `note?` |
| `tgo_visitor_enable_ai` | Enable AI for a visitor | `id` (string) |
| `tgo_visitor_disable_ai` | Disable AI for a visitor | `id` (string) |
| `tgo_agent_list` | List AI agents | `limit`, `offset` |
| `tgo_agent_get` | Get AI agent details | `id` (string) |
| `tgo_agent_create` | Create a new AI agent | `name`, `model`, `instruction?`, `ai_provider_id?` |
| `tgo_agent_update` | Update an AI agent | `id`, `name?`, `model?`, `instruction?` |
| `tgo_agent_delete` | Delete an AI agent | `id` (string) |
| `tgo_provider_list` | List AI providers | — |
| `tgo_provider_create` | Create a new AI provider | `name`, `provider_type`, `api_key`, `api_base?` |
| `tgo_provider_test` | Test provider connection | `id` (string) |
| `tgo_provider_enable` | Enable a provider | `id` (string) |
| `tgo_provider_disable` | Disable a provider | `id` (string) |
| `tgo_knowledge_list` | List knowledge collections | `limit`, `offset` |
| `tgo_knowledge_get` | Get collection details | `id` (string) |
| `tgo_knowledge_create` | Create a knowledge collection | `display_name`, `description?` |
| `tgo_knowledge_search` | Search documents in collection | `collection_id`, `query`, `limit?` |
| `tgo_knowledge_delete` | Delete a knowledge collection | `id` (string) |
| `tgo_workflow_list` | List workflows | `limit`, `offset` |
| `tgo_workflow_get` | Get workflow details | `id` (string) |
| `tgo_workflow_execute` | Execute a workflow | `id`, `input?` (object) |
| `tgo_workflow_validate` | Validate a workflow | `id` (string) |
| `tgo_staff_list` | List staff members | `role?`, `status?`, `limit`, `offset` |
| `tgo_staff_get` | Get staff member details | `id` (string) |
| `tgo_staff_pause` | Pause service (self if no ID) | `id?` (string) |
| `tgo_staff_resume` | Resume service (self if no ID) | `id?` (string) |
| `tgo_platform_list` | List platforms | — |
| `tgo_platform_get` | Get platform details | `id` (string) |
| `tgo_tag_list` | List tags | `category?` |
| `tgo_tag_create` | Create a tag | `name`, `category?`, `color?` |
| `tgo_tag_delete` | Delete a tag | `id` (string) |
| `tgo_system_info` | Get system information | — |

### CLI-only Commands (no MCP tool)

| Command | Reason |
|---------|--------|
| `tgo chat listen` | Long-running process, not suitable for MCP request/response |
| `tgo knowledge upload` | Requires local file path, not suitable for MCP |
| `tgo mcp serve` | Starts the MCP server itself |
