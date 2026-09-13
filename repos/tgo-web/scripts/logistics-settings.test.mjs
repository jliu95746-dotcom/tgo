import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const source = readFileSync(new URL('../src/components/settings/LogisticsSettings.tsx', import.meta.url), 'utf8');
const tree = ts.createSourceFile('settings.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let callback;
let saveCallback;
function visit(node) {
  if (ts.isVariableDeclaration(node) && node.name.getText(tree) === 'handleTest') callback = node.initializer;
  if (ts.isVariableDeclaration(node) && node.name.getText(tree) === 'handleSave') saveCallback = node.initializer;
  ts.forEachChild(node, visit);
}
visit(tree);
const compiled = ts.transpileModule(`globalThis.run = ${callback.getText(tree)};`, {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;

test('actual test button uses the selected unsaved tool without writing settings', async () => {
  const calls = [];
  const scope = { testTrackingNo: ' SF1234567890 ', settings: { query_tool_id: 'new-tool' },
    setTesting() {}, setNotice() {},
    logisticsApi: { testTool: async (...args) => { calls.push(args); return { success: true, message: '已获取轨迹' }; },
      updateSettings: () => assert.fail('Testing is not saving configuration') },
  };
  vm.runInNewContext(compiled, scope);
  await scope.run();
  assert.deepEqual(calls, [['SF1234567890', 'new-tool']]);
});

test('failed initial loading cannot overwrite saved settings with local defaults', async () => {
  const code = ts.transpileModule(`globalThis.save = ${saveCallback.getText(tree)};`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const scope = { settingsLoaded: false, saving: false, testing: false,
    setSaving: () => assert.fail('Must not enter save after failed loading'),
    logisticsApi: { updateSettings: () => assert.fail('Must not overwrite persisted settings') },
  };
  vm.runInNewContext(code, scope);
  await scope.save();
});

test('logistics page only offers implemented controls and retains on-demand queries', () => {
  const requireModule = createRequire(import.meta.url);
  const scope = { exports: {}, require: name => {
    if (name === 'react') return { ...React,
      useState: value => React.useState(value === true ? false : value),
    };
    if (name === '@/components/ui/Toggle') return ({ 'aria-label': label }) =>
      React.createElement('input', { type: 'checkbox', 'aria-label': label });
    if (name === '@/services/logisticsApi') return { logisticsApi: {} };
    if (name === '@/services/projectToolsApi') return {};
    return requireModule(name);
  } };
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
    jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
  } }).outputText, scope);
  const html = renderToStaticMarkup(React.createElement(scope.exports.default));
  assert.doesNotMatch(html, /签收后停止自动跟踪|已签收物流不再重复刷新/);
  for (const label of ['启用物流档案', '识别顾客发来的物流单号', '识别客服发出的物流单号',
    '无单号时使用档案查询', '顾客单号先验证再确认', '测试查询', '签收后保留天数']) {
    assert.ok(html.includes(label), label);
  }
  assert.match(html, /不在后台循环请求/);
});

test('retiring a control preserves existing saved values when other settings are edited', () => {
  const settings = { enabled: true, stop_after_delivered: false, poll_interval_minutes: 720,
    auto_capture_visitor_messages: false, auto_capture_staff_messages: true,
    verify_before_binding: true, auto_query_on_mention: true, query_tool_id: 'saved-tool',
    archive_after_days: 90, conflict_policy: 'keep_first' };
  let editable;
  function find(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(tree) === 'toEditableSettings') editable = node.initializer;
    ts.forEachChild(node, find);
  }
  find(tree);
  const scope = {};
  vm.runInNewContext(ts.transpileModule(`globalThis.edit = ${editable.getText(tree)};`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText, scope);
  const result = scope.edit(settings);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), settings);
});
