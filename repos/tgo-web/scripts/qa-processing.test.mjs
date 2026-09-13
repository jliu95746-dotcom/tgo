import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const moduleRequire = createRequire(import.meta.url);
const locale = JSON.parse(readFileSync(new URL('../src/i18n/locales/zh.json', import.meta.url), 'utf8'));
const source = readFileSync(new URL('../src/components/knowledge/QAPairProcessingNotice.tsx', import.meta.url), 'utf8');
const scope = { exports: {}, require: name => name === 'react-i18next' ? {
  useTranslation: () => ({ t: key => key.split('.').reduce((value, field) => value?.[field], locale) || key }),
} : moduleRequire(name) };
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
  jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
} }).outputText, scope);
const render = props => renderToStaticMarkup(React.createElement(scope.exports.default, props));

test('failed pair shows escaped failure reason and explicit retry instructions', () => {
  const html = render({ status: 'failed', errorMessage: '<script>unsafe()</script> No active embedding configuration' });
  assert.match(html, /No active embedding configuration/);
  assert.match(html, /保存即可重试/);
  assert.doesNotMatch(html, /<script>/);
});

test('legacy failed pair has a useful fallback; successful pair hides stale errors', () => {
  assert.match(render({ status: 'failed', errorMessage: null }), /未返回具体原因/);
  assert.equal(render({ status: 'processed', errorMessage: 'stale failure' }), '');
});

test('actual QA detail page wires the failure component to each pair', () => {
  const page = readFileSync(new URL('../src/pages/QAKnowledgeBaseDetail.tsx', import.meta.url), 'utf8');
  assert.match(page, /<QAPairProcessingNotice status=\{pair.status\} errorMessage=\{pair.error_message\}/);
});

