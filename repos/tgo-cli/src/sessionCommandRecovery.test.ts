import { afterEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import { join, resolve, sep } from 'node:path';
import { ApiError } from './client.js';
import type { TgoClient } from './client.js';
import { SessionCommandJournal, makeSessionCommandScope } from './sessionCommandJournal.js';
import { submitSessionCommand, syncSessionCommandReceipt } from './sessionCommandRecovery.js';

const roots: string[] = [];
function journal(): SessionCommandJournal {
  const root = mkdtempSync(join(process.cwd(), '.tmp-session-recovery-'));
  roots.push(root);
  return new SessionCommandJournal(root);
}
afterEach(() => {
  for (const root of roots.splice(0)) {
    if (!resolve(root).startsWith(resolve(process.cwd()) + sep)) throw new Error('Invalid test cleanup path');
    rmSync(root, { recursive: true, force: true });
  }
});

const visitorId = '3e7d710c-b8b0-4d63-9758-7932775fbd46';
const staffId = '14db1238-b44b-4f1a-b48b-51e2b3715267';
const projectId = '2458e650-22be-4a95-b4bc-aa43fe72ed54';
const scope = makeSessionCommandScope('https://api.example.test', projectId, staffId, visitorId);
function mockClient() {
  return {
    serverUrl: 'https://api.example.test',
    get: vi.fn().mockResolvedValue({ id: staffId, project_id: projectId }),
    post: vi.fn().mockResolvedValue({ success: true, visitor_id: visitorId }),
  } as unknown as TgoClient & { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> };
}
function receipt(operationId: string, kind: 'close' | 'transfer' = 'close', toStaffId: string | null = null) {
  return { operation_id: operationId, visitor_id: visitorId, kind, to_staff_id: toStaffId,
    notification_status: 'sent', session_id: '90fe914a-37cf-4fcb-b1d9-577ab06f9982', attempts: 1 };
}

describe('session command recovery', () => {
  it('persists the identifier before the first POST and commits it on success', async () => {
    const store = journal();
    const client = mockClient();
    const seen: string[] = [];
    const result = await submitSessionCommand(client, store, { kind: 'close', visitorId }, id => {
      seen.push(id);
      expect(client.post).not.toHaveBeenCalled();
    });
    expect(seen).toEqual([result.operationId]);
    expect(client.post).toHaveBeenCalledTimes(1);
    expect((await store.read(scope))?.status).toBe('committed');
  });

  it('queries a pending operation and does not silently resend after a lost response', async () => {
    const store = journal();
    const prior = await store.reserve(scope, 'close', null);
    const client = mockClient();
    client.get.mockImplementation(async (path: string) => {
      if (path === '/v1/staff/me') return { id: staffId, project_id: projectId };
      throw new ApiError(404, { message: 'Not found' });
    });
    await expect(submitSessionCommand(client, store, { kind: 'close', visitorId }, () => {}))
      .rejects.toThrow(prior.record.operationId);
    expect(client.post).not.toHaveBeenCalled();
    expect((await store.read(scope))?.status).toBe('pending');
  });

  it('resolves an existing receipt without a second POST', async () => {
    const store = journal();
    const prior = await store.reserve(scope, 'close', null);
    const client = mockClient();
    client.get.mockImplementation(async (path: string) => path === '/v1/staff/me'
      ? { id: staffId, project_id: projectId } : receipt(prior.record.operationId));
    const result = await submitSessionCommand(client, store, { kind: 'close', visitorId }, () => {});
    expect(result.receipt).toEqual(receipt(prior.record.operationId));
    expect(client.post).not.toHaveBeenCalled();
    expect((await store.read(scope))?.status).toBe('committed');
  });

  it('allows explicit same-id retry only after receipt lookup returns 404', async () => {
    const store = journal();
    const prior = await store.reserve(scope, 'close', null);
    const client = mockClient();
    client.get.mockImplementation(async (path: string) => {
      if (path === '/v1/staff/me') return { id: staffId, project_id: projectId };
      throw new ApiError(404, { message: 'Not found' });
    });
    await submitSessionCommand(client, store, {
      kind: 'close', visitorId, operationId: prior.record.operationId,
    }, () => {});
    expect(client.get).toHaveBeenCalledWith(`/v1/sessions/operations/${prior.record.operationId}`);
    expect(client.post).toHaveBeenCalledTimes(1);
    expect((await store.read(scope))?.status).toBe('committed');
  });

  it('returns the previous receipt on repeated invocation and sends again only with --new-operation', async () => {
    const store = journal();
    const first = await store.reserve(scope, 'close', null);
    await store.mark(scope, first.record.operationId, 'committed');
    const client = mockClient();
    client.get.mockImplementation(async (path: string) => path === '/v1/staff/me'
      ? { id: staffId, project_id: projectId } : receipt(first.record.operationId));
    const repeated = await submitSessionCommand(client, store, { kind: 'close', visitorId }, () => {});
    expect(repeated.receipt?.operation_id).toBe(first.record.operationId);
    expect(client.post).not.toHaveBeenCalled();
    const next = await submitSessionCommand(client, store, {
      kind: 'close', visitorId, newOperation: true,
    }, () => {});
    expect(next.operationId).not.toBe(first.record.operationId);
    expect(client.post).toHaveBeenCalledTimes(1);
    expect((await store.read(scope))?.status).toBe('committed');
  });

  it('keeps a pending record after uncertain POST failure and recovers via receipt', async () => {
    const store = journal();
    const client = mockClient();
    client.post.mockRejectedValue(new Error('socket hang up'));
    client.get.mockImplementation(async (path: string) => {
      if (path === '/v1/staff/me') return { id: staffId, project_id: projectId };
      throw new ApiError(404, { message: 'Not found' });
    });
    await expect(submitSessionCommand(client, store, { kind: 'close', visitorId }, () => {}))
      .rejects.toThrow('socket hang up');
    const stored = await store.read(scope);
    expect(stored?.status).toBe('pending');
    client.get.mockImplementation(async (path: string) => path === '/v1/staff/me'
      ? { id: staffId, project_id: projectId } : receipt(stored!.operationId));
    await submitSessionCommand(client, store, { kind: 'close', visitorId }, () => {});
    expect(client.post).toHaveBeenCalledTimes(1);
    expect((await store.read(scope))?.status).toBe('committed');
  });

  it('does not accept a receipt for a different visitor or target', async () => {
    const store = journal();
    const prior = await store.reserve(scope, 'transfer', staffId);
    const client = mockClient();
    client.get.mockImplementation(async (path: string) => path === '/v1/staff/me'
      ? { id: staffId, project_id: projectId }
      : { ...receipt(prior.record.operationId, 'transfer', staffId), visitor_id: projectId });
    await expect(submitSessionCommand(client, store, {
      kind: 'transfer', visitorId, targetStaffId: staffId,
    }, () => {})).rejects.toThrow();
    expect(client.post).not.toHaveBeenCalled();
    expect((await store.read(scope))?.status).toBe('pending');
  });

  it('syncs a direct receipt query only with the matching local account and command', async () => {
    const store = journal();
    const prior = await store.reserve(scope, 'close', null);
    const client = mockClient();
    client.get.mockImplementation(async (path: string) => path === '/v1/staff/me'
      ? { id: staffId, project_id: projectId } : receipt(prior.record.operationId));
    await syncSessionCommandReceipt(client, store, prior.record.operationId);
    expect((await store.read(scope))?.status).toBe('committed');
  });
});
