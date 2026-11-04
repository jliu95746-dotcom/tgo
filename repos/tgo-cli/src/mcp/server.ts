import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { TgoClient } from '../client.js';
import { resolveServer, resolveToken } from '../config.js';

// Auth
import { authLogin, authLogout, authWhoami } from '../commands/auth.js';
// Conversation
import {
  conversationList,
  conversationAccept,
  conversationTransfer,
  conversationClose,
  conversationReceipt,
  conversationWaitingCount,
} from '../commands/conversation.js';
// Chat
import { chatSend, chatSendPlatform, chatAgent, chatClearMemory } from '../commands/chat.js';
// Visitor
import {
  visitorList,
  visitorGet,
  visitorUpdate,
  visitorEnableAi,
  visitorDisableAi,
} from '../commands/visitor.js';
// Agent
import {
  agentList,
  agentGet,
  agentCreate,
  agentUpdate,
  agentDelete,
} from '../commands/agent.js';
// Provider
import {
  providerList,
  providerCreate,
  providerTest,
  providerEnable,
  providerDisable,
} from '../commands/provider.js';
// Knowledge
import {
  knowledgeList,
  knowledgeGet,
  knowledgeCreate,
  knowledgeSearch,
  knowledgeDelete,
} from '../commands/knowledge.js';
// Workflow
import {
  workflowList,
  workflowGet,
  workflowExecute,
  workflowValidate,
} from '../commands/workflow.js';
// Staff
import { staffList, staffGet, staffPause, staffResume } from '../commands/staff.js';
// Platform
import { platformList, platformGet } from '../commands/platform.js';
// Tag
import { tagList, tagCreate, tagDelete } from '../commands/tag.js';
// System
import { systemInfo } from '../commands/system.js';

function text(data: unknown): { content: { type: 'text'; text: string }[] } {
  return { content: [{ type: 'text', text: JSON.stringify(data, null, 2) }] };
}

function getClient(): TgoClient {
  return new TgoClient({ server: resolveServer(), token: resolveToken() });
}

