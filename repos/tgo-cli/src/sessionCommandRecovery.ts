import { ApiError, type TgoClient } from './client.js';
import {
  makeSessionCommandScope, SessionCommandJournal,
  type SessionCommandKind, type SessionCommandRecord, type SessionCommandScope,
} from './sessionCommandJournal.js';

interface StaffIdentity {
  id: string;
  project_id: string;
}

export interface SessionCommandReceipt {
  operation_id: string;
  visitor_id: string;
  kind: SessionCommandKind;
  to_staff_id: string | null;
  notification_status: 'pending' | 'sending' | 'sent' | 'unknown' | 'skipped';
  session_id: string;
  attempts: number;
  effects_status?: 'pending' | 'done' | 'unknown' | 'skipped';
  effects_attempts?: number;
}

export interface SubmitSessionCommandOptions {
  kind: SessionCommandKind;
  visitorId: string;
  targetStaffId?: string;
  reason?: string;
  operationId?: string;
  newOperation?: boolean;
}

export interface SessionCommandResult {
  operationId: string;
  response?: unknown;
  receipt?: SessionCommandReceipt;
}

async function currentScope(client: TgoClient, visitorId: string): Promise<SessionCommandScope> {
  const staff = await client.get<StaffIdentity>('/v1/staff/me');
  return makeSessionCommandScope(client.serverUrl, staff.project_id, staff.id, visitorId);
}

function verifyReceipt(receipt: SessionCommandReceipt, record: SessionCommandRecord): void {
  if (receipt.operation_id !== record.operationId || receipt.visitor_id !== record.visitorId
    || receipt.kind !== record.kind || receipt.to_staff_id !== record.targetStaffId) {
    throw new Error('服务端回执与本地原操作不一致；已停止自动处理，请人工核对');
  }
}

async function queryReceipt(client: TgoClient, record: SessionCommandRecord): Promise<SessionCommandReceipt | null> {
  try {
    const receipt = await client.get<SessionCommandReceipt>(`/v1/sessions/operations/${record.operationId}`);
    verifyReceipt(receipt, record);
    return receipt;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

async function sendCommand(client: TgoClient, options: SubmitSessionCommandOptions, operationId: string): Promise<unknown> {
  if (options.kind === 'close') {
    return client.post(`/v1/sessions/visitor/${options.visitorId}/close`, {}, {
      'X-Session-Operation-ID': operationId,
    });
  }
  return client.post(`/v1/sessions/visitor/${options.visitorId}/transfer`, {
    target_staff_id: options.targetStaffId,
    ...(options.reason ? { reason: options.reason } : {}),
  }, { 'X-Session-Operation-ID': operationId });
}

/** CLI recovery contract: reserve and show the ID before POST; never silently resend a pending command. */
export async function submitSessionCommand(
  client: TgoClient,
  journal: SessionCommandJournal,
  options: SubmitSessionCommandOptions,
  onReserved: (operationId: string) => void,
): Promise<SessionCommandResult> {
  const scope = await currentScope(client, options.visitorId);
  const { record, created } = await journal.reserve(
    scope, options.kind, options.kind === 'transfer' ? options.targetStaffId ?? null : null,
    { operationId: options.operationId, newOperation: options.newOperation },
  );
  onReserved(record.operationId);

  if (!created) {
    const receipt = await queryReceipt(client, record);
    if (receipt) {
      await journal.mark(scope, record.operationId, 'committed');
      return { operationId: record.operationId, receipt };
    }
    if (record.status === 'committed') {
      throw new Error(`原操作 ${record.operationId} 本地已确认，但服务端回执不可见；请人工核对`);
    }
    if (record.status === 'rejected') {
      throw new Error(`原操作 ${record.operationId} 已拒绝；如需重新操作，请使用 --new-operation`);
    }
    if (options.operationId !== record.operationId) {
      throw new Error(`原操作 ${record.operationId} 暂无回执，未自动重发。核对后以 --operation-id ${record.operationId} 明确重试`);
    }
  }

  try {
    const response = await sendCommand(client, options, record.operationId);
    await journal.mark(scope, record.operationId, 'committed');
    return { operationId: record.operationId, response };
  } catch (error) {
    // A response can be lost after the transaction commits; query the original ID first.
    let receipt: SessionCommandReceipt | null;
    try {
      receipt = await queryReceipt(client, record);
    } catch (lookupError) {
      throw new Error(`原操作 ${record.operationId} 提交失败，回执查询也失败；本地记录保留待核对。提交错误：${String(error)}；查询错误：${String(lookupError)}`);
    }
    if (receipt) {
      await journal.mark(scope, record.operationId, 'committed');
      return { operationId: record.operationId, receipt };
    }
    throw new Error(`原操作 ${record.operationId} 暂无回执，本地记录保留；未自动重发。提交错误：${String(error)}`);
  }
}

/** Direct receipt command also reconciles a matching local pending journal entry. */
export async function syncSessionCommandReceipt(
  client: TgoClient,
  journal: SessionCommandJournal,
  operationId: string,
): Promise<SessionCommandReceipt> {
  const receipt = await client.get<SessionCommandReceipt>(`/v1/sessions/operations/${operationId}`);
  if (receipt.operation_id !== operationId) {
    throw new Error('服务端回执的操作编号与查询编号不一致');
  }
  const scope = await currentScope(client, receipt.visitor_id);
  const record = await journal.read(scope);
  if (record && record.operationId === operationId) {
    verifyReceipt(receipt, record);
    await journal.mark(scope, operationId, 'committed');
  }
  return receipt;
}
