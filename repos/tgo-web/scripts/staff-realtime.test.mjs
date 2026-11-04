import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/services/wukongimWebSocket.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const events = Object.fromEntries(['Connect', 'Disconnect', 'Reconnecting', 'Error', 'Message', 'CustomEvent'].map(name => [name, name]));
const settle = () => new Promise(resolve => setImmediate(resolve));

function harness() {
  const timers = new Map();
  let nextTimer = 0;
  const instances = [];
  const window = Object.assign(new EventTarget(), {
    setTimeout(callback, delay) { timers.set(++nextTimer, { callback, delay }); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
  });
  const document = Object.assign(new EventTarget(), { visibilityState: 'visible' });
  const navigator = { onLine: true };
  const exports = {};
  vm.runInNewContext(compiled, {
    exports, window, document, navigator, AbortController, Error,
    console: { log() {}, warn() {}, error() {} },
    require(name) {
      if (name === 'easyjssdk') return {
        WKIMEvent: events, WKIMChannelType: {},
        ReasonCode: new Proxy({}, { get: (_, key) => key }),
        WKIM: { init(url, auth) {
          const handlers = new Map();
          const instance = {
            url, auth, destroyed: false,
            on(event, handler) { handlers.set(event, handler); },
            off(event, handler) { if (handlers.get(event) === handler) handlers.delete(event); },
            emit(event, value) { handlers.get(event)?.(value); },
            async connect() { instance.emit('Connect', {}); },
            destroy() { instance.destroyed = true; handlers.clear(); },
          };
          instances.push(instance);
          return instance;
        } },
      };
      if (name === './staffSession') return { tokenNeedsRefresh: value => value === 'expiring-token' };
      return {};
    },
  });
  const service = new exports.WuKongIMWebSocketService();
  const config = token => ({ serverUrl: 'wss://example.invalid', uid: 'fixture-staff', token });
  const tick = async delay => {
    const matches = [...timers].filter(([, timer]) => timer.delay === delay);
    assert.equal(matches.length, 1);
    const [id, timer] = matches[0];
    timers.delete(id);
    timer.callback();
    await settle();
  };
  return { service, config, timers, instances, window, document, navigator, tick };
}

test('1008 refreshes credentials and reconnects once using a new SDK instance', async () => {
  const h = harness();
  const refreshes = [];
  const stop = h.service.maintainConnection(async (_signal, refresh) => {
    refreshes.push(refresh);
    await h.service.init(h.config(refresh ? 'new-token' : 'old-token'));
  });
  await settle();
  assert.equal(h.service.getConnectionStatus().isConnected, true);
  h.instances[0].emit('Disconnect', { code: 1008 });
  assert.equal(h.instances[0].destroyed, true);
  assert.equal(h.service.getConnectionStatus().isConnected, false);
  await h.tick(1000);
  assert.deepEqual(refreshes, [false, true]);
  assert.equal(h.instances[1].auth.token, 'new-token');
  assert.equal(h.service.getConnectionStatus().isConnected, true);
  assert.equal(h.timers.size, 0);
  stop();
});

test('initial route failure retries with backoff and online wake reconnects after a ping outage', async () => {
  const h = harness();
  let calls = 0;
  const stop = h.service.maintainConnection(async () => {
    if (++calls <= 2) throw new Error('route unavailable');
    await h.service.init(h.config('valid-token'));
  });
  await settle();
  await h.tick(1000);
  await h.tick(2000);
  assert.equal(h.service.getConnectionStatus().isConnected, true);
  h.navigator.onLine = false;
  h.instances[0].emit('Reconnecting', {});
  assert.equal(h.instances[0].destroyed, true);
  assert.equal(h.timers.size, 0);
  h.navigator.onLine = true;
  h.window.dispatchEvent(new Event('online'));
  await settle();
  assert.equal(calls, 4);
  assert.equal(h.instances.length, 2);
  assert.equal(h.service.getConnectionStatus().isConnected, true);
  stop();
});

test('fresh staff credentials rotate an expiring socket and deliberate disconnect stays offline', async () => {
  const h = harness();
  let token = 'expiring-token';
  const stop = h.service.maintainConnection(async () => { await h.service.init(h.config(token)); });
  await settle();
  token = 'current-token';
  h.service.renewConnectionToken(token);
  await settle();
  assert.equal(h.instances[0].destroyed, true);
  assert.equal(h.instances[1].auth.token, 'current-token');
  h.service.safeDisconnect();
  h.window.dispatchEvent(new Event('online'));
  h.document.dispatchEvent(new Event('visibilitychange'));
  await settle();
  assert.equal(h.instances.length, 2);
  assert.equal(h.service.getConnectionStatus().isConnected, false);
  assert.equal(h.timers.size, 0);
  stop();
});

test('a clean server close reconnects and unmount cancels a scheduled retry', async () => {
  const h = harness();
  const stop = h.service.maintainConnection(async () => { await h.service.init(h.config('valid-token')); });
  await settle();
  h.instances[0].emit('Disconnect', { code: 1000 });
  await h.tick(1000);
  assert.equal(h.service.getConnectionStatus().isConnected, true);
  h.instances[1].emit('Disconnect', { code: 1001 });
  assert.equal(h.timers.size, 1);
  stop();
  assert.equal(h.timers.size, 0);
});
