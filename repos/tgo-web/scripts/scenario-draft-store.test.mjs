import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function harness() {
  let user = { id: 'staff-a', project_id: 'project-a' };
  const listeners = [];
  const calls = [];
  const pending = [];
  const auth = { getState: () => ({ user }), subscribe: callback => listeners.push(callback) };
  const create = initializer => {
    let state;
    const set = next => { state = { ...state, ...next }; };
    state = initializer(set, () => state);
    return Object.assign(() => state, { getState: () => state, setState: set });
  };
  const api = new Proxy({}, { get: (_, name) => (...args) => {
    calls.push({ name, args });
    return new Promise((resolve, reject) => pending.push({ name, resolve, reject }));
  } });
  const exports = {};
  const source = ts.transpileModule(readFileSync(new URL('../src/stores/scenarioDraftStore.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(source, { exports, structuredClone, crypto: { randomUUID: () => 'stable-request-key' }, require: name => {
    if (name === 'zustand') return { create };
    if (name === './authStore') return { useAuthStore: auth };
    if (name === '@/services/businessScenariosApi') return { BusinessScenariosApi: api };
    throw new Error(`Unexpected import: ${name}`);
  } });
  return {
    store: exports.useScenarioDraftStore,
    calls,
    resolve: data => pending.shift().resolve(data),
    reject: () => pending.shift().reject(new Error('network-error')),
    changeUser: next => { const previous = { user }; user = next; listeners.forEach(callback => callback({ user: next }, previous)); },
  };
}

const input = () => ({ module: 'agents', template_id: 'knowledge', template_version: 1, channel: 'web', step: 'resources',
  business: { name: '商品客服', profession: '商品咨询', instructions: '依据企业资料回答', company_summary: '女包' },
  bindings: { model: null, knowledge_ids: ['kb-a'], tool_ids: [], workflow_ids: [], connection_ids: [], device_ids: [] }, agent_settings: {},
});
const draft = (changes = {}) => ({ id: 'draft-a', project_id: 'project-a', created_by: 'staff-a', module: 'agents', template_id: 'knowledge', template_version: 1, revision: 1, step: 'resources', ...changes });
const tick = () => new Promise(resolve => setImmediate(resolve));

test('switching identity ignores an in-flight save and sends no further update', async () => {
  const fixture = harness();
  const saved = fixture.store.getState().save(input());
  fixture.changeUser({ id: 'staff-b', project_id: 'project-b' });
  fixture.resolve(draft());
  assert.equal(await saved, false);
  assert.equal(fixture.store.getState().current, null);
  assert.equal(fixture.calls.length, 1);
});

test('retrying an unknown creation outcome reuses the exact key and then saves current edits', async () => {
  const fixture = harness();
  const first = fixture.store.getState().save(input());
  fixture.reject();
  assert.equal(await first, false);
  const changed = input();
  changed.business.name = '修改后的名字';
  const retry = fixture.store.getState().save(changed);
  assert.deepEqual(JSON.parse(JSON.stringify(fixture.calls[0].args)), JSON.parse(JSON.stringify(fixture.calls[1].args)));
  fixture.resolve(draft());
  await tick();
  assert.equal(fixture.calls[2].name, 'updateDraft');
  assert.equal(fixture.calls[2].args[1].business.name, '修改后的名字');
  assert.equal(fixture.calls[2].args[1].expected_revision, 1);
  fixture.resolve(draft({ revision: 2 }));
  assert.equal(await retry, true);
  assert.equal(fixture.store.getState().current.revision, 2);
  assert.equal(fixture.store.getState().pendingCreation, null);
});

test('foreign draft responses cannot be resumed or updated', async () => {
  const fixture = harness();
  const result = fixture.store.getState().resume('draft-a');
  fixture.resolve(draft({ project_id: 'project-b' }));
  assert.equal(await result, null);
  assert.equal(fixture.store.getState().failed, true);
  assert.equal(fixture.store.getState().current, null);
});

test('a conflicting save preserves the accepted revision and requires an explicit reload', async () => {
  const fixture = harness();
  fixture.store.setState({ current: draft({ revision: 2 }) });
  const result = fixture.store.getState().save(input());
  fixture.reject();
  assert.equal(await result, false);
  assert.equal(fixture.calls.length, 1);
  assert.equal(fixture.calls[0].args[1].expected_revision, 2);
  assert.equal(fixture.store.getState().current.revision, 2);
  assert.equal(fixture.store.getState().failed, true);
});

test('resuming restores the saved step and starting a new setup clears the previous step', async () => {
  const fixture = harness();
  const resumed = fixture.store.getState().resume('draft-a');
  fixture.resolve(draft({ step: 'testing' }));
  assert.ok(await resumed);
  assert.equal(fixture.store.getState().step, 'testing');
  fixture.store.getState().startNew();
  assert.equal(fixture.store.getState().step, 'scenario');
  assert.equal(fixture.store.getState().current, null);
});

test('the chosen step is persisted with the current form on save', async () => {
  const fixture = harness();
  fixture.store.setState({ current: draft(), step: 'activation' });
  const payload = input();
  payload.step = fixture.store.getState().step;
  const saved = fixture.store.getState().save(payload);
  assert.equal(fixture.calls[0].args[1].step, 'activation');
  fixture.resolve(draft({ revision: 2, step: 'activation' }));
  assert.equal(await saved, true);
});
