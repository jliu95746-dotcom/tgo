import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

function render(entry, extra = {}) {
  const exports = {};
  const scope = { exports, require: name => {
    if (name === 'react') return React;
    if (name === 'react/jsx-runtime') return { jsx: React.createElement, jsxs: React.createElement };
    if (name === 'react-i18next') return { useTranslation: () => ({ t: (key, values) => `${key}${values?.count ?? ''}` }) };
    if (name === 'react-router-dom') return { useNavigate: () => () => {} };
    if (name === '@/utils/scenarioPreparation') return { preparedResourceKinds: (template, report) => template.required_resources.filter(kind => !report.issues.some(issue => issue.kind === kind)) };
    throw new Error(`Unexpected import: ${name}`);
  } };
  const source = ts.transpileModule(readFileSync(new URL('../src/components/business/ScenarioPreparationStatus.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React },
  }).outputText;
  vm.runInNewContext(source, { ...scope, React });
  return renderToStaticMarkup(React.createElement(exports.default, { entry, onRetry() {}, ...extra }));
}

const template = { required_resources: ['model', 'knowledge'] };
test('prepared basics remain explicitly untested and details start collapsed', () => {
  const html = render({ template, status: 'checked', report: { configuration_ready: true, issues: [], can_enable: false } });
  assert.match(html, /businessScenarios.cardPreparation.prepared/);
  assert.match(html, /businessScenarios.cardPreparation.notTested/);
  assert.match(html, /<details/);
  assert.doesNotMatch(html, /<details[^>]+open/);
});
test('missing knowledge has an actionable button while configured model is collapsed', () => {
  const html = render({ template, status: 'checked', report: { configuration_ready: false, issues: [{ kind: 'knowledge', code: 'missing', message: '请选择当前资料', action: 'choose_resource', route: '/knowledge' }] } });
  assert.match(html, /请选择当前资料/);
  assert.match(html, /businessScenarios.readiness.actions.choose_resource/);
  assert.match(html, /businessScenarios.cardPreparation.readyResources1/);
});
test('owner failure is a retry state rather than an empty-resource state', () => {
  const html = render({ template, status: 'failed', report: null });
  assert.match(html, /role="alert"/);
  assert.match(html, /businessScenarios.cardPreparation.failed/);
  assert.match(html, /businessScenarios.cardPreparation.retry/);
  assert.doesNotMatch(html, /businessScenarios.cardPreparation.prepared/);
});
test('unavailable screen templates stay planned without a readiness claim', () => {
  const html = render({ template, status: 'planned', report: null });
  assert.match(html, /businessScenarios.cardPreparation.planned/);
  assert.doesNotMatch(html, /businessScenarios.cardPreparation.prepared/);
});
