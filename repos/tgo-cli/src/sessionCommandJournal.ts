import { createHash, randomUUID } from 'node:crypto';
import {
  closeSync, existsSync, mkdirSync, openSync, readFileSync, renameSync,
  rmdirSync, statSync, unlinkSync, writeFileSync, fsyncSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export type SessionCommandKind = 'close' | 'transfer';
export type SessionCommandStatus = 'pending' | 'committed' | 'rejected';

export interface SessionCommandScope {
  serverFingerprint: string;
  projectId: string;
  actorId: string;
  visitorId: string;
}

export interface SessionCommandRecord extends SessionCommandScope {
  operationId: string;
  kind: SessionCommandKind;
  targetStaffId: string | null;
  status: SessionCommandStatus;
  createdAt: string;
  updatedAt: string;
}

export interface ReserveOptions {
  operationId?: string;
  newOperation?: boolean;
}

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const LOCK_STALE_MS = 30_000;

function requireId(value: string, label: string): string {
  if (typeof value !== 'string' || !value.trim() || /[\r\n]/.test(value)) {
    throw new Error(`${label}无效`);
  }
  return value;
}

export function makeSessionCommandScope(
  server: string,
  projectId: string,
  actorId: string,
  visitorId: string,
): SessionCommandScope {
  const url = new URL(server);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error('服务地址无效，不能包含账号、参数或片段');
  }
  const normalized = `${url.origin}${url.pathname.replace(/\/+$/, '')}`;
  return {
    serverFingerprint: createHash('sha256').update(normalized).digest('hex'),
    projectId: requireId(projectId, '项目编号'),
    actorId: requireId(actorId, '员工编号'),
    visitorId: requireId(visitorId, '访客编号'),
  };
}

function validRecord(value: unknown, scope: SessionCommandScope): value is SessionCommandRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  const fields = [
    'serverFingerprint', 'projectId', 'actorId', 'visitorId', 'operationId',
    'kind', 'targetStaffId', 'status', 'createdAt', 'updatedAt',
  ];
  if (Object.keys(item).length !== fields.length || !fields.every(field => Object.hasOwn(item, field))) {
    return false;
  }
  return item.serverFingerprint === scope.serverFingerprint
    && item.projectId === scope.projectId
    && item.actorId === scope.actorId
    && item.visitorId === scope.visitorId
    && typeof item.operationId === 'string' && UUID_V4.test(item.operationId)
    && (item.kind === 'close' || item.kind === 'transfer')
    && ((item.kind === 'close' && item.targetStaffId === null)
      || (item.kind === 'transfer' && typeof item.targetStaffId === 'string' && !!item.targetStaffId))
    && (item.status === 'pending' || item.status === 'committed' || item.status === 'rejected')
    && typeof item.createdAt === 'string' && typeof item.updatedAt === 'string';
}

function processAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== 'ESRCH';
  }
}

export class SessionCommandJournal {
  constructor(private readonly root = join(homedir(), '.tgo', 'session-commands')) {}

  private path(scope: SessionCommandScope): string {
    const key = [scope.serverFingerprint, scope.projectId, scope.actorId, scope.visitorId].join('\0');
    return join(this.root, `${createHash('sha256').update(key).digest('hex')}.json`);
  }

  async read(scope: SessionCommandScope): Promise<SessionCommandRecord | null> {
    const path = this.path(scope);
    if (!existsSync(path)) return null;
    let value: unknown;
    try {
      value = JSON.parse(readFileSync(path, 'utf8'));
    } catch {
      throw new Error('本地会话操作记录损坏；请保留文件并人工核对，不能自动覆盖');
    }
    if (!validRecord(value, scope)) {
      throw new Error('本地会话操作记录与当前账号或格式不匹配，不能自动覆盖');
    }
    return value;
  }

  private recoverStaleLock(lock: string): boolean {
    try {
      if (Date.now() - statSync(lock).mtimeMs < LOCK_STALE_MS) return false;
      const ownerPath = join(lock, 'owner.json');
      const owner = existsSync(ownerPath)
        ? JSON.parse(readFileSync(ownerPath, 'utf8')) as { pid?: unknown; nonce?: unknown }
        : null;
      if (owner && (typeof owner.pid !== 'number' || typeof owner.nonce !== 'string')) return false;
      if (owner && processAlive(owner.pid as number)) return false;
      const moved = `${lock}.abandoned-${randomUUID()}`;
      renameSync(lock, moved);
      const movedOwner = join(moved, 'owner.json');
      const actual = existsSync(movedOwner)
        ? JSON.parse(readFileSync(movedOwner, 'utf8')) as { pid?: unknown; nonce?: unknown }
        : null;
      if (JSON.stringify(owner) !== JSON.stringify(actual)) {
        throw new Error('锁归属在回收期间发生变化，请人工核对');
      }
      if (existsSync(movedOwner)) unlinkSync(movedOwner);
      rmdirSync(moved);
      return true;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return true;
      throw error;
    }
  }

