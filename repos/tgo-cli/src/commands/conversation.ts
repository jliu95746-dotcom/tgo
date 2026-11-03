import { Command } from 'commander';
import { randomUUID } from 'node:crypto';
import { TgoClient } from '../client.js';
import { resolveOutput, resolveServer } from '../config.js';
import { printError, printResult } from '../output.js';
import { SessionCommandJournal } from '../sessionCommandJournal.js';
import { submitSessionCommand, syncSessionCommandReceipt } from '../sessionCommandRecovery.js';

function makeClient(globals: Record<string, string>) {
  return new TgoClient({ server: resolveServer(globals.server), token: globals.token });
}

function resolveOperationId(value?: string): string {
  const operationId = value ?? randomUUID();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(operationId)) {
    throw new Error('操作编号必须是 UUIDv4');
  }
  return operationId;
}

function withOperationId(result: unknown, operationId: string): Record<string, unknown> {
  if (result !== null && typeof result === 'object' && !Array.isArray(result)) {
    return { ...result, operation_id: operationId };
  }
  return { operation_id: operationId, result };
}

export function registerConversationCommands(parent: Command): void {
  const conv = parent.command('conversation').alias('conv').description('Conversation management');

  conv
    .command('list')
    .description('List conversations')
    .option('--scope <scope>', 'Scope: mine, waiting, all', 'mine')
    .option('--limit <n>', 'Limit results', '20')
    .option('--offset <n>', 'Offset', '0')
    .option('--msg-count <n>', 'Messages per conversation', '20')
    .action(async (opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      const format = resolveOutput(globals.output);
      try {
        const client = makeClient(globals);
        const result = await conversationList(client, {
          scope: opts.scope,
          limit: parseInt(opts.limit),
          offset: parseInt(opts.offset),
          msgCount: parseInt(opts.msgCount),
        });
        printResult(result, format);
      } catch (err) {
        printError(err, format);
      }
    });

  conv
    .command('accept <visitor-id>')
    .description('Accept a visitor from the waiting queue')
    .action(async (visitorId, _opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      const format = resolveOutput(globals.output);
      try {
        const client = makeClient(globals);
        const result = await conversationAccept(client, visitorId);
        printResult(result, format);
      } catch (err) {
        printError(err, format);
      }
    });

  conv
    .command('transfer <visitor-id>')
    .description('Transfer a visitor to another staff')
    .requiredOption('--to <staff-id>', 'Target staff ID')
    .option('--reason <text>', 'Transfer reason')
    .option('--operation-id <uuid>', '原操作 UUIDv4，供结果查询与重试')
    .option('--new-operation', '明确发起新操作；仅在上一操作已确认后可用')
    .action(async (visitorId, opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      const format = resolveOutput(globals.output);
      try {
        const client = makeClient(globals);
        const result = await submitSessionCommand(client, new SessionCommandJournal(), {
          kind: 'transfer', visitorId, targetStaffId: opts.to, reason: opts.reason,
          operationId: opts.operationId, newOperation: opts.newOperation,
        }, operationId => process.stderr.write(`原操作编号: ${operationId}\n`));
        printResult(withOperationId(result.receipt ?? result.response, result.operationId), format);
      } catch (err) {
        printError(err, format);
      }
    });

  conv
    .command('close <visitor-id>')
    .description('Close a visitor session')
    .option('--operation-id <uuid>', '原操作 UUIDv4，供结果查询与重试')
    .option('--new-operation', '明确发起新操作；仅在上一操作已确认后可用')
    .action(async (visitorId, opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      const format = resolveOutput(globals.output);
      try {
        const client = makeClient(globals);
        const result = await submitSessionCommand(client, new SessionCommandJournal(), {
          kind: 'close', visitorId, operationId: opts.operationId, newOperation: opts.newOperation,
        }, operationId => process.stderr.write(`原操作编号: ${operationId}\n`));
        printResult(withOperationId(result.receipt ?? result.response, result.operationId), format);
      } catch (err) {
        printError(err, format);
      }
    });

  conv
    .command('receipt <operation-id>')
    .description('查询原关闭或转接操作的回执')
    .action(async (operationId, _opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      const format = resolveOutput(globals.output);
      try {
        const client = makeClient(globals);
        printResult(await syncSessionCommandReceipt(client, new SessionCommandJournal(), resolveOperationId(operationId)), format);
      } catch (err) {
        printError(err, format);
      }
    });

  conv
    .command('waiting-count')
    .description('Get waiting queue count')
    .action(async (_opts, cmd) => {
      const globals = cmd.parent!.parent!.opts();
      const format = resolveOutput(globals.output);
      try {
        const client = makeClient(globals);
        const result = await conversationWaitingCount(client);
        printResult(result, format);
      } catch (err) {
        printError(err, format);
      }
    });
}

// Standalone action functions for MCP
export async function conversationList(
  client: TgoClient,
  params: { scope?: string; limit?: number; offset?: number; msgCount?: number },
): Promise<unknown> {
  const msgCount = params.msgCount ?? 20;
  const limit = params.limit ?? 20;
  const offset = params.offset ?? 0;
  const scope = params.scope ?? 'mine';

  switch (scope) {
    case 'waiting':
      return client.post(`/v1/conversations/waiting?msg_count=${msgCount}&limit=${limit}&offset=${offset}`, {});
    case 'all':
      return client.post(`/v1/conversations/all`, { msg_count: msgCount, limit, offset });
    default: // mine
      return client.post(`/v1/conversations/my`, { msg_count: msgCount });
  }
}

export async function conversationAccept(client: TgoClient, visitorId: string): Promise<unknown> {
  return client.post(`/v1/visitors/${visitorId}/accept`, {});
}

export async function conversationTransfer(
  client: TgoClient,
  visitorId: string,
  targetStaffId: string,
  reason?: string,
  operationId?: string,
): Promise<unknown> {
  const id = resolveOperationId(operationId);
  return client.post(`/v1/sessions/visitor/${visitorId}/transfer`, {
    target_staff_id: targetStaffId,
    ...(reason ? { reason } : {}),
  }, { 'X-Session-Operation-ID': id });
}

export async function conversationClose(client: TgoClient, visitorId: string, operationId?: string): Promise<unknown> {
  const id = resolveOperationId(operationId);
  return client.post(`/v1/sessions/visitor/${visitorId}/close`, {}, {
    'X-Session-Operation-ID': id,
  });
}

export async function conversationReceipt(client: TgoClient, operationId: string): Promise<unknown> {
  return client.get(`/v1/sessions/operations/${resolveOperationId(operationId)}`);
}

export async function conversationWaitingCount(client: TgoClient): Promise<unknown> {
  return client.get('/v1/visitor-waiting-queue/count');
}
