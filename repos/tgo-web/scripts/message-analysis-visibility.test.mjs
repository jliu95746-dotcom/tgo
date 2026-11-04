import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import i18next from 'i18next';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const { I18nextProvider } = require('react-i18next');
const source = readFileSync(new URL('../src/components/chat/MessageAnalysisInsights.tsx', import.meta.url), 'utf8');
const scope = { exports: {}, require: name => name === '@/types/messageAnalysis'
  ? { MESSAGE_ANALYSIS_UNAVAILABLE: { status: 'unavailable', reason: 'analysis_not_requested' } }
  : require(name) };
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React,
  esModuleInterop: true,
} }).outputText, scope);
const i18n = i18next.createInstance();
await i18n.init({ lng: 'zh', resources: { zh: { translation: JSON.parse(
  readFileSync(new URL('../src/i18n/locales/zh.json', import.meta.url), 'utf8'),
) } } });
const render = state => renderToStaticMarkup(React.createElement(I18nextProvider, { i18n },
  React.createElement(scope.exports.default, { state })));

for (const status of [undefined, 'not_found', 'unavailable', 'loading', 'error']) {
  test(`no empty analysis card for ${status ?? 'missing'} results`, () => {
    assert.equal(render(status ? { status } : undefined), '');
  });
}
test('an available response with no analysis content stays hidden', () => {
  assert.equal(render({ status: 'available', analysis: { intent: null, media: null } }), '');
});
test('real intent results remain available in a collapsed card', () => {
  const html = render({ status: 'available', analysis: { media: null, intent: {
    intent: 'product_inquiry', confidence: 0.92, risk_level: 'low', recommended_route: 'auto_reply',
    routing_reason: 'high_confidence_faq', classification_source: 'model', need_human: false,
  } } });
  assert.match(html, /智能分析/);
  assert.match(html, /92%/);
  assert.doesNotMatch(html, /暂无结果|<details[^>]*\sopen/);
});
test('an actual failed media analysis retains its useful failure details', () => {
  const html = render({ status: 'available', analysis: { intent: null, media: {
    status: 'failed', normalized_text: null, transcript: null, ocr_text: null, vision_summary: null,
    normalized_text_is_untrusted: false, sensitive_data_categories: [],
    stages: [{ capability: 'vlm', status: 'failed', error: { category: 'timeout' } }],
  } } });
  assert.match(html, /多模态处理/);
  assert.match(html, /超时/);
});
