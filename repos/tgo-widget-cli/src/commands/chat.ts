import { Command } from 'commander';
import { readFileSync } from 'node:fs';
import { basename } from 'node:path';
import { TgoWidgetClient, type VisitorRequestProof } from '../client.js';
import { loadConfig, resolveOutput, resolveServer, resolveApiKey, type TgoWidgetConfig } from '../config.js';
import { printError, printResult } from '../output.js';
import { ensureWuKongIMConnected, getSharedWuKongIMClient } from '../wukongim.js';
import { normalizeCustomerSessions, readCustomerSessionsFromStdin, type BusinessCustomerSessionInput } from '../businessCustomerSession.js';
import { sendBusinessChat } from '../businessChat.js';

function makeClient(globals: Record<string, string>) {
  return new TgoWidgetClient({ server: resolveServer(globals.server), apiKey: resolveApiKey(globals.apiKey) });
}

function currentVisitorProof(client: TgoWidgetClient, config: TgoWidgetConfig): VisitorRequestProof {
  if (!config.visitor_id || !config.im_token || !config.server || config.api_key !== client.apiKey
      || config.server.replace(/\/+$/, '') !== client.serverUrl) {
    throw new Error('当前访客访问与服务器不匹配，请重新初始化。');
  }
  const token = config.im_token;
  return {imToken:token, assertCurrent:() => {
    const current = loadConfig();
    if (current.server !== config.server || current.api_key !== config.api_key
        || current.visitor_id !== config.visitor_id || current.im_token !== token
        || current.channel_id !== config.channel_id || current.channel_type !== config.channel_type) {
      throw new Error('当前聊天访问已变化，请重新初始化后重试。');
    }
  }};
}

export function registerChatCommands(parent: Command): void {
  const chat = parent.command('chat').description('Chat messaging');

  chat
    .command('send')
    .description('Send a message and get AI response (SSE streaming by default)')
    .requiredOption('-m, --message <text>', 'Message text')
    .option('--no-stream', 'Disable streaming, return JSON')
    .option('--customer-sessions-stdin', '从标准输入读取本次签名客户会话 JSON，不保存到配置')
    .action(async (opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      const format = resolveOutput(globals.output);
      try {
        const result = await chatSend({
          server: globals.server,
          apiKey: globals.apiKey,
          message: opts.message,
          stream: opts.stream !== false,
          customerSessions: opts.customerSessionsStdin ? readCustomerSessionsFromStdin() : undefined,
        });
        if (!opts.stream || opts.stream === false) {
          printResult(result, format);
        }
        // In stream mode, output is already written to stdout
      } catch (err) {
        printError(err, format);
      }
    });

  chat
    .command('send-ws')
    .description('Send a message via WuKongIM WebSocket (raw IM, no AI reply)')
    .requiredOption('-m, --message <text>', 'Message text')
    .action(async (opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      const format = resolveOutput(globals.output);
      let result: unknown;
      let error: unknown;
      try {
        result = await chatSendWs({
          server: globals.server,
          message: opts.message,
        });
      } catch (err) {
        error = err;
      }
      getSharedWuKongIMClient().disconnect();
      if (error) {
        printError(error, format);
      } else {
        printResult(result, format);
      }
    });

  chat
    .command('listen')
    .description('Listen for incoming messages via WuKongIM (JSONL output)')
    .option('--events', 'Also print custom events')
    .action(async (opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      try {
        await chatListen({
          server: globals.server,
          includeEvents: opts.events,
        });
      } catch (err) {
        printError(err, resolveOutput(globals.output));
      }
    });

  chat
    .command('history')
    .description('Get message history')
    .option('-l, --limit <n>', 'Max messages', '20')
    .option('--start-seq <n>', 'Start message sequence number')
    .action(async (opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      const format = resolveOutput(globals.output);
      try {
        const result = await chatHistory({
          server: globals.server,
          apiKey: globals.apiKey,
          limit: parseInt(opts.limit),
          startSeq: opts.startSeq ? parseInt(opts.startSeq) : undefined,
        });
        printResult(result, format);
      } catch (err) {
        printError(err, format);
      }
    });

  chat
    .command('upload <file>')
    .description('Upload a file')
    .action(async (file, _opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      const format = resolveOutput(globals.output);
      try {
        const result = await chatUpload({
          server: globals.server,
          apiKey: globals.apiKey,
          filePath: file,
        });
        printResult(result, format);
      } catch (err) {
        printError(err, format);
      }
    });
}

// -- Action functions (shared by CLI + MCP) --

/**
 * Send a message via /v1/chat/completion.
 * Stream mode (default): SSE streaming to stdout, returns accumulated text.
 * Non-stream mode: returns full JSON response.
 */
