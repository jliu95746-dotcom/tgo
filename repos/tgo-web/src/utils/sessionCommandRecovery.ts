/** Keep only operation identifiers needed to recover a lost session response. */
import type {
  PendingSessionCommand,
  SessionCommandKind,
  SessionCommandReceipt,
  SessionCommandScope,
} from '../types/sessionCommand';

const prefix = 'tgo:session-command:v1:';
export const sessionCommandRecoveryChanged = 'tgo:session-command-recovery-changed';
export const sessionCommandResolved = 'tgo:session-command-resolved';
const uuidV4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function notifyChanged(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(sessionCommandRecoveryChanged));
}

function key(scope: SessionCommandScope, visitorId: string): string {
  return `${prefix}${scope.projectId}:${scope.actorId}:${visitorId}`;
}

function valid(value: unknown, scope: SessionCommandScope, visitorId: string): value is PendingSessionCommand {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return Object.keys(row).length === 7
    && row.projectId === scope.projectId
    && row.actorId === scope.actorId
    && row.visitorId === visitorId
    && typeof row.operationId === 'string' && uuidV4.test(row.operationId)
    && (row.kind === 'close' || row.kind === 'transfer')
    && (row.kind === 'close' ? row.targetStaffId === null
      : typeof row.targetStaffId === 'string' && uuidV4.test(row.targetStaffId))
    && typeof row.createdAt === 'string' && Number.isFinite(Date.parse(row.createdAt));
}

export function readPendingSessionCommand(
  storage: Storage, scope: SessionCommandScope, visitorId: string,
): PendingSessionCommand | null {
  try {
    const raw = storage.getItem(key(scope, visitorId));
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    return valid(value, scope, visitorId) ? value : null;
  } catch { return null; }
}

export function listPendingSessionCommands(
  storage: Storage, scope: SessionCommandScope,
): PendingSessionCommand[] {
  const result: PendingSessionCommand[] = [];
  const scopePrefix = `${prefix}${scope.projectId}:${scope.actorId}:`;
  try {
    for (let index = 0; index < storage.length && result.length < 50; index++) {
      const storageKey = storage.key(index);
      if (!storageKey?.startsWith(scopePrefix)) continue;
      const visitorId = storageKey.slice(scopePrefix.length);
      const row = readPendingSessionCommand(storage, scope, visitorId);
      if (row) result.push(row);
    }
  } catch { return result; }
  return result;
}

export function savePendingSessionCommand(
  storage: Storage, command: PendingSessionCommand,
): boolean {
  if (!valid(command, command, command.visitorId)) return false;
  const existing = readPendingSessionCommand(storage, command, command.visitorId);
  if (existing && existing.operationId !== command.operationId) return false;
  try {
    storage.setItem(key(command, command.visitorId), JSON.stringify(command));
    const saved = readPendingSessionCommand(storage, command, command.visitorId)?.operationId
      === command.operationId;
    if (saved) notifyChanged();
    return saved;
  } catch { return false; }
}

export function clearPendingSessionCommand(
  storage: Storage, scope: SessionCommandScope, visitorId: string,
  operationId: string,
): void {
  try {
    if (readPendingSessionCommand(storage, scope, visitorId)?.operationId === operationId) {
      storage.removeItem(key(scope, visitorId));
      notifyChanged();
    }
  } catch { /* A later recovery can read the original pointer. */ }
}

export function clearAllSessionCommandRecoveries(storage: Storage): void {
  try {
    for (let index = storage.length - 1; index >= 0; index--) {
      const storageKey = storage.key(index);
      if (storageKey?.startsWith(prefix)) storage.removeItem(storageKey);
    }
    notifyChanged();
  } catch { /* Scoping still prevents another account from reading old pointers. */ }
}

export interface SessionCommandTransport {
  send: (command: PendingSessionCommand) => Promise<void>;
  lookup: (operationId: string) => Promise<SessionCommandReceipt | null>;
}

export type SessionCommandOutcome =
  | { state: 'committed'; command: PendingSessionCommand; receipt: SessionCommandReceipt | null }
  | { state: 'pending'; command: PendingSessionCommand }
  | { state: 'rejected'; command: PendingSessionCommand; message: string };

