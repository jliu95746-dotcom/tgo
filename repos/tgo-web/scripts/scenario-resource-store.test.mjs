import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function harness() {
  let identity = { id: 'staff-a', project_id: 'project-a' };
  const listeners = [];
  const pending = [];
  const auth = { getState: () => ({ user: identity }), subscribe: callback => listeners.push(callback) };
  const create = initializer => {
    let state;
    const set = change => { state = { ...state, ...change }; };
    state = initializer(set, () => state);
    return Object.assign(() => state, { getState: () => state, setState: set });
  };
  const exports = {};
  const source = ts.transpileModule(readFileSync(new URL('../src/stores/scenarioResourceStore.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(source, { exports, require: name => {
    if (name === 'zustand') return { create };
    if (name === './authStore') return { useAuthStore: auth };
    if (name === '@/services/businessScenariosApi') return { BusinessScenariosApi: { resources: () => new Promise(resolve => pending.push(resolve)) } };
    throw new Error(`Unexpected import: ${name}`);
  } });
  return {
    store: exports.useScenarioResourceStore,
    resolve: data => pending.shift()(data),
    changeIdentity: next => { const previous = { user: identity }; identity = next; listeners.forEach(listener => listener({ user: next }, previous)); },
  };
}

test('logout or switching companies clears resources and ignores a previous request', async () => {
  const fixture = harness();
  const promise = fixture.store.getState().load();
  fixture.changeIdentity({ id: 'staff-b', project_id: 'project-b' });
  fixture.resolve({ project_id: 'project-a', channel: 'web', sections: [{ name: 'private-company-resource' }] });
  await promise;
  assert.equal(fixture.store.getState().catalog, null);
  assert.equal(fixture.store.getState().loading, false);
});

test('a mismatched tenant response becomes an error rather than displayed resources', async () => {
  const fixture = harness();
  const promise = fixture.store.getState().load();
  fixture.resolve({ project_id: 'project-b', channel: 'web', sections: [] });
  await promise;
  assert.equal(fixture.store.getState().catalog, null);
  assert.equal(fixture.store.getState().failed, true);
});

test('changing the knowledge channel invalidates the previous availability result', async () => {
  const fixture = harness();
  const first = fixture.store.getState().load();
  fixture.store.getState().setChannel('wecom_kf');
  fixture.resolve({ project_id: 'project-a', channel: 'web', sections: [] });
  await first;
  assert.equal(fixture.store.getState().catalog, null);
  const second = fixture.store.getState().load();
  fixture.resolve({ project_id: 'project-a', channel: 'wecom_kf', sections: [] });
  await second;
  assert.equal(fixture.store.getState().catalog.channel, 'wecom_kf');
});

test('role changes clear the catalogue and cannot reuse a previous role request', async () => {
  const fixture = harness();
  const promise = fixture.store.getState().load();
  fixture.changeIdentity({ id: 'staff-a', project_id: 'project-a', role: 'user' });
  fixture.resolve({ project_id: 'project-a', channel: 'web', sections: [] });
  await promise;
  assert.equal(fixture.store.getState().catalog, null);
  assert.equal(fixture.store.getState().loading, false);
});
