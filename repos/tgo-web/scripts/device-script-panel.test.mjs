import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import { test } from 'node:test';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(import.meta.url);
const compile = path => ts.transpileModule(readFileSync(new URL('../src/' + path, import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
function load(path, dependencies = {}) {
  const exports = {};
  vm.runInNewContext(compile(path), { exports, Number, RegExp, require: name => dependencies[name] ?? require(name) });
  return exports;
}
const messages = load('i18n/deviceScripts.ts').deviceScriptsZh;
const t = (key, options = {}) => {
  let value = key.split('.').slice(1).reduce((item, name) => item?.[name], messages) ?? key;
  for (const [name, content] of Object.entries(options)) value = value.replaceAll(`{{${name}}}`, String(content));
  return value;
};
const device = { id: 'device', project_id: 'project', status: 'online' };
const own = { session: 1, deviceId: 'device', projectId: 'project', userId: 'admin' };
function html(changes = {}, role = 'admin') {
  const state = {
    owner: own, local: null, policy: null, file: null, screen: null,
    loading: false, loaded: true, busy: false, error: false, notice: null,
    catalog: { protocol_version: 1, scripts: [
      { script_id: 'report', title: '自有报表审核样本', version: 'v1', effect: 'read_only' },
      { script_id: 'write', title: '受限写操作样本', version: 'v1', effect: 'write' },
    ] }, open() {}, close() {}, ...changes,
  };
  const View = load('components/business/DeviceScriptPanel.tsx', {
    'react-i18next': { useTranslation: () => ({ t }) },
    '@/stores/authStore': { useAuthStore: selector => selector({ user: { id: 'admin', project_id: 'project', role } }) },
    '@/stores/deviceScriptStore': { useDeviceScriptStore: () => state },
    '@/utils/deviceScriptPolicy': load('utils/deviceScriptPolicy.ts'),
  }).default;
  return renderToStaticMarkup(React.createElement(View, { device, draftSaved: true }));
}

test('configuration describes approved scripts and keeps business verification separate', () => {
  const result = html();
  assert.match(result, /自有报表审核样本/);
  assert.match(result, /暂不支持写操作/);
  assert.match(result, /不代表业务测试通过/);
  assert.doesNotMatch(result, /name="(?:command|script_path|interpreter)"/);
});

test('ordinary employees and a stale project never render approved device metadata', () => {
  assert.doesNotMatch(html({}, 'agent'), /自有报表审核样本/);
  assert.doesNotMatch(html({ owner: { ...own, projectId: 'other-project' } }), /自有报表审核样本/);
});

test('unconfirmed operations keep a refresh action and an explicit warning', () => {
  const result = html({ error: true });
  assert.match(result, /role="alert"/);
  assert.match(result, /不要重复提交/);
  assert.match(result, /刷新脚本状态/);
});