  private async withLock<T>(scope: SessionCommandScope, action: () => Promise<T>): Promise<T> {
    mkdirSync(this.root, { recursive: true, mode: 0o700 });
    const lock = this.path(scope).replace(/\.json$/, '.lock');
    const nonce = randomUUID();
    let acquired = false;
    for (let attempt = 0; attempt < 50; attempt++) {
      try {
        mkdirSync(lock);
        acquired = true;
        writeFileSync(join(lock, 'owner.json'), JSON.stringify({ pid: process.pid, nonce, createdAt: Date.now() }));
        break;
      } catch (error) {
        if (acquired) {
          if (existsSync(join(lock, 'owner.json'))) unlinkSync(join(lock, 'owner.json'));
          rmdirSync(lock);
          throw error;
        }
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
        if (this.recoverStaleLock(lock)) continue;
        await new Promise(resolve => setTimeout(resolve, 50));
      }
    }
    if (!acquired) throw new Error('会话操作记录正被其他进程使用，请稍后重试');
    try {
      return await action();
    } finally {
      const ownerPath = join(lock, 'owner.json');
      const owner = JSON.parse(readFileSync(ownerPath, 'utf8')) as { nonce?: string };
      if (owner.nonce !== nonce) throw new Error('会话操作锁归属变化，请人工核对');
      unlinkSync(ownerPath);
      rmdirSync(lock);
    }
  }

  private write(scope: SessionCommandScope, record: SessionCommandRecord): void {
    const path = this.path(scope);
    const temporary = `${path}.${randomUUID()}.tmp`;
    const fd = openSync(temporary, 'wx', 0o600);
    try {
      writeFileSync(fd, `${JSON.stringify(record)}\n`);
      fsyncSync(fd);
    } finally {
      closeSync(fd);
    }
    try {
      renameSync(temporary, path);
    } finally {
      if (existsSync(temporary)) unlinkSync(temporary);
    }
  }

  async reserve(
    scope: SessionCommandScope,
    kind: SessionCommandKind,
    targetStaffId: string | null,
    options: ReserveOptions = {},
  ): Promise<{ record: SessionCommandRecord; created: boolean }> {
    if (options.operationId && !UUID_V4.test(options.operationId)) throw new Error('操作编号必须是 UUIDv4');
    if (kind === 'transfer' && !targetStaffId) throw new Error('转接目标不能为空');
    if (kind === 'close' && targetStaffId !== null) throw new Error('关闭操作不能指定转接目标');
    return this.withLock(scope, async () => {
      const existing = await this.read(scope);
      if (existing) {
        if (options.newOperation && options.operationId === existing.operationId) {
          throw new Error('新操作不能复用上一操作编号');
        }
        if (existing.status === 'pending') {
          if (options.newOperation || existing.kind !== kind || existing.targetStaffId !== targetStaffId
            || (options.operationId && options.operationId !== existing.operationId)) {
            throw new Error(`原操作 ${existing.operationId} 尚未确认；先查询回执，不能生成新操作`);
          }
          return { record: existing, created: false };
        }
        if (!options.newOperation) {
          if (existing.kind !== kind || existing.targetStaffId !== targetStaffId
            || (options.operationId && options.operationId !== existing.operationId)) {
            throw new Error(`最近操作 ${existing.operationId} 已结束；如需新的业务操作，请使用 --new-operation`);
          }
          return { record: existing, created: false };
        }
      }
      const now = new Date().toISOString();
      const record: SessionCommandRecord = {
        ...scope, operationId: options.operationId ?? randomUUID(), kind, targetStaffId,
        status: 'pending', createdAt: now, updatedAt: now,
      };
      this.write(scope, record);
      return { record, created: true };
    });
  }

  async mark(
    scope: SessionCommandScope,
    operationId: string,
    status: 'committed' | 'rejected',
  ): Promise<SessionCommandRecord> {
    return this.withLock(scope, async () => {
      const existing = await this.read(scope);
      if (!existing || existing.operationId !== operationId) {
        throw new Error('操作编号与当前本地记录不一致，不能更新');
      }
      if (existing.status !== 'pending' && existing.status !== status) {
        throw new Error('已经确认的操作状态不能反向改写');
      }
      const record = { ...existing, status, updatedAt: new Date().toISOString() };
      this.write(scope, record);
      return record;
    });
  }
}
