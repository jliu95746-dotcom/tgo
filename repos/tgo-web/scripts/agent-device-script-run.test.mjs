import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const project = randomUUID(), actor = randomUUID(), agent = randomUUID();
const report = randomUUID(), review = randomUUID();
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
const receipt = (pointer, status = 'succeeded') => {
  const now = new Date().toISOString();
  const dispatched = ['unknown', 'succeeded'].includes(status);
  return { ...pointer, scope: 'employee_device_script_run', can_enable: false,
    id: randomUUID(), project_id: project, actor_id: actor, agent_id: agent,
    execution_id: dispatched ? randomUUID() : null, status,
    error_code: status === 'cancelled' ? 'cancelled' : null,
    created_at: now, deadline_at: new Date(Date.now() + 60000).toISOString(),
    dispatch_started_at: dispatched ? now : null,
    completed_at: ['succeeded', 'failed', 'cancelled'].includes(status) ? now : null,
    stop_requested_at: null, result_digest: status === 'succeeded' ? 'a'.repeat(64) : null,
    result_fields: status === 'succeeded' ? { label: 'PRIVATE-RUN-RESULT' } : null };
};

function harness(overrides = {}, storage = new Map(), writable = true) {
  const calls = [], listeners = [];
  let user = { id: actor, project_id: project, role: 'admin' }, original = null;
  class APIError extends Error { constructor(status) { super('fixture'); this.status = status; } }
  const api = { start: async (_agent, body) => receipt(body),
    read: async () => { throw new APIError(404); },
    cancel: async (_agent, pointer) => receipt(pointer, 'cancelled'), ...overrides };
  const wrapped = Object.fromEntries(Object.entries(api).map(([name, fn]) => [name, (...args) => {
    calls.push({ name, args });
    if (name === 'start') {
      const { parameters, ...pointer } = args[1]; void parameters; original = pointer;
      return fn(args[0], original, args[2]);
    }
    return fn(...args);
  }]));
  const modules = {};
  function load(path) {
    if (modules[path]) return modules[path];
    const exports = {};
    const source = ts.transpileModule(readFileSync(new URL('../src/' + path, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    vm.runInNewContext(source, { exports, Date, TextEncoder, AbortController,
      crypto: { randomUUID }, sessionStorage: {
        getItem: key => storage.get(key) ?? null,
        setItem: (key, value) => { if (!writable) throw Error('blocked'); storage.set(key, value); },
      }, require: name => {
        if (name === './authStore') return { useAuthStore: { getState: () => ({ user }), subscribe: fn => listeners.push(fn) } };
        if (name === '@/services/agentDeviceScriptRunsApi') return { AgentDeviceScriptRunsApi: wrapped };
        if (name === '@/services/api') return { APIError };
        if (name === '@/utils/agentDeviceScriptRunValidation') return load('utils/agentDeviceScriptRunValidation.ts');
        if (name === './agentDeviceScriptValidation') return { scriptUuid: value => typeof value === 'string' && /^[0-9a-f-]{36}$/i.test(value) };
        return require(name);
      } });
    return modules[path] = exports;
  }
  return { store: load('stores/agentDeviceScriptRunStore.ts').useAgentDeviceScriptRunStore, calls, storage,
    original: () => original, changeUser: () => {
      const previous = { user }; user = { ...user, id: randomUUID() };
      listeners.forEach(fn => fn({ user }, previous));
    } };
}

test('a lost response preserves only original metadata and reload performs GET only', async () => {
  const storage = new Map();
  const h = harness({ start: async () => { throw Error('504'); } }, storage);
  await h.store.getState().select(agent);
  await h.store.getState().start(report, review, { label: 'PRIVATE-RUN-INPUT' });
  const pointer = h.store.getState().pointer;
  assert.ok(pointer);
  assert.equal(h.calls.filter(call => call.name === 'start').length, 1);
  await h.store.getState().start(report, review, { label: 'second' });
  assert.equal(h.calls.filter(call => call.name === 'start').length, 1);
  const resumed = harness({ read: async () => receipt(pointer) }, storage);
  await resumed.store.getState().select(agent);
  assert.ok(resumed.calls.every(call => call.name === 'read'));
  assert.equal(resumed.store.getState().receipt.status, 'succeeded');
  assert.equal(resumed.store.getState().receipt.result_fields, undefined);
  assert.ok(!JSON.stringify([...storage.values()]).includes('PRIVATE-RUN'));
  const value = await resumed.store.getState().recover();
  assert.equal(value.result_fields.label, 'PRIVATE-RUN-RESULT');
});

test('failed metadata persistence prevents the business POST', async () => {
  const h = harness({}, new Map(), false);
  await h.store.getState().select(agent);
  await h.store.getState().start(report, review, { label: 'private' });
  assert.equal(h.calls.length, 0);
  assert.equal(h.store.getState().error, 'storage');
});

test('a confirmed cancel wins against a delayed original response', async () => {
  const pending = deferred();
  const h = harness({ start: () => pending.promise });
  await h.store.getState().select(agent);
  const task = h.store.getState().start(report, review, { label: 'private' });
  await new Promise(resolve => setImmediate(resolve));
  const pointer = h.store.getState().pointer;
  await h.store.getState().cancel();
  pending.resolve(receipt(pointer, 'unknown'));
  await task;
  assert.equal(h.store.getState().receipt.status, 'cancelled');
  assert.equal(h.calls.filter(call => call.name === 'start').length, 1);
});

test('account changes discard the old execution and late private result', async () => {
  const pending = deferred();
  const h = harness({ start: () => pending.promise });
  await h.store.getState().select(agent);
  const task = h.store.getState().start(report, review, { label: 'private' });
  await new Promise(resolve => setImmediate(resolve));
  const pointer = h.store.getState().pointer;
  h.changeUser();
  pending.resolve(receipt(pointer));
  assert.equal(await task, null);
  assert.equal(h.store.getState().receipt, null);
  assert.equal(h.store.getState().pointer, null);
});
