import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/business/DeviceScenarioGuide.tsx', import.meta.url), 'utf8');
const helperStart = source.indexOf('  const isCurrentSelection = (expectedDeviceId: string) => {');
const helperEnd = source.indexOf('\n\n  useEffect', helperStart);
const saveStart = source.indexOf('  const saveDraft = async () => {');
const readTestStart = source.indexOf('  const testRead = async () => {');
const start = source.indexOf('  const continueSetup = async () => {');
const end = source.indexOf('\n  return <div', start);
assert.ok(helperStart >= 0 && helperEnd > helperStart && saveStart >= 0
  && readTestStart > saveStart && start > readTestStart && end > start,
  'device handoff guard and callback exist');
const callbackSource = ts.transpileModule(`${source.slice(helperStart, helperEnd)}\n${source.slice(saveStart, end)}\nglobalThis.callback = continueSetup; globalThis.testRead = testRead; globalThis.saveDraft = saveDraft; globalThis.publishPolicy = publishPolicy;`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
}).outputText;

function fixture() {
  const user = { id: 'staff-a', project_id: 'project-a', role: 'admin' };
  const device = { id: 'device-a', project_id: 'project-a', status: 'online', device_name: '电脑 A' };
  const current = { user, selectedId: device.id, devices: [device] };
  const events = { policies: [], prepared: [], closed: 0, navigated: [], draftSaved: [], revisions: [] };
  let finish, finishRead, finishDraft, finishPublish;
  const context = {
    canContinue: true, device, deviceId: device.id, sameOrigin: true, user,
    origin: user, selectedIdRef: { get current() { return current.selectedId; } },
    useAuthStore: { getState: () => ({ user: current.user }) },
    useDeviceControlStore: { getState: () => ({ devices: current.devices,
      isLoading: false, loadError: null, hasLoadedDevices: true }) },
    scenarioId: 'files', targetDirectory: '/docs', maxLines: 100, maxOffset: 10000,
    purpose: '读取业务文件', businessSystem: '本机文件', draftRevision: 0,
    loadingDraft: false, draftLoadFailed: false, savingDraft: false, draftSaved: true,
    policyLoading: false, policyLoadFailed: false, policySaving: false, readPolicy: null,
    policyActive: true, testPath: '/docs/example.txt', testMarker: 'example', testingRead: false,
    setContinuing() {}, setPolicyLoadFailed() {}, setTestingRead() {}, setReadTestError() {}, setTestMarker() {},
    setSavingDraft() {}, setDraftSaveFailed() {}, setDraftSaved: value => events.draftSaved.push(value),
    setDraftRevision: value => events.revisions.push(value), setPolicySaving() {}, setPolicySaveFailed() {},
    setReadPolicy: value => events.policies.push(value),
    getDeviceReadPolicy: () => new Promise(resolve => { finish = resolve; }),
    testDeviceReadPolicy: () => new Promise(resolve => { finishRead = resolve; }),
    saveDeviceScenarioDraft: () => new Promise(resolve => { finishDraft = resolve; }),
    publishDeviceReadPolicy: () => new Promise(resolve => { finishPublish = resolve; }),
    useDeviceSetupStore: { getState: () => ({ prepare: (...args) => { events.prepared.push(args); return true; } }) },
    useAIStore: { getState: () => ({ setShowCreateAgentModal() {} }) },
    onClose: () => { events.closed += 1; }, navigate: path => events.navigated.push(path),
  };
  vm.runInNewContext(callbackSource, context);
  const policy = { device_id: device.id, project_id: user.project_id, tool_name: 'fs_read',
    status: 'active', verified: true, allowed_directory: '/docs', max_lines: 100, max_offset: 10000 };
  const draft = { device_id: device.id, project_id: user.project_id, scenario: 'files', revision: 1 };
  return { current, events, run: context.callback, testRead: context.testRead,
    saveDraft: context.saveDraft, publishPolicy: context.publishPolicy,
    finish: () => finish(policy), finishRead: () => finishRead(policy),
    finishDraft: () => finishDraft(draft), finishPublish: () => finishPublish(policy) };
}

test('a valid verified device policy hands off to the employee setup', async () => {
  const h = fixture();
  const pending = h.run(); h.finish(); await pending;
  assert.equal(h.events.prepared.length, 1);
  assert.deepEqual(h.events.navigated, ['/ai/agents']);
  assert.equal(h.events.closed, 1);
});

test('a policy response from a previous account cannot hand off a device', async () => {
  const h = fixture();
  const pending = h.run();
  h.current.user = { id: 'staff-b', project_id: 'project-b', role: 'admin' };
  h.finish(); await pending;
  assert.equal(h.events.prepared.length, 0);
  assert.equal(h.events.closed, 0);
  assert.equal(h.events.policies.length, 0);
});

test('a policy response for a device no longer selected cannot hand off', async () => {
  const h = fixture();
  const pending = h.run(); h.current.selectedId = 'device-b';
  h.finish(); await pending;
  assert.equal(h.events.prepared.length, 0);
  assert.equal(h.events.policies.length, 0);
});

test('a disconnected device cannot hand off after its policy response', async () => {
  const h = fixture();
  const pending = h.run(); h.current.devices[0] = { ...h.current.devices[0], status: 'offline' };
  h.finish(); await pending;
  assert.equal(h.events.prepared.length, 0);
  assert.equal(h.events.policies.length, 0);
});

test('a late read test cannot mark another selected device as verified', async () => {
  const h = fixture();
  const pending = h.testRead(); h.current.selectedId = 'device-b';
  h.finishRead(); await pending;
  assert.equal(h.events.policies.length, 0);
});

test('a late draft save cannot mark another selected device as saved', async () => {
  const h = fixture();
  const pending = h.saveDraft(); h.current.selectedId = 'device-b';
  h.finishDraft(); await pending;
  assert.deepEqual(h.events.draftSaved, [false]);
  assert.equal(h.events.revisions.length, 0);
});

test('a late policy publication cannot replace another selected device policy', async () => {
  const h = fixture();
  const pending = h.publishPolicy(); h.current.selectedId = 'device-b';
  h.finishPublish(); await pending;
  assert.equal(h.events.policies.length, 0);
});
