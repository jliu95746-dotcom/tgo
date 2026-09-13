import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
const source = readFileSync(new URL('../src/components/ai/DeviceSelectionModal.tsx', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const messages = JSON.parse(readFileSync(new URL('../src/i18n/locales/zh.json', import.meta.url), 'utf8'));
function t(key, fallback, options = {}) {
  let value = key.split('.').reduce((part, name) => part?.[name], messages) ?? fallback ?? key;
  for (const [name, replacement] of Object.entries(options)) value = value.replaceAll(`{{${name}}}`, String(replacement));
  return value;
}

function fixture(state, selection = null) {
  const confirmed = [];
  let closed = 0;
  let hook = 0;
  const exports = {};
  const hooks = { ...React, useEffect() {}, useMemo: (fn) => fn(), useState: (initial) => {
    const index = hook++;
    return [index === 1 ? selection : initial, () => {}];
  } };
  const dependencies = {
    react: hooks,
    'react-router-dom': { useNavigate: () => () => {} },
    'react-i18next': { useTranslation: () => ({ t }) },
    '@/hooks/useDebouncedValue': { useDebouncedValue: (value) => value },
    '@/hooks/useToast': { useToast: () => ({ showToast() {} }) },
    '@/stores/deviceControlStore': { useDeviceControlStore: () => state },
  };
  vm.runInNewContext(compiled, { exports, console, require: (name) => dependencies[name] ?? require(name) });
  const tree = exports.default({ isOpen: true, selectedDeviceId: selection,
    onConfirm: (id) => confirmed.push(id), onClose: () => { closed++; } });
  const buttons = [];
  function visit(node) {
    if (Array.isArray(node)) return node.forEach(visit);
    if (!node?.props) return;
    if (node.type === 'button') buttons.push(node);
    visit(node.props.children);
  }
  visit(tree);
  const confirm = buttons.find((button) => button.props.children === t('agents.deviceSelectModal.confirmSelection'));
  assert.ok(confirm);
  return { html: renderToStaticMarkup(tree), confirm: confirm.props, confirmed, closed: () => closed };
}

const device = { id: 'owned', device_name: '测试电脑', os: 'Windows', device_type: 'desktop', status: 'online' };
const ready = { devices: [device], hasLoadedDevices: true, isLoading: false, loadError: null, error: null };

test('failed device loading cannot confirm or erase an existing binding and never shows zero devices', () => {
  const view = fixture({ ...ready, devices: [], loadError: 'HTTP 503', error: null }, 'owned');
  assert.equal(view.confirm.disabled, true);
  view.confirm.onClick();
  assert.deepEqual(view.confirmed, []);
  assert.equal(view.closed(), 0);
  assert.match(view.html, /HTTP 503/);
  assert.match(view.html, /设备数量暂不可用/);
  assert.doesNotMatch(view.html, /共 0 台设备|暂无可用设备/);
});

test('initial and refreshing lists cannot be confirmed', () => {
  for (const state of [{ ...ready, hasLoadedDevices: false }, { ...ready, isLoading: true }]) {
    const view = fixture(state, 'owned');
    assert.equal(view.confirm.disabled, true);
    view.confirm.onClick();
    assert.deepEqual(view.confirmed, []);
  }
});

test('a device missing from the latest successful list is not submitted as a valid selection', () => {
  const view = fixture(ready, 'removed-device');
  assert.equal(view.confirm.disabled, true);
  view.confirm.onClick();
  assert.deepEqual(view.confirmed, []);
});

test('a loaded selection can be confirmed and unrelated operation errors do not hide the list', () => {
  const view = fixture({ ...ready, error: 'Failed to generate bind code' }, 'owned');
  assert.equal(view.confirm.disabled, false);
  assert.match(view.html, /测试电脑/);
  view.confirm.onClick();
  assert.deepEqual(view.confirmed, ['owned']);
  assert.equal(view.closed(), 1);
});

test('explicit deselection remains possible after a successful empty response', () => {
  const view = fixture({ ...ready, devices: [] });
  assert.equal(view.confirm.disabled, false);
  assert.match(view.html, /共 0 台设备/);
  assert.match(view.html, /暂无可用设备/);
  view.confirm.onClick();
  assert.deepEqual(view.confirmed, [null]);
});