export async function startMcpServer(): Promise<void> {
  const server = new McpServer({
    name: 'tgo',
    version: '0.1.0',
  });

  // ─── Auth ───────────────────────────────────────────────

  server.tool(
    'tgo_auth_login',
    'Login to TGO and save token',
    {
      server: z.string().describe('API server URL (e.g. http://localhost:8000/api)'),
      username: z.string().describe('Username or email'),
      password: z.string().describe('Password'),
    },
    async ({ server: srv, username, password }) => text(await authLogin(srv, username, password)),
  );

  server.tool(
    'tgo_auth_logout',
    'Clear saved auth token',
    {},
    async () => text(authLogout()),
  );

  server.tool(
    'tgo_auth_whoami',
    'Get current logged-in user info',
    {},
    async () => text(await authWhoami()),
  );

  // ─── Conversation ──────────────────────────────────────

  server.tool(
    'tgo_conversation_list',
    'List conversations (mine, waiting, or all)',
    {
      scope: z.enum(['mine', 'waiting', 'all']).default('mine').describe('Conversation scope'),
      limit: z.number().optional().default(20).describe('Max results'),
      offset: z.number().optional().default(0).describe('Offset'),
      msg_count: z.number().optional().default(20).describe('Messages per conversation'),
    },
    async (params) => text(await conversationList(getClient(), {
      scope: params.scope,
      limit: params.limit,
      offset: params.offset,
      msgCount: params.msg_count,
    })),
  );

  server.tool(
    'tgo_conversation_accept',
    'Accept a visitor from the waiting queue',
    { visitor_id: z.string().describe('Visitor ID') },
    async ({ visitor_id }) => text(await conversationAccept(getClient(), visitor_id)),
  );

  server.tool(
    'tgo_conversation_transfer',
    'Transfer a visitor to another staff member',
    {
      visitor_id: z.string().describe('Visitor ID'),
      target_staff_id: z.string().describe('Target staff ID'),
      reason: z.string().optional().describe('Transfer reason'),
      operation_id: z.string().uuid().describe('Caller-owned UUIDv4 operation ID'),
    },
    async ({ visitor_id, target_staff_id, reason, operation_id }) =>
      text(await conversationTransfer(getClient(), visitor_id, target_staff_id, reason, operation_id)),
  );

  server.tool(
    'tgo_conversation_close',
    'Close a visitor session',
    {
      visitor_id: z.string().describe('Visitor ID'),
      operation_id: z.string().uuid().describe('Caller-owned UUIDv4 operation ID'),
    },
    async ({ visitor_id, operation_id }) =>
      text(await conversationClose(getClient(), visitor_id, operation_id)),
  );

  server.tool(
    'tgo_conversation_receipt',
    'Look up the original close or transfer operation',
    { operation_id: z.string().uuid().describe('Original operation ID') },
    async ({ operation_id }) => text(await conversationReceipt(getClient(), operation_id)),
  );

  server.tool(
    'tgo_conversation_waiting_count',
    'Get waiting queue count',
    {},
    async () => text(await conversationWaitingCount(getClient())),
  );

  // ─── Chat ──────────────────────────────────────────────

  server.tool(
    'tgo_chat_send',
    'Send a message via WuKongIM WebSocket (works for all platform types including website)',
    {
      channel_id: z.string().describe('Channel ID'),
      channel_type: z.number().describe('Channel type (1=person, 2=group)'),
      message: z.string().describe('Message text'),
    },
    async (params) => text(await chatSend(getClient(), params)),
  );

  server.tool(
    'tgo_chat_send_platform',
    'Send a message via HTTP API to non-website platforms (WeChat, LINE, etc). Use tgo_chat_send for website visitors.',
    {
      channel_id: z.string().describe('Channel ID'),
      channel_type: z.number().describe('Channel type'),
      message: z.string().describe('Message text'),
    },
    async (params) => text(await chatSendPlatform(getClient(), params)),
  );

  server.tool(
    'tgo_chat_agent',
    'Chat with an AI agent',
    {
      message: z.string().describe('Message text'),
      agent_id: z.string().describe('AI Agent ID'),
    },
    async (params) => text(await chatAgent(getClient(), params)),
  );

  server.tool(
    'tgo_chat_clear_memory',
    'Clear AI memory for a channel',
    {
      channel_id: z.string().describe('Channel ID'),
      channel_type: z.number().describe('Channel type'),
    },
    async (params) => text(await chatClearMemory(getClient(), params)),
  );

  // ─── Visitor ───────────────────────────────────────────

  server.tool(
    'tgo_visitor_list',
    'List visitors with filtering',
    {
      is_online: z.boolean().optional().describe('Filter online visitors only'),
      search: z.string().optional().describe('Search query'),
      tag_ids: z.array(z.string()).optional().describe('Filter by tag IDs'),
      platform_id: z.string().optional().describe('Filter by platform ID'),
      limit: z.number().optional().default(20).describe('Max results'),
      offset: z.number().optional().default(0).describe('Offset'),
    },
    async (params) => text(await visitorList(getClient(), params)),
  );

  server.tool(
    'tgo_visitor_get',
    'Get visitor details',
    { id: z.string().describe('Visitor ID') },
    async ({ id }) => text(await visitorGet(getClient(), id)),
  );

  server.tool(
    'tgo_visitor_update',
    'Update visitor attributes',
    {
      id: z.string().describe('Visitor ID'),
      name: z.string().optional().describe('Name'),
      email: z.string().optional().describe('Email'),
      phone_number: z.string().optional().describe('Phone number'),
      company: z.string().optional().describe('Company'),
      note: z.string().optional().describe('Note'),
    },
    async ({ id, ...attrs }) => {
      const filtered = Object.fromEntries(Object.entries(attrs).filter(([, v]) => v !== undefined));
      return text(await visitorUpdate(getClient(), id, filtered));
    },
  );

  server.tool(
    'tgo_visitor_enable_ai',
    'Enable AI for a visitor',
    { id: z.string().describe('Visitor ID') },
    async ({ id }) => text(await visitorEnableAi(getClient(), id)),
  );

  server.tool(
    'tgo_visitor_disable_ai',
    'Disable AI for a visitor',
    { id: z.string().describe('Visitor ID') },
    async ({ id }) => text(await visitorDisableAi(getClient(), id)),
  );

  // ─── Agent ─────────────────────────────────────────────

  server.tool(
    'tgo_agent_list',
    'List AI agents',
    {
      limit: z.number().optional().default(20).describe('Max results'),
      offset: z.number().optional().default(0).describe('Offset'),
    },
    async (params) => text(await agentList(getClient(), params)),
  );

  server.tool(
    'tgo_agent_get',
    'Get AI agent details',
    { id: z.string().describe('Agent ID') },
    async ({ id }) => text(await agentGet(getClient(), id)),
  );

  server.tool(
    'tgo_agent_create',
    'Create a new AI agent',
    {
      name: z.string().describe('Agent name'),
      model: z.string().describe('Model (e.g. openai:gpt-4)'),
      instruction: z.string().optional().describe('Agent instructions'),
      ai_provider_id: z.string().optional().describe('AI provider ID'),
    },
    async (params) => text(await agentCreate(getClient(), params)),
  );

  server.tool(
    'tgo_agent_update',
    'Update an AI agent',
    {
      id: z.string().describe('Agent ID'),
      name: z.string().optional().describe('Agent name'),
      model: z.string().optional().describe('Model'),
      instruction: z.string().optional().describe('Agent instructions'),
    },
    async ({ id, ...data }) => {
      const filtered = Object.fromEntries(Object.entries(data).filter(([, v]) => v !== undefined));
      return text(await agentUpdate(getClient(), id, filtered));
    },
  );

  server.tool(
    'tgo_agent_delete',
    'Delete an AI agent',
    { id: z.string().describe('Agent ID') },
    async ({ id }) => {
      await agentDelete(getClient(), id);
      return text({ success: true, message: `Agent ${id} deleted` });
    },
  );

  // ─── Provider ──────────────────────────────────────────

  server.tool(
    'tgo_provider_list',
    'List AI providers',
    {},
    async () => text(await providerList(getClient())),
  );

  server.tool(
    'tgo_provider_create',
    'Create a new AI provider',
    {
      name: z.string().describe('Provider name'),
      provider_type: z.string().describe('Provider type (openai, azure, qwen, etc.)'),
      api_key: z.string().describe('API key'),
      api_base: z.string().optional().describe('Custom API base URL'),
    },
    async (params) => text(await providerCreate(getClient(), params)),
  );

  server.tool(
    'tgo_provider_test',
    'Test an AI provider connection',
    { id: z.string().describe('Provider ID') },
    async ({ id }) => text(await providerTest(getClient(), id)),
  );

  server.tool(
    'tgo_provider_enable',
    'Enable an AI provider',
    { id: z.string().describe('Provider ID') },
    async ({ id }) => text(await providerEnable(getClient(), id)),
  );

  server.tool(
    'tgo_provider_disable',
    'Disable an AI provider',
    { id: z.string().describe('Provider ID') },
    async ({ id }) => text(await providerDisable(getClient(), id)),
  );

  // ─── Knowledge ─────────────────────────────────────────

  server.tool(
    'tgo_knowledge_list',
    'List knowledge collections',
    {
      limit: z.number().optional().default(20).describe('Max results'),
      offset: z.number().optional().default(0).describe('Offset'),
    },
    async (params) => text(await knowledgeList(getClient(), params)),
  );

  server.tool(
    'tgo_knowledge_get',
    'Get knowledge collection details',
    { id: z.string().describe('Collection ID') },
    async ({ id }) => text(await knowledgeGet(getClient(), id)),
  );

  server.tool(
    'tgo_knowledge_create',
    'Create a knowledge collection',
    {
      display_name: z.string().describe('Collection name'),
      description: z.string().optional().describe('Description'),
    },
    async (params) => text(await knowledgeCreate(getClient(), params)),
  );

  server.tool(
    'tgo_knowledge_search',
    'Search documents in a knowledge collection',
    {
      collection_id: z.string().describe('Collection ID'),
      query: z.string().describe('Search query'),
      limit: z.number().optional().default(10).describe('Max results'),
    },
    async ({ collection_id, ...params }) =>
      text(await knowledgeSearch(getClient(), collection_id, params)),
  );

  server.tool(
    'tgo_knowledge_delete',
    'Delete a knowledge collection',
    { id: z.string().describe('Collection ID') },
    async ({ id }) => {
      await knowledgeDelete(getClient(), id);
      return text({ success: true, message: `Collection ${id} deleted` });
    },
  );

  // ─── Workflow ──────────────────────────────────────────

  server.tool(
    'tgo_workflow_list',
    'List workflows',
    {
      limit: z.number().optional().default(20).describe('Max results'),
      offset: z.number().optional().default(0).describe('Offset'),
    },
    async (params) => text(await workflowList(getClient(), params)),
  );

  server.tool(
    'tgo_workflow_get',
    'Get workflow details',
    { id: z.string().describe('Workflow ID') },
    async ({ id }) => text(await workflowGet(getClient(), id)),
  );

  server.tool(
    'tgo_workflow_execute',
    'Execute a workflow',
    {
      id: z.string().describe('Workflow ID'),
      input: z.record(z.unknown()).optional().describe('Input JSON object'),
    },
    async ({ id, input }) => text(await workflowExecute(getClient(), id, input)),
  );

  server.tool(
    'tgo_workflow_validate',
    'Validate a workflow',
    { id: z.string().describe('Workflow ID') },
    async ({ id }) => text(await workflowValidate(getClient(), id)),
  );

  // ─── Staff ─────────────────────────────────────────────

  server.tool(
    'tgo_staff_list',
    'List staff members',
    {
      role: z.string().optional().describe('Filter by role (user, admin, agent)'),
      status: z.string().optional().describe('Filter by status (online, offline, busy)'),
      limit: z.number().optional().default(20).describe('Max results'),
      offset: z.number().optional().default(0).describe('Offset'),
    },
    async (params) => text(await staffList(getClient(), params)),
  );

  server.tool(
    'tgo_staff_get',
    'Get staff member details',
    { id: z.string().describe('Staff ID') },
    async ({ id }) => text(await staffGet(getClient(), id)),
  );

  server.tool(
    'tgo_staff_pause',
    'Pause service for a staff member (self if no ID)',
    { id: z.string().optional().describe('Staff ID (omit for self)') },
    async ({ id }) => text(await staffPause(getClient(), id)),
  );

  server.tool(
    'tgo_staff_resume',
    'Resume service for a staff member (self if no ID)',
    { id: z.string().optional().describe('Staff ID (omit for self)') },
    async ({ id }) => text(await staffResume(getClient(), id)),
  );

  // ─── Platform ──────────────────────────────────────────

  server.tool(
    'tgo_platform_list',
    'List platforms',
    {},
    async () => text(await platformList(getClient())),
  );

  server.tool(
    'tgo_platform_get',
    'Get platform details',
    { id: z.string().describe('Platform ID') },
    async ({ id }) => text(await platformGet(getClient(), id)),
  );

  // ─── Tag ───────────────────────────────────────────────

  server.tool(
    'tgo_tag_list',
    'List tags',
    { category: z.string().optional().describe('Filter by category (visitor, knowledge)') },
    async (params) => text(await tagList(getClient(), params)),
  );

  server.tool(
    'tgo_tag_create',
    'Create a tag',
    {
      name: z.string().describe('Tag name'),
      category: z.string().optional().default('visitor').describe('Category'),
      color: z.string().optional().describe('Color hex'),
    },
    async (params) => text(await tagCreate(getClient(), params)),
  );

  server.tool(
    'tgo_tag_delete',
    'Delete a tag',
    { id: z.string().describe('Tag ID') },
    async ({ id }) => {
      await tagDelete(getClient(), id);
      return text({ success: true, message: `Tag ${id} deleted` });
    },
  );

  // ─── System ────────────────────────────────────────────

  server.tool(
    'tgo_system_info',
    'Get system information',
    {},
    async () => {
      const srv = resolveServer();
      if (!srv) return text({ error: 'No server configured' });
      return text(await systemInfo(srv));
    },
  );

  // ─── Connect ───────────────────────────────────────────

  const transport = new StdioServerTransport();
  await server.connect(transport);
}
