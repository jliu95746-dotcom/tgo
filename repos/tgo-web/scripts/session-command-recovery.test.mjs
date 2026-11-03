import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  beginSessionCommand,
  checkPendingSessionCommand,
  clearPendingSessionCommand,
  listPendingSessionCommands,
  readPendingSessionCommand,
  retryPendingSessionCommand,
  savePendingSessionCommand,
} from '../src/utils/sessionCommandRecovery.ts';

class MemoryStorage {
  values = new Map();
  get length() { return this.values.size; }
  key(index) { return [...this.values.keys()][index] ?? null; }
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, value); }
  removeItem(key) { this.values.delete(key); }
  clear() { this.values.clear(); }
}

const scope = {
  projectId: '2458e650-22be-4a95-b4bc-aa43fe72ed54',
  actorId: '14db1238-b44b-4f1a-b48b-51e2b3715267',
};
const visitorId = '3e7d710c-b8b0-4d63-9758-7932775fbd46';
const pending = {
  ...scope,
  visitorId,
  operationId: '01e4b977-32ee-4c0d-b381-a6862a3a4d4f',
  kind: 'close',
  targetStaffId: null,
  createdAt: '2026-10-14T12:00:00.000Z',
};

test('persists an opaque command pointer and isolates another actor', () => {
  const storage = new MemoryStorage();
  assert.equal(savePendingSessionCommand(storage, pending), true);
  assert.deepEqual(readPendingSessionCommand(storage, scope, visitorId), pending);
  assert.deepEqual(listPendingSessionCommands(storage, scope), [pending]);
  assert.equal(readPendingSessionCommand(storage, { ...scope, actorId: visitorId }, visitorId), null);
  assert.equal(storage.getItem(storage.key(0))?.includes('token'), false);
});

test('does not replace a pending operation with another operation id', () => {
  const storage = new MemoryStorage();
  assert.equal(savePendingSessionCommand(storage, pending), true);
  assert.equal(savePendingSessionCommand(storage, {
    ...pending,
    operationId: 'cbb9b62c-6b77-47ea-b899-66b9333144de',
  }), false);
  clearPendingSessionCommand(storage, scope, visitorId, 'cbb9b62c-6b77-47ea-b899-66b9333144de');
  assert.deepEqual(readPendingSessionCommand(storage, scope, visitorId), pending);
  clearPendingSessionCommand(storage, scope, visitorId, pending.operationId);
  assert.equal(readPendingSessionCommand(storage, scope, visitorId), null);
});

test('refuses to submit when storage cannot persist the operation id', () => {
  const storage = new MemoryStorage();
  storage.setItem = () => { throw new Error('storage unavailable'); };
  assert.equal(savePendingSessionCommand(storage, pending), false);
});

const receipt = {
  operation_id: pending.operationId,
  session_id: '528e7ae4-4072-409d-b1e6-915d19f010c9',
  visitor_id: visitorId,
  to_staff_id: null,
  kind: 'close',
  notification_status: 'unknown',
  attempts: 1,
};

test('finds a committed operation after its POST response is lost', async () => {
  const storage = new MemoryStorage();
  const sent = [];
  const result = await beginSessionCommand(storage, scope, visitorId, 'close', null, {
    send: async command => { sent.push(command.operationId); throw new Error('connection lost'); },
    lookup: async () => receipt,
  }, () => pending.operationId);
  assert.equal(result.state, 'committed');
  assert.deepEqual(sent, [pending.operationId]);
  assert.equal(readPendingSessionCommand(storage, scope, visitorId), null);
});

test('leaves an unknown operation pending and does not silently submit twice', async () => {
  const storage = new MemoryStorage();
  let sends = 0;
  const transport = {
    send: async () => { sends++; throw new Error('connection lost'); },
    lookup: async () => null,
  };
  const result = await beginSessionCommand(
    storage, scope, visitorId, 'close', null, transport, () => pending.operationId,
  );
  assert.equal(result.state, 'pending');
  assert.equal((await checkPendingSessionCommand(storage, scope, visitorId, transport))?.state, 'pending');
  assert.equal((await beginSessionCommand(storage, scope, visitorId, 'close', null, transport))?.state, 'pending');
  assert.equal(sends, 1);
  assert.deepEqual(readPendingSessionCommand(storage, scope, visitorId), {
    ...pending, createdAt: readPendingSessionCommand(storage, scope, visitorId)?.createdAt,
  });
});

test('explicit retry reuses the original id and checks the receipt before sending', async () => {
  const storage = new MemoryStorage();
  savePendingSessionCommand(storage, pending);
  const sent = [];
  const transport = {
    send: async command => { sent.push(command.operationId); },
    lookup: async () => null,
  };
  assert.equal((await retryPendingSessionCommand(storage, scope, visitorId, transport))?.state, 'committed');
  assert.deepEqual(sent, [pending.operationId]);
  assert.equal(readPendingSessionCommand(storage, scope, visitorId), null);
});

test('rejects a receipt that belongs to a different operation target', async () => {
  const storage = new MemoryStorage();
  savePendingSessionCommand(storage, pending);
  const result = await checkPendingSessionCommand(storage, scope, visitorId, {
    send: async () => { throw new Error('must not send'); },
    lookup: async () => ({ ...receipt, kind: 'transfer', to_staff_id: scope.actorId }),
  });
  assert.equal(result?.state, 'pending');
  assert.deepEqual(readPendingSessionCommand(storage, scope, visitorId), pending);
});

test('does not retry when receipt lookup itself fails', async () => {
  const storage = new MemoryStorage();
  savePendingSessionCommand(storage, pending);
  let sent = false;
  const result = await retryPendingSessionCommand(storage, scope, visitorId, {
    send: async () => { sent = true; },
    lookup: async () => { throw new Error('lookup unavailable'); },
  });
  assert.equal(result?.state, 'pending');
  assert.equal(sent, false);
});

test('does not POST when the browser cannot save the operation id', async () => {
  const storage = new MemoryStorage();
  storage.setItem = () => { throw new Error('storage unavailable'); };
  let sent = false;
  await assert.rejects(() => beginSessionCommand(storage, scope, visitorId, 'close', null, {
    send: async () => { sent = true; },
    lookup: async () => null,
  }, () => pending.operationId));
  assert.equal(sent, false);
});

test('a definitive server rejection frees the visitor for a corrected operation', async () => {
  const storage = new MemoryStorage();
  const result = await beginSessionCommand(storage, scope, visitorId, 'close', null, {
    send: async () => { throw Object.assign(new Error('会话已关闭'), { status: 400 }); },
    lookup: async () => null,
  }, () => pending.operationId);
  assert.equal(result.state, 'rejected');
  assert.equal(readPendingSessionCommand(storage, scope, visitorId), null);
});
