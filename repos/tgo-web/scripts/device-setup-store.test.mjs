import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function fixture() {
  let user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  const listeners = [];
  const auth = { getState: () => ({ user }), subscribe: fn => listeners.push(fn) };
  const create = initializer => {
    let state;
    const set = change => { state = { ...state, ...change }; };
    state = initializer(set, () => state);
    return Object.assign(() => state, { getState: () => state, setState: set });
  };
  const output = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/stores/deviceSetupStore.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports: output, require: name => {
    if (name === 'zustand') return { create };
    if (name === './authStore') return { useAuthStore: auth };
    throw Error(`Unexpected dependency ${name}`);
  } });
  return { store: output.useDeviceSetupStore, changeUser: next => {
    const previous = { user }; user = next;
    listeners.forEach(fn => fn({ user }, previous));
  } };
}
const device = { id: 'device-a', project_id: 'project-a', device_name: '业务电脑', status: 'online' };

function verifiedPolicyGuard() {
  const output = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/deviceSetup.ts', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports: output });
  return output.isVerifiedDeviceReadPolicy;
}

test('employee handoff needs a current verified policy for the same tenant and device', () => {
  const verify = verifiedPolicyGuard();
  const policy = { device_id: 'device-a', project_id: 'project-a', tool_name: 'fs_read', status: 'active', verified: true };
  assert.equal(verify(policy, 'device-a', 'project-a'), true);
  for (const stale of [null, { ...policy, verified: false }, { ...policy, status: 'pending' },
    { ...policy, device_id: 'device-b' }, { ...policy, project_id: 'project-b' },
    { ...policy, tool_name: 'shell_exec' }]) {
    assert.equal(verify(stale, 'device-a', 'project-a'), false);
  }
});

test('device purpose and exact selection survive navigation in memory', () => {
  const { store } = fixture();
  assert.equal(store.getState().prepare('files', device), true);
  assert.equal(store.getState().handoff.scenarioId, 'files');
  assert.equal(store.getState().handoff.deviceId, device.id);
  assert.equal(store.getState().handoff.deviceName, device.device_name);
});
test('foreign, offline and unsupported screen selections cannot prepare an employee', () => {
  for (const [scenario, selected] of [['files', { ...device, project_id: 'project-b' }],
    ['report', { ...device, status: 'offline' }], ['screen', device]]) {
    const { store } = fixture();
    assert.equal(store.getState().prepare(scenario, selected), false);
    assert.equal(store.getState().handoff, null);
  }
});
test('logout, staff, company or role change clears the previous device purpose', () => {
  for (const next of [null, { id: 'staff-b', project_id: 'project-a', role: 'admin' },
    { id: 'staff-a', project_id: 'project-b', role: 'admin' }, { id: 'staff-a', project_id: 'project-a', role: 'user' }]) {
    const h = fixture(); h.store.getState().prepare('files', device);
    h.changeUser(next);
    assert.equal(h.store.getState().handoff, null);
  }
});
test('a role-preserving authentication refresh keeps the selected purpose', () => {
  const h = fixture(); h.store.getState().prepare('report', device);
  h.changeUser({ id: 'staff-a', project_id: 'project-a', role: 'admin' });
  assert.equal(h.store.getState().handoff.scenarioId, 'report');
});
