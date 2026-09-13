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
function t(key, options = {}) {
  let result = key.split('.').reduce((value, part) => value?.[part], messages) ?? key;
  for (const [name, value] of Object.entries(options)) result = result.replaceAll(`{{${name}}}`, String(value));
  return result;
}
function component(relativePath, state) {
  const source = readFileSync(new URL('../src/' + relativePath, import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, require: (name) => {
    if (name === 'react-i18next') return { useTranslation: () => ({ t }) };
    if (name === '@/stores/deviceSessionStore') return { useDeviceSessionStore: () => state, STEP_PAGE_SIZE: 100, SESSION_PAGE_SIZE: 20 };
    if (name === '@/stores/authStore') return { useAuthStore: (selector) => selector({ user: { project_id: 'owned' } }) };
    if (name.endsWith('/DeviceSessionStatus')) return component('components/ai/device-control/DeviceSessionStatus.tsx', state);
    if (name.endsWith('/SessionPagination')) return component('components/ai/device-control/SessionPagination.tsx', state);
    if (name.endsWith('/ComputerUseSessionMonitor')) return component('components/ai/remote-agent/ComputerUseSessionMonitor.tsx', state);
    return require(name);
  } });
  return exports;
}
const detail = {
  id: 'owned-session', device_name: '测试电脑', agent_name: '测试员工', status: 'interrupted',
  started_at: '2026-09-08T12:00:00Z', ended_at: null, actions_count: 2, failed_actions_count: 1,
  screenshots_count: 0, step_total: 120,
  steps: [{ id: 'step', tool_name: 'fixture_echo', status: 'failed', started_at: '2026-09-08T12:00:01Z', ended_at: null }],
};

test('detail renders actual steps, interruption warning and pagination without fake progress', () => {
  const state = { selectedId: detail.id, detail, stepPage: 0, isDetailLoading: false, detailError: null };
  const View = component('components/ai/remote-agent/ComputerUseSessionMonitor.tsx', state).default;
  const html = renderToStaticMarkup(React.createElement(View, { sessionId: detail.id, onClose() {} }));
  assert.match(html, /fixture_echo/);
  assert.match(html, /未能确认这次任务的最终结果/);
  assert.match(html, /下一页/);
  assert.match(html, /失败或中断/);
  assert.doesNotMatch(html, /0%|查看截图|当前轮次/);
});

test('failure keeps a retry and back action visible, not an empty panel', () => {
  const state = { selectedId: detail.id, detail: null, stepPage: 0, isDetailLoading: false, detailError: t('deviceControl.sessions.detailFailed') };
  const View = component('components/ai/remote-agent/ComputerUseSessionMonitor.tsx', state).default;
  const html = renderToStaticMarkup(React.createElement(View, { sessionId: detail.id, onClose() {} }));
  assert.match(html, /role="alert"/);
  assert.match(html, /返回任务列表/);
  assert.match(html, /重试/);
});

test('browser renders owned task selection and accessible dialog', () => {
  const state = { ownerProjectId: 'owned', selectedId: null, deviceId: null, listPage: 0,
    sessions: [detail], total: 1, listError: null, isListLoading: false };
  const View = component('components/ai/device-control/DeviceSessionBrowser.tsx', state).default;
  const html = renderToStaticMarkup(React.createElement(View, { devices: [], onClose() {} }));
  assert.match(html, /<dialog/);
  assert.match(html, /aria-labelledby="device-session-title"/);
  assert.match(html, /测试电脑/);
  assert.match(html, /全部设备/);
});