function definitiveRejection(error: unknown): boolean {
  if (!error || typeof error !== 'object' || !('status' in error)) return false;
  return [400, 401, 403, 404, 422].includes(error.status as number);
}

function rejectionMessage(error: unknown): string {
  return error instanceof Error ? error.message : '操作未提交';
}

async function inspectPending(
  storage: Storage, command: PendingSessionCommand, transport: SessionCommandTransport,
): Promise<{ outcome: SessionCommandOutcome; retryAllowed: boolean }> {
  let receipt: SessionCommandReceipt | null = null;
  try { receipt = await transport.lookup(command.operationId); }
  catch { return { outcome: { state: 'pending', command }, retryAllowed: false }; }
  if (receipt?.operation_id === command.operationId
      && receipt.visitor_id === command.visitorId
      && receipt.kind === command.kind
      && receipt.to_staff_id === command.targetStaffId) {
    clearPendingSessionCommand(storage, command, command.visitorId, command.operationId);
    return { outcome: { state: 'committed', command, receipt }, retryAllowed: false };
  }
  return { outcome: { state: 'pending', command }, retryAllowed: receipt === null };
}

async function confirmPending(
  storage: Storage, command: PendingSessionCommand, transport: SessionCommandTransport,
): Promise<SessionCommandOutcome> {
  return (await inspectPending(storage, command, transport)).outcome;
}

export async function beginSessionCommand(
  storage: Storage,
  scope: SessionCommandScope,
  visitorId: string,
  kind: SessionCommandKind,
  targetStaffId: string | null,
  transport: SessionCommandTransport,
  createId: () => string = () => crypto.randomUUID(),
): Promise<SessionCommandOutcome> {
  const existing = readPendingSessionCommand(storage, scope, visitorId);
  if (existing) return confirmPending(storage, existing, transport);
  const command: PendingSessionCommand = {
    ...scope, visitorId, kind, targetStaffId,
    operationId: createId(), createdAt: new Date().toISOString(),
  };
  if (!savePendingSessionCommand(storage, command)) {
    throw new Error('无法保存会话操作编号，请检查浏览器存储设置后重试');
  }
  try {
    await transport.send(command);
    clearPendingSessionCommand(storage, scope, visitorId, command.operationId);
    return { state: 'committed', command, receipt: null };
  } catch (error) {
    const confirmed = await confirmPending(storage, command, transport);
    if (confirmed.state === 'pending' && definitiveRejection(error)) {
      clearPendingSessionCommand(storage, scope, visitorId, command.operationId);
      return { state: 'rejected', command, message: rejectionMessage(error) };
    }
    return confirmed;
  }
}

export async function retryPendingSessionCommand(
  storage: Storage,
  scope: SessionCommandScope,
  visitorId: string,
  transport: SessionCommandTransport,
): Promise<SessionCommandOutcome | null> {
  const command = readPendingSessionCommand(storage, scope, visitorId);
  if (!command) return null;
  const inspected = await inspectPending(storage, command, transport);
  if (inspected.outcome.state === 'committed' || !inspected.retryAllowed) return inspected.outcome;
  try {
    await transport.send(command);
    clearPendingSessionCommand(storage, scope, visitorId, command.operationId);
    return { state: 'committed', command, receipt: null };
  } catch (error) {
    const confirmed = await confirmPending(storage, command, transport);
    if (confirmed.state === 'pending' && definitiveRejection(error)) {
      clearPendingSessionCommand(storage, scope, visitorId, command.operationId);
      return { state: 'rejected', command, message: rejectionMessage(error) };
    }
    return confirmed;
  }
}

export async function checkPendingSessionCommand(
  storage: Storage,
  scope: SessionCommandScope,
  visitorId: string,
  transport: SessionCommandTransport,
): Promise<SessionCommandOutcome | null> {
  const command = readPendingSessionCommand(storage, scope, visitorId);
  return command ? confirmPending(storage, command, transport) : null;
}