export async function chatSend(params: {
  server?: string;
  apiKey?: string;
  message: string;
  stream?: boolean;
  customerSessions?: readonly BusinessCustomerSessionInput[];
}): Promise<unknown> {
  const config = loadConfig();
  const server = params.server || resolveServer();
  const apiKey = params.apiKey || resolveApiKey();
  if (!server) throw new Error('No server configured. Run: tgo-widget init');
  if (!apiKey) throw new Error('No API key configured. Run: tgo-widget init');
  if (!config.platform_open_id) throw new Error('No platform_open_id. Run: tgo-widget init');

  const client = new TgoWidgetClient({ server, apiKey });
  const proof = currentVisitorProof(client, config);

  const body: Record<string, unknown> = {
    api_key: apiKey,
    message: params.message,
    from_uid: config.platform_open_id,
    stream: params.stream !== false,
  };
  if (config.channel_id) body.channel_id = config.channel_id;
  if (config.channel_type != null) body.channel_type = config.channel_type;
  const sessions = normalizeCustomerSessions(params.customerSessions || []);
  if (sessions.length) body.customer_sessions = sessions;
  const assertIdentity = () => {
    const current = loadConfig();
    if (current.platform_open_id !== config.platform_open_id || current.channel_id !== config.channel_id
      || current.channel_type !== config.channel_type || (params.server ? params.server : resolveServer()) !== server
      || (params.apiKey ? params.apiKey : resolveApiKey()) !== apiKey) throw new Error('聊天身份已变化，请重新发送。');
  };
  return sendBusinessChat(client, body, sessions, assertIdentity, proof);
}

/**
 * Send a message via WuKongIM WebSocket (raw IM, no AI reply).
 */
export async function chatSendWs(params: {
  server?: string;
  message: string;
}): Promise<unknown> {
  const config = loadConfig();
  if (!config.channel_id) throw new Error('No channel_id. Run: tgo-widget init');
  if (config.channel_type == null) throw new Error('No channel_type. Run: tgo-widget init');

  const wkim = await ensureWuKongIMConnected(params.server);
  const payload = { content: params.message, type: 1 };
  const clientMsgNo = generateClientMsgNo();

  const result = await wkim.send(config.channel_id, config.channel_type, payload, clientMsgNo);

  return {
    messageId: result.messageId,
    messageSeq: result.messageSeq,
    clientMsgNo,
    status: 'sent',
  };
}

/**
 * Listen for incoming messages via WuKongIM WebSocket.
 * Outputs JSONL to stdout. Runs until Ctrl+C.
 */
export async function chatListen(params?: {
  server?: string;
  includeEvents?: boolean;
}): Promise<void> {
  const wkim = await ensureWuKongIMConnected(params?.server);

  console.error(`Connected as ${wkim.uid}. Listening for messages... (Ctrl+C to stop)`);

  wkim.onMessage((msg) => {
    const line = JSON.stringify({
      type: 'message',
      messageId: msg.messageId,
      messageSeq: msg.messageSeq,
      timestamp: msg.timestamp,
      channelId: msg.channelId,
      channelType: msg.channelType,
      fromUid: msg.fromUid,
      payload: msg.payload,
      clientMsgNo: msg.clientMsgNo,
    });
    console.log(line);
  });

  if (params?.includeEvents) {
    wkim.onEvent((evt) => {
      const line = JSON.stringify({
        type: 'event',
        id: evt.id,
        eventType: evt.type,
        timestamp: evt.timestamp,
        data: evt.data,
      });
      console.log(line);
    });
  }

  // Keep alive
  await new Promise<void>(() => {});
}

/**
 * Get message history via POST /v1/visitors/messages/sync.
 */
export async function chatHistory(params: {
  server?: string;
  apiKey?: string;
  limit?: number;
  startSeq?: number;
}): Promise<unknown> {
  const config = loadConfig();
  const apiKey = params.apiKey || resolveApiKey();
  if (!apiKey) throw new Error('No API key configured. Run: tgo-widget init');
  if (!config.channel_id) throw new Error('No channel_id. Run: tgo-widget init');
  if (config.channel_type == null) throw new Error('No channel_type. Run: tgo-widget init');

  const client = new TgoWidgetClient({ server: params.server || resolveServer(), apiKey });
  const proof = currentVisitorProof(client, config);
  return client.postVisitor('/v1/visitors/messages/sync', {
    platform_api_key: apiKey,
    channel_id: config.channel_id,
    channel_type: config.channel_type,
    start_message_seq: params.startSeq ?? null,
    limit: params.limit ?? 20,
    pull_mode: 0,
  }, proof);
}

/**
 * Upload a file via POST /v1/chat/upload.
 */
export async function chatUpload(params: {
  server?: string;
  apiKey?: string;
  filePath: string;
}): Promise<unknown> {
  const config = loadConfig();
  const apiKey = params.apiKey || resolveApiKey();
  if (!apiKey) throw new Error('No API key configured. Run: tgo-widget init');
  if (!config.channel_id) throw new Error('No channel_id. Run: tgo-widget init');
  if (config.channel_type == null) throw new Error('No channel_type. Run: tgo-widget init');

  const client = new TgoWidgetClient({ server: params.server || resolveServer(), apiKey });

  const proof = currentVisitorProof(client, config);
  proof.assertCurrent();
  const fileBuffer = readFileSync(params.filePath);
  const fileName = basename(params.filePath);
  const blob = new Blob([fileBuffer]);

  const formData = new FormData();
  formData.append('file', blob, fileName);
  formData.append('channel_id', config.channel_id);
  formData.append('channel_type', String(config.channel_type));

  return client.postVisitorFormData('/v1/chat/upload', formData, proof);
}

// -- Helpers --

function generateClientMsgNo(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
