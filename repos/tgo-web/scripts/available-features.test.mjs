import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const requireModule = createRequire(import.meta.url);
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const knowledgeSource = read('../src/components/knowledge/CreateKnowledgeBaseModal.tsx');
const scope = { exports: {}, require: name => {
  if (name === 'react-i18next') return { useTranslation: () => ({ t: (key, fallback) => fallback ?? key }) };
  if (name === '@/components/ui/TagInput') return { TagInput: () => null };
  if (name === '@/services/knowledgeBaseApi') return { default: {} };
  return requireModule(name);
} };
vm.runInNewContext(ts.transpileModule(knowledgeSource, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
  jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
} }).outputText, scope);

test('knowledge creation only advertises implemented import types', () => {
  const html = renderToStaticMarkup(React.createElement(scope.exports.CreateKnowledgeBaseModal, {
    isOpen: true, onClose() {}, onSubmit: async () => {},
  }));
  for (const label of ['文件', '网站', '问答对']) assert.ok(html.includes(label));
  assert.doesNotMatch(html, /即将推出|天猫|淘宝|京东/);
});

test('obsolete simulated creator is removed, while both real tool creators remain wired', () => {
  assert.equal(existsSync(new URL('../src/components/ai/CreateCustomToolModal.tsx', import.meta.url)), false);
  const tools = read('../src/components/ai/Tools.tsx');
  for (const name of ['AddToolModal', 'AddHTTPToolModal']) {
    assert.ok(tools.includes(`<${name}`));
    assert.match(read(`../src/components/ai/${name}.tsx`), /await ProjectToolsApiService\.createAiTool\(requestData\)/);
  }
});

test('frontend no longer exports unsupported remote registration mutations', () => {
  const service = read('../src/services/remoteAgentsApi.ts');
  assert.doesNotMatch(service, /registerRemoteAgent|unregisterRemoteAgent|RemoteAgentRegisterRequest/);
  for (const name of ['listRemoteAgents', 'getRemoteAgent', 'getRemoteAgentConfig', 'testRemoteAgent']) {
    assert.ok(service.includes(`function ${name}(`));
  }
});
