import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const content = '---\ntitle: MetadataOnly\nversion: 1\n---\n# 企业资料\n企业服务咨询。\n' + '完整资料内容。'.repeat(2600) + 'EXCLUSIVE_TAIL_TERM';
const item = { id: 'kb-a', title: '业务知识库', content, author: '业务管理员', category: 'file', fileCount: 3, tags: [] };
const noop = () => {};
function compile(file, dependency) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(file, import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText, { exports, require: dependency });
  return exports;
}
function render(kind = 'picker', query = '', value = item) {
  const dependencies = {
    'react-i18next': { useTranslation: () => ({ t: (key, fallback) => typeof fallback === 'string' ? fallback : fallback?.defaultValue ?? key }) },
    'react-router-dom': { Link: ({ to, children, ...props }) => React.createElement('a', { ...props, href: to }, children) },
    '@/hooks/useDialogFocus': { useDialogFocus: () => ({ current: null }) },
    '@/hooks/useDebouncedValue': { useDebouncedValue: () => query },
    '@/stores': { useKnowledgeStore: () => ({ knowledgeBases: [value], isLoading: false, error: null, fetchKnowledgeBases: async () => {}, refreshKnowledgeBases: async () => {} }) },
    '@/hooks/useToast': { useToast: () => ({ showToast: noop }) },
    '@/components/knowledge/IconPicker': { getIconComponent: () => () => React.createElement('svg'), getIconColor: () => 'text-blue-500' },
  };
  const module = compile(kind === 'picker' ? '../src/components/ai/KnowledgeBaseSelectionModal.tsx' : '../src/components/ui/AgentKnowledgeBaseRow.tsx', name => {
    if (name in dependencies) return dependencies[name];
    if (name === '@/utils/knowledgePreview') return compile('../src/utils/knowledgePreview.ts', require);
    return require(name);
  });
  return renderToStaticMarkup(React.createElement(module.default, kind === 'picker'
    ? { isOpen: true, onClose: noop, selectedKnowledgeBases: [], onConfirm: noop }
    : { kb: value, onRemove: noop }));
}

for (const kind of ['picker', 'selected']) {
  test(`${kind} card renders a bounded business preview rather than the full source`, () => {
    const html = render(kind);
    assert.ok(html.includes('企业服务咨询。'));
    assert.equal(html.includes('EXCLUSIVE_TAIL_TERM'), false, 'Full knowledge document is still rendered');
    assert.equal(html.includes('title: MetadataOnly'), false, 'Frontmatter should not replace the business preview');
    assert.ok(html.includes('3 文件'));
  });
}
test('search still matches text outside the preview', () => {
  const html = render('picker', 'EXCLUSIVE_TAIL_TERM');
  assert.ok(html.includes('aria-label="业务知识库"'));
  assert.equal(html.includes('EXCLUSIVE_TAIL_TERM'), false);
});
test('a nonmatching full-text query does not display the knowledge checkbox', () => {
  assert.equal(render('picker', 'no-such-phrase').includes('aria-label="业务知识库"'), false);
});
test('preview text is escaped by React rather than interpreted as HTML', () => {
  const html = render('picker', '', { ...item, content: '<script>preview()</script>' });
  assert.ok(html.includes('&lt;script&gt;preview()&lt;/script&gt;'));
  assert.equal(html.includes('<script>'), false);
});

const { knowledgeBasePreview } = compile('../src/utils/knowledgePreview.ts', require);
test('short descriptions preserve their business text and collapse whitespace', () => {
  assert.equal(knowledgeBasePreview('  企业服务\n  咨询资料。 '), '企业服务 咨询资料。');
  assert.equal(knowledgeBasePreview(' \n\t '), '');
});
test('complete frontmatter with BOM and CRLF is omitted from the preview', () => {
  assert.equal(knowledgeBasePreview('\uFEFF---\r\ntitle: 标题\r\nversion: 1\r\n---\r\n# 企业资料\r\n> 服务范围'), '企业资料 服务范围');
});
test('incomplete metadata and ordinary separated prose are not silently discarded', () => {
  assert.ok(knowledgeBasePreview('---\ntitle: incomplete\n企业资料').includes('企业资料'));
  assert.ok(knowledgeBasePreview('---\n普通业务说明\n---\n下一段').includes('普通业务说明'));
});
test('the boundary has no ellipsis until source content exceeds the preview limit', () => {
  assert.equal(knowledgeBasePreview('中'.repeat(160)), '中'.repeat(160));
  assert.equal(knowledgeBasePreview('中'.repeat(161)), '中'.repeat(160) + '…');
});
test('truncation counts Unicode characters without splitting emoji surrogate pairs', () => {
  const value = knowledgeBasePreview('😀'.repeat(161));
  assert.equal(value, '😀'.repeat(160) + '…');
  assert.equal(Array.from(value).length, 161);
});
