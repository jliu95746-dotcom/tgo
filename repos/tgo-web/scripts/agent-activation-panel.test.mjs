import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function compile(path, dependency) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
  } }).outputText, { exports, Date, require: dependency });
  return exports;
}
const strings = compile('../src/i18n/businessScenarios.ts', () => { throw new Error('Unexpected translation dependency'); }).businessScenariosZh;
const t = (key, values = {}) => {
  let value = key.startsWith('businessScenarios.') ? key.slice(18).split('.').reduce((current, part) => current?.[part], strings) : key === 'common.cancel' ? '取消' : undefined;
  assert.equal(typeof value, 'string', `Missing translation ${key}`);
  for (const [name, item] of Object.entries(values)) value = value.replaceAll(`{{${name}}}`, String(item));
  return value;
};
function render({ passed = false, enabled = false, expired = false, pending = false, admin = true, allowActivation = true, reviewPending = false, reviewSessionPending = false } = {}) {
  const now = Date.now();
  const entry = { policiesReady: true, readinessReady: true, loading: false, writing: false, enabling: false,
    pending: pending ? { kind: 'enable' } : null, error: pending ? 'unknown' : null,
    policies: { policies: [{ template_id: 'knowledge', channel: 'web', enabled: true }] },
    readiness: { checked_at: new Date(now).toISOString(), enabled, can_enable: passed,
      blockers: passed ? [] : [{ code: 'tests_not_current', channel: 'web', message: '缺少当前资料的完整试聊与评审' }],
      channels: [{ template_id: 'knowledge', channel: 'web', status: passed ? 'passed' : 'needs_retest', valid_until: new Date(now + (expired ? -1000 : 3600000)).toISOString() }] } };
  const state = { entries: { 'agent-a': entry }, templatesReady: true, templatesLoading: false,
    templates: [{ id: 'knowledge', version: 7, available: true, title: '商品咨询客服' }], loadTemplates() {} };
  const usable = row => row.readinessReady && row.readiness.can_enable && !row.pending && row.readiness.channels.every(channel => Date.parse(channel.valid_until) > now);
  const cache = new Map();
  const dependency = name => {
    if (name === 'react' || name === 'react/jsx-runtime' || name === 'lucide-react') return require(name);
    if (name === 'react-i18next') return { useTranslation: () => ({ t }) };
    if (name === '@/hooks/useAgentActivationEntry') return { useAgentActivationEntry: () => ({ entry, now, scope: admin ? 'project-a:staff-a' : null, load() {} }) };
    if (name === '@/stores/agentActivationStore') return { useAgentActivationStore: () => state, activationUsable: usable };
    if (name === '@/stores/agentTrialReviewStore') return { useAgentTrialReviewStore: selector => selector({ identity: 'project-a:staff-a', agentId: 'agent-a', submitting: false, pending: reviewPending ? {} : null,
      sessions: reviewSessionPending ? { 'agent-a:report-old': { pending: {}, unknown: true } } : {} }) };
    if (name === '@/stores/aiStore') return { useAIStore: selector => selector({ agents: [] }) };
    if (name === '@/stores/authStore') return { useAuthStore: { getState: () => ({ user: { id: 'staff-a', project_id: 'project-a', role: admin ? 'admin' : 'user' } }) } };
    if (name === '@/services/aiAgentsApi') return { AIAgentsApiService: {} };
    if (name === './AgentActivationPolicyEditor') {
      if (!cache.has(name)) cache.set(name, compile('../src/components/business/AgentActivationPolicyEditor.tsx', dependency));
      return { ...cache.get(name), __esModule: true };
    }
    throw new Error(`Unexpected component dependency ${name}`);
  };
  const Component = compile('../src/components/business/AgentActivationPanel.tsx', dependency).default;
  return renderToStaticMarkup(React.createElement(Component, { agentId: 'agent-a', name: '澜月客服', allowActivation }));
}
test('missing proof renders actual blockers and a disabled enable action', () => {
  const html = render();
  assert.match(html, /缺少当前资料的完整试聊与评审/);
  assert.match(html, /待补齐或重测/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>启用员工<\/button>/);
});
test('current complete proof offers confirmation entry without performing activation', () => {
  const html = render({ passed: true });
  assert.match(html, /核验通过/);
  assert.match(html, /<button[^>]*>启用员工<\/button>/);
  assert.doesNotMatch(html, /<button[^>]*disabled=""[^>]*>启用员工<\/button>/);
});
test('elapsed case expiry removes ready state and displays retesting', () => {
  const html = render({ passed: true, expired: true });
  assert.doesNotMatch(html, /核验通过/); assert.match(html, /待重测/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>启用员工<\/button>/);
});
test('a serving employee has an explicit pause action and locked policy editing', () => {
  const html = render({ enabled: true });
  assert.match(html, /接待中/); assert.match(html, /停用后修改业务配置/);
  assert.match(html, /<fieldset[^>]*disabled=""/); assert.doesNotMatch(html, />启用员工<\/button>/);
});
test('unknown activation results remain pending with no automatic retry action', () => {
  const html = render({ passed: true, pending: true });
  assert.match(html, /请求结果尚未确认/); assert.doesNotMatch(html, /按原选择重试保存/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>启用员工<\/button>/);
});
test('an unresolved knowledge review prevents reusing an older green activation proof', () => {
  const html = render({ passed: true, reviewPending: true });
  assert.match(html, /有尚未确认结果的知识评审/); assert.doesNotMatch(html, /核验通过/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>启用员工<\/button>/);
});
test('closing an older report leaves its unresolved review blocking activation', () => {
  const html = render({ passed: true, reviewSessionPending: true });
  assert.match(html, /有尚未确认结果的知识评审/);
  assert.match(html, /<button[^>]*disabled=""[^>]*>启用员工<\/button>/);
});
test('unsaved editor and ordinary staff cannot expose an enable action', () => {
  assert.doesNotMatch(render({ passed: true, allowActivation: false }), />启用员工<\/button>/);
  assert.equal(render({ admin: false }), '');
});
