import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
const messages = JSON.parse(readFileSync(new URL('../src/i18n/locales/zh.json', import.meta.url), 'utf8'));
const t = (key, fallback) => key.split('.').reduce((value, part) => value?.[part], messages)
  ?? (typeof fallback === 'string' ? fallback : fallback?.defaultValue) ?? key;

function loadModule(path, dependencies, globals = {}) {
  const source = readFileSync(new URL('../src/' + path, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, Error, console, AbortController, setTimeout, clearTimeout,
    ...globals, require: (name) => dependencies[name] ?? require(name) });
  return exports;
}

function loadStore(api, globals) {
  return loadModule('stores/deviceControlStore.ts', {
    '@/services/deviceControlApi': api,
    i18next: { t },
  }, globals).useDeviceControlStore;
}

function render(state) {
  const hiddenModal = { default: () => null, __esModule: true };
  const View = loadModule('components/ai/device-control/DeviceManagement.tsx', {
    'react-i18next': { useTranslation: () => ({ t }) },
    '@/stores/deviceControlStore': { useDeviceControlStore: () => state },
    './DeviceCard': { __esModule: true, default: ({ device }) => React.createElement('article', {}, device.device_name) },
    './BindCodeModal': hiddenModal,
    './EditDeviceModal': hiddenModal,
    './DeviceSessionBrowser': hiddenModal,
    '@/components/ui/ConfirmDialog': hiddenModal,
  }).default;
  return renderToStaticMarkup(React.createElement(View));
}

const empty = { devices: [], isLoading: false, error: null, loadError: null, hasLoadedDevices: true };
const counts = (html) => [...html.matchAll(/<p class="text-2xl font-bold[^>]*>(.*?)<\/p>/g)].map((match) => match[1]);

test('a failed device request shows an error and retry, never a successful empty state or zero counts', () => {
  const html = render({ ...empty, hasLoadedDevices: false, loadError: 'Device control service unavailable', error: 'Device control service unavailable' });
  assert.match(html, /role="alert"/);
  assert.match(html, /设备列表加载失败/);
  assert.match(html, /Device control service unavailable/);
  assert.match(html, /重试/);
  assert.doesNotMatch(html, /还没有连接的设备|没有找到匹配的设备/);
  assert.deepEqual(counts(html), ['—', '—', '—']);
});

test('initial and loading states do not briefly claim there are no devices', () => {
  for (const state of [{ ...empty, hasLoadedDevices: false }, { ...empty, isLoading: true }]) {
    const html = render(state);
    assert.doesNotMatch(html, /还没有连接的设备/);
    assert.deepEqual(counts(html), ['—', '—', '—']);
  }
});

test('a successful empty response still displays the setup guide and true zero counts', () => {
  const html = render(empty);
  assert.match(html, /还没有连接的设备/);
  assert.deepEqual(counts(html), ['0', '0', '0']);
});

test('refresh failure preserves cached cards but marks their status as not updated', () => {
  const html = render({ ...empty, devices: [{ id: 'owned', device_name: '测试电脑', status: 'online', os: 'Windows' }], loadError: 'HTTP 503' });
  assert.match(html, /测试电脑/);
  assert.match(html, /上次加载的设备，在线状态可能已变化/);
  assert.deepEqual(counts(html), ['—', '—', '—']);
});

test('an operation failure is not misreported as a list loading failure', () => {
  const html = render({ ...empty, error: 'Failed to generate bind code' });
  assert.match(html, /Failed to generate bind code/);
  assert.doesNotMatch(html, /设备列表加载失败/);
  assert.deepEqual(counts(html), ['0', '0', '0']);
});

test('load error survives clearing unrelated operation errors until a successful refresh', async () => {
  let fail = true;
  const store = loadStore({ listDevices: async () => {
    if (fail) throw new Error('HTTP 503');
    return { devices: [] };
  } });
  assert.equal(store.getState().hasLoadedDevices, false);
  await store.getState().loadDevices();
  assert.equal(store.getState().isLoading, false);
  assert.equal(store.getState().loadError, 'HTTP 503');
  assert.equal(store.getState().error, 'HTTP 503');
  store.getState().clearError();
  assert.equal(store.getState().loadError, 'HTTP 503');
  fail = false;
  await store.getState().loadDevices();
  assert.equal(store.getState().hasLoadedDevices, true);
  assert.equal(store.getState().loadError, null);
});

test('malformed list responses are failures rather than empty successes', async () => {
  const store = loadStore({ listDevices: async () => ({}) });
  await store.getState().loadDevices();
  assert.ok(store.getState().loadError);
  assert.equal(store.getState().hasLoadedDevices, false);
  assert.equal(store.getState().devices.length, 0);
});

test('an earlier response cannot overwrite the latest successful device list', async () => {
  let resolve;
  let firstSignal;
  const slow = new Promise((done) => { resolve = done; });
  const store = loadStore({ listDevices: (params, options) => {
    if (params.status === 'offline') { firstSignal = options?.signal; return slow; }
    return Promise.resolve({ devices: [{ id: 'latest', status: 'online' }] });
  } });
  const first = store.getState().loadDevices(undefined, 'offline');
  await store.getState().loadDevices();
  resolve({ devices: [{ id: 'stale', status: 'offline' }] });
  await first;
  assert.equal(store.getState().devices[0].id, 'latest');
  assert.equal(firstSignal.aborted, true);
  assert.equal(store.getState().loadError, null);
});

test('late failures never replace the current successful state', async () => {
  let reject;
  const slow = new Promise((_resolve, fail) => { reject = fail; });
  const store = loadStore({ listDevices: (params) => params.status === 'offline'
    ? slow : Promise.resolve({ devices: [] }) });
  const first = store.getState().loadDevices(undefined, 'offline');
  await store.getState().loadDevices();
  reject(new Error('stale error'));
  await first;
  assert.equal(store.getState().loadError, null);
  assert.equal(store.getState().error, null);
  assert.equal(store.getState().isLoading, false);
});

test('an unresponsive transport times out, aborts and permits a successful retry', { timeout: 1000 }, async () => {
  let signal;
  let hang = true;
  const store = loadStore({ listDevices: (_params, options) => {
    signal = options?.signal;
    return hang ? new Promise(() => {}) : Promise.resolve({ devices: [] });
  } }, { setTimeout: (callback) => setTimeout(callback, 20) });
  await store.getState().loadDevices();
  assert.equal(signal.aborted, true);
  assert.equal(store.getState().isLoading, false);
  assert.equal(store.getState().loadError, '设备列表加载超时，请重试。');
  hang = false;
  await store.getState().loadDevices();
  assert.equal(store.getState().hasLoadedDevices, true);
  assert.equal(store.getState().loadError, null);
});

test('the actual list API forwards cancellation without changing filter parameters', async () => {
  const calls = [];
  const api = loadModule('services/deviceControlApi.ts', {
    './api': { get: async (...args) => { calls.push(args); return { devices: [] }; } },
  }, { URLSearchParams });
  const controller = new AbortController();
  await api.listDevices({ status: 'online', limit: 10 }, { signal: controller.signal });
  assert.equal(calls[0][0], '/v1/device-control/devices?status=online&limit=10');
  assert.equal(calls[0][1]?.signal, controller.signal);
});
