import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const moduleRequire = createRequire(import.meta.url);
const t = (key, fallback) => typeof fallback === 'string' ? fallback : fallback?.defaultValue || key;
function load(relative) {
  const source = readFileSync(new URL(relative, import.meta.url), 'utf8');
  const scope = { exports: {}, require: name => {
    if (name === 'react-i18next') return { useTranslation: () => ({ t }) };
    if (name === '@/i18n') return { __esModule: true, default: { language: 'zh', t } };
    return moduleRequire(name);
  } };
  vm.runInNewContext(ts.transpileModule(source.replaceAll('import.meta', '({ env: { DEV: false } })'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText, scope);
  return scope.exports;
}
const { transformFileToKnowledgeFile } = load('../src/utils/knowledgeBaseTransforms.ts');
const { DocumentList } = load('../src/components/knowledge/DocumentList.tsx');
const file = status => transformFileToKnowledgeFile({ id: 'owned', original_filename: 'owned.md',
  file_size: 1, content_type: 'text/markdown', status, created_at: '2026-09-08T00:00:00Z',
  error_message: '<script>owned</script> 处理任务未能提交' });
const render = document => renderToStaticMarkup(React.createElement(DocumentList, {
  documents: [document], isLoading: false, onDownload() {}, onDelete() {}, onViewQA() {},
  searchTerm: '', fileTypeFilter: '',
}));

test('file failure reason survives API-to-view transformation', () => {
  assert.equal(file('failed').error_message, '<script>owned</script> 处理任务未能提交');
  assert.equal(file('failed').statusType, 'error');
});
test('document list shows escaped failure details', () => {
  const html = render(file('failed'));
  assert.match(html, /处理任务未能提交/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});
test('completed and processing files do not show stale error details', () => {
  for (const status of ['completed', 'processing']) {
    assert.doesNotMatch(render(file(status)), /处理任务未能提交/);
  }
});
