import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';

const read = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
function load(path, dependencies = {}) {
  const scope = { exports: {}, require: name => {
    if (name in dependencies) return dependencies[name];
    throw new Error(`Unexpected dependency ${name}`);
  } };
  vm.runInNewContext(ts.transpileModule(read(path), { compilerOptions: {
    target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText, scope);
  return scope.exports;
}
const types = load('types/logisticsProvider.ts');
const { default: Modal } = load('components/ai/LogisticsProviderModal.tsx', {
  react: React, 'react/jsx-runtime': jsxRuntime,
  'lucide-react': { X: () => null, Truck: () => null, Loader2: () => null },
  'react-i18next': { useTranslation: () => ({ t: key => key }) },
  '@/stores/authStore': { useAuthStore: selector => selector({ user: { project_id: 'fixture' } }) },
  '@/services/logisticsProviderService': {}, '@/services/logisticsApi': {},
  '@/types/logisticsProvider': types,
});
function render(config) {
  return renderToStaticMarkup(React.createElement(Modal, {
    tool: config ? { id: 'fixture', endpoint: 'https://provider.example/query', config: { logistics_provider: config } } : null,
    onClose() {}, onSaved() {},
  }));
}

test('new setup asks user to choose without exposing advanced parameters', () => {
  const html = render();
  assert.match(html, /value="" disabled="" selected=""/);
  assert.match(html, /value="kuaidi100"/);
  assert.match(html, /value="kdniao"/);
  assert.doesNotMatch(html, /logisticsProvider\.endpoint|logisticsProvider\.mapping/);
});
for (const kind of ['kuaidi100', 'kdniao']) {
  test(`${kind} only displays the matching account and key fields`, () => {
    const html = render({ ...types.defaultLogisticsProvider, provider_kind: kind, credential_configured: true });
    assert.match(html, new RegExp(`logisticsProvider.accounts.${kind}`));
    assert.match(html, new RegExp(`logisticsProvider.keys.${kind}`));
    assert.doesNotMatch(html, /logisticsProvider\.endpoint|logisticsProvider\.mapping|logisticsProvider\.method/);
    assert.match(html, /logisticsProvider.extraQuery/);
  });
}
test('legacy custom configuration remains editable with existing values', () => {
  const html = render({ provider_name: 'Legacy provider', tracking_param: 'mailNo', credential_configured: true });
  assert.match(html, /Legacy provider/);
  assert.match(html, /mailNo/);
  assert.match(html, /logisticsProvider.mapping/);
});
