import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { createRequire } from 'node:module';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const source = readFileSync(new URL('../src/utils/agentToolDetails.ts', import.meta.url), 'utf8');
const scope = { exports: {} };
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, scope);
const { agentToolDetails, schemaParameters } = scope.exports;

const moduleRequire = createRequire(import.meta.url);
const locale = JSON.parse(readFileSync(new URL('../src/i18n/locales/zh.json', import.meta.url), 'utf8'));
const modalScope = { exports: {}, require: name => {
  if (name === '@/utils/agentToolDetails') return scope.exports;
  if (name === 'react-i18next') return { useTranslation: () => ({
    t: key => key.split('.').reduce((value, field) => value?.[field], locale) || key,
  }) };
  return moduleRequire(name);
} };
const modalSource = readFileSync(new URL('../src/components/ui/ToolDetailModal.tsx', import.meta.url), 'utf8');
vm.runInNewContext(ts.transpileModule(modalSource, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
  jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
} }).outputText, modalScope);

test('detailed tools expose the real description and input schema, never private configuration', () => {
  const schema = { type: 'object', properties: { code: { type: 'string', description: '单号' } }, required: ['code'] };
  const result = agentToolDetails({ id: 'real-tool', name: 'express_service', title: '物流查询',
    description: '查询物流状态', status: 'active', input_schema: schema,
    config: { token: 'private-fixture-value' }, tool_server: { endpoint: 'http://private-fixture' } });
  assert.equal(result.name, '物流查询');
  assert.equal(result.description, '查询物流状态');
  assert.equal(result.input_schema, schema);
  assert.equal('config' in result, false);
  assert.equal('endpoint' in result, false);
});

test('legacy tool bindings show their real name without inventing tool capabilities', () => {
  const result = agentToolDetails({ id: 'binding', tool_name: 'provider:tool', enabled: true });
  assert.equal(result.name, 'provider:tool');
  assert.equal(result.description, undefined);
  assert.equal(result.input_schema, undefined);
});

test('schema parameters preserve actual required fields and handle missing schema safely', () => {
  const parameters = schemaParameters({ properties: { code: { type: 'string', description: '单号' },
    limit: { type: 'integer' } }, required: ['code'] });
  assert.equal(parameters.length, 2);
  assert.equal(parameters[0].required, true);
  assert.equal(parameters[1].required, false);
  assert.equal(parameters[1].type, 'integer');
  assert.equal(schemaParameters(undefined).length, 0);
  assert.equal(schemaParameters({ properties: ['not-a-schema'] }).length, 0);
});

test('malformed nested schema values do not crash rendering or fabricate string types', () => {
  const parameters = schemaParameters({ properties: { optional: true, nested: null,
    union: { type: ['string', 'null'], description: 123 } }, required: 'optional' });
  assert.equal(parameters.length, 3);
  assert.equal(parameters[0].type, undefined);
  assert.equal(parameters[0].required, false);
  assert.equal(parameters[2].type, 'string | null');
  assert.equal(parameters[2].description, undefined);
});

test('the connected detail component renders Chinese labels and actual schema safely', () => {
  const html = renderToStaticMarkup(React.createElement(modalScope.exports.default, {
    isOpen: true, onClose() {}, tool: { name: '物流查询', description: '<script>untrusted()</script>',
      input_schema: { properties: { tracking_number: { type: 'string', description: '快递单号' } },
        required: ['tracking_number'] } },
  }));
  assert.match(html, /<dialog/);
  assert.match(html, /工具详情/);
  assert.match(html, /快递单号/);
  assert.match(html, /必填/);
  assert.match(html, /aria-label="关闭"/);
  assert.doesNotMatch(html, /<script>/);
  assert.equal(renderToStaticMarkup(React.createElement(modalScope.exports.default, {
    isOpen: false, onClose() {}, tool: { name: 'hidden' },
  })), '');
});

test('the employee-card tool handler opens the selected detail instead of doing nothing', () => {
  const management = readFileSync(new URL('../src/components/ai/AgentManagement.tsx', import.meta.url), 'utf8');
  const handler = management.match(/const handleToolClick = \(tool: AgentToolResponse\): void => \{([\s\S]*?)\n  \};/);
  assert.ok(handler);
  let selected = null;
  const clicked = { id: 'actual-click', tool_name: 'provider:tool', enabled: true };
  new Function('tool', 'setSelectedTool', handler[1])(clicked, tool => { selected = tool; });
  assert.equal(selected, clicked);
  assert.match(management, /tool=\{selectedTool \? agentToolDetails\(selectedTool\) : null\}/);
  assert.match(management, /onClose=\{\(\) => setSelectedTool\(null\)\}/);
});
