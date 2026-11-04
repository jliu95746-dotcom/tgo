import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const project = randomUUID(), actor = randomUUID(), agent = randomUUID();
const report = randomUUID(), review = randomUUID();
const receipt = command => ({ scope: 'employee_device_script_selection', can_enable: false,
  id: randomUUID(), project_id: project, agent_id: agent, request_id: command.request_id,
  configured_by: actor, revision: command.expected_revision + 1, enabled: command.enabled,
  report_id: command.report_id ?? report, review_id: command.review_id ?? review,
  binding_id: randomUUID(), binding_revision: 1, saved_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 3600000).toISOString(), selection_digest: 'a'.repeat(64) });
const state = (selection = null) => ({ scope: 'employee_device_script_selection_state', can_enable: false,
  project_id: project, actor_id: actor, agent_id: agent, selection, status: selection?.enabled ? 'selected' : 'unselected' });

function harness(overrides = {}, storage = new Map(), writable = true) {
  const calls = [], listeners = [], modules = {};
  let user = { id: actor, project_id: project, role: 'admin' }, current = null;
  class APIError extends Error { constructor(status) { super('fixture'); this.status = status; } }
  const api = { read: async () => state(current), save: async (_agent, command) => (current = receipt(command)),
    original: async () => { throw new APIError(404); }, ...overrides };
  const wrapped = Object.fromEntries(Object.entries(api).map(([name, fn]) => [name, (...args) => {
    calls.push({ name, args }); return fn(...args);
  }]));
  function load(path) {
    if (modules[path]) return modules[path];
    const exports = {};
    const source = ts.transpileModule(readFileSync(new URL('../src/' + path, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    vm.runInNewContext(source, { exports, Date, AbortController, crypto: { randomUUID },
      sessionStorage: { getItem: key => storage.get(key) ?? null,
        setItem: (key, value) => { if (!writable) throw Error('blocked'); storage.set(key, value); } },
      require: name => {
        if (name === './authStore') return { useAuthStore: { getState: () => ({ user }), subscribe: fn => listeners.push(fn) } };
        if (name === './agentActivationStore') return { useAgentActivationStore: { getState: () => ({ invalidate() {} }) } };
        if (name === '@/services/agentDeviceScriptSelectionApi') return { AgentDeviceScriptSelectionApi: wrapped };
        if (name === '@/services/api') return { APIError };
        if (name === '@/utils/agentDeviceScriptSelectionValidation') return load('utils/agentDeviceScriptSelectionValidation.ts');
        if (name === './agentDeviceScriptValidation') return { scriptUuid: value => typeof value === 'string' && /^[0-9a-f-]{36}$/i.test(value) };
        return require(name);
      } });
    return modules[path] = exports;
  }
  return { store: load('stores/agentDeviceScriptSelectionStore.ts').useAgentDeviceScriptSelectionStore,
    calls, storage, APIError, changeUser() { const previous = { user }; user = { ...user, id: randomUUID() }; listeners.forEach(fn => fn({ user }, previous)); } };
}

test('a lost save retains the original metadata and reload only queries it', async () => {
  const storage = new Map();
  const first = harness({ save: async () => { throw Error('504'); } }, storage);
  await first.store.getState().select(agent);
  await first.store.getState().choose(report, review);
  const pending = first.store.getState().pending;
  assert.ok(pending);
  await first.store.getState().choose(report, review);
  assert.equal(first.calls.filter(call => call.name === 'save').length, 1);
  const settled = receipt(pending);
  const second = harness({ original: async () => settled, read: async () => state(settled) }, storage);
  await second.store.getState().select(agent);
  assert.ok(second.calls.every(call => call.name !== 'save'));
  assert.equal(second.store.getState().pending, null);
  assert.equal(second.store.getState().current.selection.report_id, report);
});

test('404 recovery keeps the command and explicit retry uses the exact same UUID', async () => {
  const h = harness({ save: async () => { throw Error('lost'); } });
  await h.store.getState().select(agent);
  await h.store.getState().choose(report, review);
  const original = h.store.getState().pending.request_id;
  await h.store.getState().recover();
  assert.equal(h.store.getState().pending.request_id, original);
  await h.store.getState().retry();
  const saves = h.calls.filter(call => call.name === 'save');
  assert.equal(saves.length, 2);
  assert.ok(saves.every(call => call.args[1].request_id === original));
});

test('storage failure prevents POST and a forged owner cannot settle the request', async () => {
  const h = harness({}, new Map(), false);
  await h.store.getState().select(agent);
  await h.store.getState().choose(report, review);
  assert.equal(h.calls.filter(call => call.name === 'save').length, 0);
  const bad = harness({ save: async (_agent, command) => ({ ...receipt(command), configured_by: randomUUID() }) });
  await bad.store.getState().select(agent);
  await bad.store.getState().choose(report, review);
  assert.ok(bad.store.getState().pending);
  assert.equal(bad.store.getState().error, 'invalidResponse');
});

test('account switches discard a delayed response and never carry selection state over', async () => {
  let resolve;
  const delayed = new Promise(done => { resolve = done; });
  const h = harness({ save: () => delayed });
  await h.store.getState().select(agent);
  const work = h.store.getState().choose(report, review);
  await new Promise(done => setImmediate(done));
  const pointer = h.store.getState().pending;
  h.changeUser();
  resolve(receipt(pointer));
  await work;
  assert.equal(h.store.getState().current, null);
  assert.equal(h.store.getState().pending, null);
});

test('a rejected 409 configuration transaction refreshes the revision before another save', async () => {
  let fail;
  const h = harness({ save: async () => { throw fail; } });
  fail = new h.APIError(409);
  await h.store.getState().select(agent);
  await h.store.getState().choose(report, review);
  assert.equal(h.store.getState().pending, null);
  assert.equal(h.store.getState().error, 'conflict');
  assert.ok(h.store.getState().current);
  assert.equal(h.calls.filter(call => call.name === 'save').length, 1);
});