function pollingHarness(request, overrides = {}) {
  const page = readFileSync(new URL('../src/pages/QAKnowledgeBaseDetail.tsx', import.meta.url), 'utf8');
  const body = page.match(/\/\/ Poll only[\s\S]*?useEffect\(\(\) => \{([\s\S]*?)\n  \}, \[id, qaPairs,/)[1];
  const timers = [];
  const updates = [];
  const code = ts.transpileModule(`exports.start = () => {${body}}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const runtime = { exports: {}, id: 'collection-a', qaPairs: [{ status: 'pending' }],
    pairsRequestVersion: { current: 0 }, isLoadingPairs: false, pairsError: false,
    limit: 10, offset: 0, categoryFilter: '', statusFilter: '',
    KnowledgeBaseApiService: { getQAPairs: request },
    setQaPairs: value => updates.push(value), setTotal() {},
    setTimeout: callback => (timers.push(callback), timers.length), clearTimeout() {},
    ...overrides,
  };
  vm.runInNewContext(code, runtime);
  return { cleanup: runtime.exports.start(), timers, updates, runtime };
}

test('processing refresh keeps terminal status and ignores a late result after navigation', async () => {
  const first = pollingHarness(async () => ({ data: [{ status: 'processed' }], total: 1 }));
  await first.timers[0]();
  assert.equal(first.updates[0][0].status, 'processed');
  const late = pollingHarness(async () => ({ data: [{ status: 'failed' }], total: 1 }));
  late.cleanup();
  await late.timers[0]();
  assert.equal(late.updates.length, 0);
});

function listLoadingHarness(request) {
  const page = readFileSync(new URL('../src/pages/QAKnowledgeBaseDetail.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile('page.tsx', page, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let loader;
  const visit = node => {
    if (ts.isVariableDeclaration(node) && node.name.getText(tree) === 'loadQAPairs') {
      loader = node.initializer.arguments[0].getText(tree);
    }
    ts.forEachChild(node, visit);
  };
  visit(tree);
  assert.ok(loader);
  const state = { loading: false, error: false, pairs: [], total: 0 };
  const runtime = { exports: {}, id: 'collection-a', limit: 10, offset: 0,
    categoryFilter: '', statusFilter: '', pairsRequestVersion: { current: 0 },
    KnowledgeBaseApiService: { getQAPairs: request },
    setIsLoadingPairs: value => { state.loading = value; },
    setPairsError: value => { state.error = value; },
    setQaPairs: value => { state.pairs = value; },
    setTotal: value => { state.total = value; },
    showToast() {}, t: key => key, console: { error() {} },
  };
  vm.runInNewContext(ts.transpileModule(`exports.run = ${loader}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, runtime);
  return { run: runtime.exports.run, state, version: runtime.pairsRequestVersion };
}

test('list loading failure stays visible until retry succeeds', async () => {
  let fail = true;
  const view = listLoadingHarness(async () => {
    if (fail) throw new Error('HTTP 503');
    return { data: [{ id: 'recovered' }], total: 1 };
  });
  await view.run();
  assert.equal(view.state.error, true);
  assert.equal(view.state.loading, false);
  fail = false;
  await view.run();
  assert.equal(view.state.error, false);
  assert.equal(view.state.pairs[0].id, 'recovered');
});

test('late list success cannot overwrite a newer response', async () => {
  let finishOld;
  let count = 0;
  const view = listLoadingHarness(() => ++count === 1
    ? new Promise(resolve => { finishOld = resolve; })
    : Promise.resolve({ data: [{ id: 'new' }], total: 1 }));
  const old = view.run();
  await view.run();
  finishOld({ data: [{ id: 'old' }], total: 99 });
  await old;
  assert.equal(view.state.pairs[0].id, 'new');
  assert.equal(view.state.total, 1);
});

test('late list failure cannot end the newer loading state or show a false error', async () => {
  let failOld, finishNew;
  let count = 0;
  const view = listLoadingHarness(() => ++count === 1
    ? new Promise((_resolve, reject) => { failOld = reject; })
    : new Promise(resolve => { finishNew = resolve; }));
  const old = view.run();
  const current = view.run();
  failOld(new Error('old request failed'));
  await old;
  assert.equal(view.state.loading, true);
  assert.equal(view.state.error, false);
  finishNew({ data: [], total: 0 });
  await current;
  assert.equal(view.state.loading, false);
});

test('a list result invalidated by navigation is ignored', async () => {
  let finish;
  const view = listLoadingHarness(() => new Promise(resolve => { finish = resolve; }));
  const pending = view.run();
  view.version.current += 1;
  finish({ data: [{ id: 'old-collection' }], total: 1 });
  await pending;
  assert.equal(view.state.pairs.length, 0);
});

test('list error precedes empty-state rendering and offers a real retry', () => {
  const page = readFileSync(new URL('../src/pages/QAKnowledgeBaseDetail.tsx', import.meta.url), 'utf8');
  assert.match(page, /pairsError \? \([\s\S]*?role="alert"[\s\S]*?onClick=\{\(\) => void loadQAPairs\(\)\}/);
  assert.ok(page.indexOf('pairsError ? (') < page.indexOf('filteredPairs.length === 0 ?'));
});

test('actual list markup distinguishes load failure from a successful empty response', () => {
  const page = readFileSync(new URL('../src/pages/QAKnowledgeBaseDetail.tsx', import.meta.url), 'utf8');
  const tree = ts.createSourceFile('page.tsx', page, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let list;
  const visit = node => {
    if (ts.isConditionalExpression(node) && node.condition.getText(tree) === 'isLoadingPairs') {
      list = node.getText(tree);
    }
    ts.forEachChild(node, visit);
  };
  visit(tree);
  assert.ok(list, 'actual page list conditional exists');
  for (const pairsError of [false, true]) {
    const runtime = { exports: {}, require: moduleRequire, isLoadingPairs: false,
      pairsError, filteredPairs: [], loadQAPairs() {},
      MessageSquare: () => React.createElement('svg'),
      t: key => key.split('.').reduce((value, field) => value?.[field], locale) || key,
    };
    vm.runInNewContext(ts.transpileModule(`exports.render = () => (${list});`, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
        jsx: ts.JsxEmit.ReactJSX }, fileName: 'list.tsx',
    }).outputText, runtime);
    const html = renderToStaticMarkup(runtime.exports.render());
    if (pairsError) {
      assert.match(html, /role="alert"/);
      assert.match(html, /加载问答对列表失败/);
      assert.match(html, /重试/);
      assert.doesNotMatch(html, /暂无问答对/);
    } else {
      assert.match(html, /暂无问答对/);
      assert.doesNotMatch(html, /role="alert"/);
    }
  }
});

test('temporary polling failure retries without clearing the last visible list', async () => {
  const result = pollingHarness(async () => { throw new Error('HTTP 503'); });
  await result.timers[0]();
  assert.equal(result.updates.length, 0);
  assert.equal(result.timers.length, 2);
});

test('polling pauses during foreground loading and after a visible list error', () => {
  for (const state of [{ isLoadingPairs: true }, { pairsError: true }]) {
    const poll = pollingHarness(async () => { throw new Error('must not request'); }, state);
    assert.equal(poll.timers.length, 0);
  }
});

test('an in-flight poll cannot overwrite a later manual reload', async () => {
  let finish;
  const poll = pollingHarness(() => new Promise(resolve => { finish = resolve; }));
  const pending = poll.timers[0]();
  poll.runtime.pairsRequestVersion.current += 1;
  finish({ data: [{ id: 'outdated-poll' }], total: 1 });
  await pending;
  assert.equal(poll.updates.length, 0);
});

test('query cleanup invalidates pending requests and retry label is translated', () => {
  const page = readFileSync(new URL('../src/pages/QAKnowledgeBaseDetail.tsx', import.meta.url), 'utf8');
  assert.match(page, /useEffect\(\(\) => \(\) => \{\s*pairsRequestVersion.current \+= 1;/);
  assert.equal(locale.common.retry, '重试');
});

test('import queue failure is not shown as success and keeps pasted data for correction', async () => {
  const page = readFileSync(new URL('../src/pages/QAKnowledgeBaseDetail.tsx', import.meta.url), 'utf8');
  const body = page.match(/const handleImport = async \(\) => \{([\s\S]*?)\n  \};/)
    ?? page.match(/const handleImportQAPairs = async \(\) => \{([\s\S]*?)\n  \};/);
  assert.ok(body, 'actual import handler exists');
  for (const success of [false, true]) {
    const toasts = [], clears = [];
    const runtime = { exports: {}, id: 'collection-a', importData: '[test]', importFormat: 'json',
      KnowledgeBaseApiService: { importQAPairs: async () => ({ success, created_count: 1, skipped_count: 0, failed_count: success ? 0 : 1 }) },
      setIsImporting() {}, showToast: (...args) => toasts.push(args), t: key => key,
      setIsImportDialogOpen: value => clears.push(value), setImportData: value => clears.push(value),
      loadQAPairs() {}, loadCategories() {}, console,
    };
    vm.runInNewContext(ts.transpileModule(`exports.run = async () => {${body[1]}}`, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText, runtime);
    await runtime.exports.run();
    assert.equal(toasts[0][0], success ? 'success' : 'error');
    assert.equal(clears.length, success ? 2 : 0);
  }
});
