import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/receptionModeSync.ts', import.meta.url), 'utf8');
const exports = {};
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { exports });
const { createReceptionModeSync, resolveReceptionMode } = exports;
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

test('three independent clients converge on the server mode in every direction', async () => {
  let server = 'auto';
  const screens = ['manual', 'assist', 'auto'];
  const clients = screens.map((_, index) => createReceptionModeSync({ read: async () => server, apply: mode => { screens[index] = mode; } }));
  for (const mode of ['auto', 'assist', 'manual', 'auto']) {
    server = mode;
    await Promise.all(clients.map(client => { client.invalidate(); return client.refresh(); }));
    assert.deepEqual(screens, [mode, mode, mode]);
  }
});

test('an invalidated in-flight read cannot overwrite a newer mode', async () => {
  const old = deferred(); const applied = []; let calls = 0;
  const client = createReceptionModeSync({ read: () => ++calls === 1 ? old.promise : Promise.resolve('manual'), apply: value => applied.push(value) });
  const pending = client.refresh();
  client.invalidate(); const current = client.refresh(); old.resolve('auto');
  assert.equal(await pending, 'manual'); assert.equal(await current, 'manual');
  assert.deepEqual(applied, ['manual']); assert.equal(calls, 2);
});

test('mode mutations pause refreshes and reconcile after the server acknowledges', async () => {
  const old = deferred(); const applied = []; let calls = 0;
  const client = createReceptionModeSync({ read: () => ++calls === 1 ? old.promise : Promise.resolve('assist'), apply: value => applied.push(value) });
  const pending = client.refresh(); client.pause(); old.resolve('auto'); await pending;
  assert.deepEqual(applied, []);
  assert.equal(await client.resume(), 'assist'); assert.deepEqual(applied, ['assist']);
});

test('late reads after leaving a customer do not update another customer', async () => {
  const read = deferred(); const applied = [];
  const client = createReceptionModeSync({ read: () => read.promise, apply: value => applied.push(value) });
  const pending = client.refresh(); client.dispose(); read.resolve('manual');
  assert.equal(await pending, null); assert.deepEqual(applied, []);
  assert.equal(await client.refresh(), null);
});

test('network failures preserve existing state and a subsequent read recovers', async () => {
  let fail = true; const applied = [];
  const client = createReceptionModeSync({ read: async () => { if (fail) throw Error('offline'); return 'manual'; }, apply: value => applied.push(value) });
  await assert.rejects(client.refresh(), /offline/); assert.deepEqual(applied, []);
  fail = false; assert.equal(await client.refresh(), 'manual'); assert.deepEqual(applied, ['manual']);
});

test('mode resolution preserves legacy safety overrides and all three modes', () => {
  assert.equal(resolveReceptionMode({ service_mode: 'auto', ai_disabled: true }), 'manual');
  assert.equal(resolveReceptionMode({ service_mode: 'assist', ai_disabled: false }), 'assist');
  assert.equal(resolveReceptionMode({ service_mode: 'manual', ai_disabled: false }), 'manual');
  assert.equal(resolveReceptionMode({ ai_disabled: false }), 'auto');
  assert.equal(resolveReceptionMode({ ai_settings: { ai_mode: 'assist' } }), 'assist');
  assert.equal(resolveReceptionMode({ ai_settings: {} }), 'auto');
});
