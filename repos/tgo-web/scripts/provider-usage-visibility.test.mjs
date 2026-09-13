import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const locale = JSON.parse(read('../src/i18n/locales/zh.json'));
const t = (key, values = {}) => {
  const template = key.split('.').reduce((value, part) => value?.[part], locale) || key;
  return template.replace(/\{\{(\w+)\}\}/g, (_, name) => String(values[name] ?? ''));
};
const module = { exports: {} };
const rowActions = [];
vm.runInNewContext(ts.transpileModule(read('../src/components/settings/ProviderCard.tsx'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText, {
  module, exports: module.exports, require: name => {
    if (name === 'react-i18next') return { useTranslation: () => ({ t }) };
    if (name === '@/utils/modelUsage') return { modelUsageTypes: ['chat', 'embedding', 'asr', 'ocr', 'vlm'] };
    if (name === './ModelRowActions') return { default: props => {
      rowActions.push(props);
      return React.createElement('button', { 'aria-label': props.modelId + ' 的更多操作' }, '更多');
    } };
    return require(name);
  },
});
const base = {
  provider: { id: 'provider', name: 'Existing service', enabled: true, models: ['chat-model'], modelTypes: { 'chat-model': 'chat' } },
  selections: { chat: 'original:kept-model', embedding: '', asr: '', ocr: '', vlm: '' },
  usageOptions: { chat: [{ value: 'provider:chat-model' }], embedding: [], asr: [], ocr: [], vlm: [] },
  defaultsSynced: true, usageDisabled: false, testingId: null,
  onEdit() {}, onDelete() {}, onTest() {}, onUsageChange() {},
};

test('each model row has a dropdown with all five purposes and unavailable types disabled', () => {
  const html = renderToStaticMarkup(React.createElement(module.exports.default, base));
  for (const name of Object.values(locale.modelSetup.uses)) assert.ok(html.includes(name), name);
  assert.equal((html.match(/<select/g) || []).length, 1);
  assert.equal((html.match(/<option[^>]*\sdisabled=""/g) || []).length, 5);
});

test('configured purpose is selected, not a separate assignment button', () => {
  const html = renderToStaticMarkup(React.createElement(module.exports.default, { ...base, selections: { ...base.selections, chat: 'provider:chat-model' } }));
  assert.match(html, /<option[^>]*value="chat"[^>]*selected=""/);
  assert.equal(html.includes('系统默认'), false);
  assert.equal((html.match(/<select/g) || []).length, 1);
});

test('disabled service cannot change system purposes', () => {
  const html = renderToStaticMarkup(React.createElement(module.exports.default, { ...base, provider: { ...base.provider, enabled: false } }));
  assert.equal((html.match(/<select[^>]*\sdisabled=""/g) || []).length, 1);
});

test('dropdown changes request confirmation without mutating existing settings', () => {
  const calls = [];
  const tree = module.exports.default({ ...base, onUsageChange: (...args) => calls.push(args) });
  const findSelect = node => {
    if (!node || typeof node !== 'object') return null;
    if (Array.isArray(node)) return node.map(findSelect).find(Boolean);
    if (node.type === 'select') return node;
    return findSelect(node.props?.children);
  };
  const select = findSelect(tree);
  assert.ok(select, 'row contains a select control');
  select.props.onChange({ target: { value: 'chat' } });
  assert.deepEqual(calls, [['chat', 'provider:chat-model']]);
  select.props.onChange({ target: { value: 'embedding' } });
  assert.equal(calls.length, 1, 'incompatible purpose is ignored');
  assert.equal(base.selections.chat, 'original:kept-model');
});

test('management and new-service dialogs never select or persist system purposes', () => {
  const source = read('../src/components/settings/ProviderConfigModal.tsx');
  assert.equal(/ProviderDefaultChoices|onApplyUsage|setChoices|currentSelections/.test(source), false);
  const page = read('../src/components/settings/ModelProvidersSettings.tsx');
  assert.equal(/onApplyUsage=/.test(page), false);
});

test('row edit and delete target only the chosen model', () => {
  rowActions.length = 0;
  const edits = [], deletes = [];
  renderToStaticMarkup(React.createElement(module.exports.default, {
    ...base,
    provider: { ...base.provider, models: ['chat-model', 'other-model'] },
    onEdit: (...args) => edits.push(args),
    onDelete: (...args) => deletes.push(args),
  }));
  assert.equal(rowActions.length, 2);
  rowActions[1].onEdit();
  rowActions[1].onDelete();
  assert.equal(edits[0][0].id, 'provider');
  assert.equal(edits[0][1], 'other-model');
  assert.deepEqual(deletes, [['provider', 'other-model']]);
});
