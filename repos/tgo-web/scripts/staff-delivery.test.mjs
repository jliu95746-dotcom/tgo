import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/stores/staffDeliveryStore.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

const request = (overrides = {}) => ({
  channel_id: 'visitor-vtr', channel_type: 251, client_msg_no: 'local-1',
  payload: { type: 1, content: '你好' }, ...overrides,
});
const receipt = (status = 'sent', history = 'sent') => ({
  client_msg_no: 'local-1', delivery_status: status, history_status: history,
});
function fixture(api) {
  const scope = { exports: {}, require: name => {
    if (name === 'zustand') return require('zustand');
    if (name.endsWith('/chatMessagesApi')) return { chatMessagesApiService: api };
    throw new Error(`Unexpected dependency: ${name}`);
  } };
  vm.runInNewContext(compiled, scope);
  return scope.exports.useStaffDeliveryStore;
}

test('channel accepted with history pending is still a sent receipt', async () => {
  let calls = 0;
  const store = fixture({ deliverStaffMessage: async () => { calls++; return receipt('sent', 'pending'); } });
  const result = await store.getState().submit('staff-a', request());
  assert.equal(result.delivery_status, 'sent');
  assert.equal(result.history_status, 'pending');
  assert.equal(calls, 1);
});

test('lost HTTP response reconciles with GET and never repeats POST', async () => {
  let posts = 0, gets = 0;
  const store = fixture({
    deliverStaffMessage: async () => { posts++; throw new Error('timeout'); },
    getStaffDelivery: async () => { gets++; return receipt('sent', 'pending'); },
  });
  assert.equal((await store.getState().submit('staff-a', request())).delivery_status, 'sent');
  await store.getState().submit('staff-a', request());
  assert.equal(posts, 1);
  assert.equal(gets, 1);
});

test('unknown delivery only checks status on subsequent user attempts', async () => {
  let posts = 0, gets = 0;
  const store = fixture({
    deliverStaffMessage: async () => { posts++; throw new Error('network'); },
    getStaffDelivery: async () => { gets++; throw new Error('offline'); },
  });
  assert.equal((await store.getState().submit('staff-a', request())).delivery_status, 'unknown');
  assert.equal((await store.getState().submit('staff-a', request())).delivery_status, 'unknown');
  assert.equal(posts, 1);
  assert.equal(gets, 2);
});

test('explicit retry reuses the rejected request and enables retry_failed', async () => {
  const requests = [];
  const store = fixture({ deliverStaffMessage: async data => {
    requests.push(data); return receipt(requests.length === 1 ? 'failed' : 'sent');
  } });
  await store.getState().submit('staff-a', request());
  await store.getState().submit('staff-a', request());
  assert.equal(requests.length, 2);
  assert.equal(requests[1].client_msg_no, 'local-1');
  assert.equal(requests[1].retry_failed, true);
});

test('concurrent clicks share one delivery request', async () => {
  let release, calls = 0;
  const gate = new Promise(resolve => { release = resolve; });
  const store = fixture({ deliverStaffMessage: async () => { calls++; await gate; return receipt(); } });
  const first = store.getState().submit('staff-a', request());
  const second = store.getState().submit('staff-a', request());
  release();
  await Promise.all([first, second]);
  assert.equal(calls, 1);
});

test('reusing a message identity with different content is rejected locally', async () => {
  let calls = 0;
  const store = fixture({ deliverStaffMessage: async () => { calls++; return receipt(); } });
  await store.getState().submit('staff-a', request());
  await assert.rejects(store.getState().submit('staff-a', request({ payload: { type: 1, content: '改过的' } })));
  assert.equal(calls, 1);
});

test('different staff identities never reuse each other\'s receipt', async () => {
  let calls = 0;
  const store = fixture({ deliverStaffMessage: async () => { calls++; return receipt(); } });
  await store.getState().submit('staff-a', request());
  await store.getState().submit('staff-b', request());
  assert.equal(calls, 2);
});

test('refreshing the page restores unknown requests without delivering them again', async () => {
  let posts = 0;
  const store = fixture({
    getPendingStaffDeliveries: async () => [{ request: request(), receipt: receipt('unknown'), created_at: '2026-09-07T00:00:00Z' }],
    getStaffDelivery: async () => receipt('unknown'),
    deliverStaffMessage: async () => { posts++; return receipt(); },
  });
  const rows = await store.getState().restore('staff-a', 'visitor-vtr');
  assert.equal(rows.length, 1);
  assert.equal((await store.getState().submit('staff-a', request())).delivery_status, 'unknown');
  assert.equal(posts, 0);
});

test('retry after restore uses the original training skill and context', async () => {
  let retried;
  const original = request({ training: { skill_name: 'style-a', customer_message: '原问题', ai_draft: '原草稿', recent_messages: [] } });
  const store = fixture({
    getPendingStaffDeliveries: async () => [{ request: original, receipt: receipt('failed'), created_at: '2026-09-07T00:00:00Z' }],
    deliverStaffMessage: async data => { retried = data; return receipt('sent'); },
  });
  const restored = await store.getState().restore('staff-a', 'visitor-vtr');
  await store.getState().submit('staff-a', restored[0].request);
  assert.equal(retried.training.skill_name, 'style-a');
  assert.equal(retried.training.ai_draft, '原草稿');
  await assert.rejects(store.getState().submit('staff-a', { ...original, training: { ...original.training, skill_name: 'style-b' } }));
});
