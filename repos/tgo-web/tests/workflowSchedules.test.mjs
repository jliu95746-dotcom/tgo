import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseTimerForm, timerExpression, readScheduleCommand } from '../src/utils/workflowScheduleForm.ts';
import { createScheduleActions, newScheduleState } from '../src/stores/workflowScheduleActions.ts';

const owner = 'project:actor:admin';
const summary = { id: 'plan', project_id: 'project', workflow_id: 'flow', revision: 1, enabled: false,
  release_id: 'release', release_digest: 'a'.repeat(64) };
const context = { release_id: 'release', definition_digest: 'a'.repeat(64), version: 1,
  cron_expression: '0 9 * * *', timezone: 'UTC' };
const state = { schedule: null, current_release: context, execution_available: true, binding_current: false, preview_at: [],
  runtime: { ready: true, status: 'ready' } };
const delay = () => new Promise(resolve => setTimeout(resolve, 0));
function harness(apiOverrides = {}) {
  let current = owner, value = newScheduleState(), saved = null, calls = [];
  const api = { getState: async () => state, getHistory: async () => ({ items: [], total: 0, offset: 0, limit: 10, has_more: false }),
    preview: async (_, body) => ({ ...body, computed_at: '2026-10-24T00:00:00Z', preview_at: [] }),
    save: async (_, body) => { calls.push(body); throw new Error('network'); },
    getCommand: async () => { throw new Error('pending'); }, ...apiOverrides };
  const storage = { getItem: () => saved, setItem: (_, body) => { saved = body; }, removeItem: () => { saved = null; } };
  const actions = createScheduleActions(change => { value = { ...value, ...change }; }, () => value, {
    api, currentOwner: () => current, createId: () => '12345678-1234-1234-1234-123456789abc', translate: key => key,
    storage,
  });
  return { actions, api, storage, value: () => value, calls, saved: () => saved,
    switchOwner: () => { current = 'other:actor:admin'; }, current: () => current };
}

test('预设频率转换保留小时分钟，复杂表达式保持自定义', () => {
  assert.deepEqual(parseTimerForm('30 8 * * 1-5'), { mode: 'weekdays', time: '08:30', weekday: '1' });
  assert.equal(timerExpression('weekly', '18:45', '0'), '45 18 * * 0');
  assert.equal(parseTimerForm('*/7 8-20 * * *').mode, 'custom');
  assert.equal(timerExpression('daily', '24:00', '1'), null);
});

test('本机请求记录拒绝附加身份和错误布尔类型', () => {
  const request = { request_id: '12345678-1234-1234-1234-123456789abc', expected_revision: 0,
    expected_release_id: 'release', expected_release_digest: 'a'.repeat(64), enabled: false };
  assert.ok(readScheduleCommand(JSON.stringify({ operation: 'save', request })));
  assert.equal(readScheduleCommand(JSON.stringify({ operation: 'save', request: { ...request, actor_staff_id: 'foreign' } })), null);
  assert.equal(readScheduleCommand(JSON.stringify({ operation: 'save', request: { ...request, enabled: 'true' } })), null);
});

test('未知结果保留原请求，重试沿用编号且不能同时发新命令', async () => {
  const h = harness(); h.actions.activate(owner, 'flow'); await delay();
  await h.actions.save(false);
  const pending = h.value().pending;
  assert.ok(pending && h.saved());
  await h.actions.save(true); assert.equal(h.calls.length, 1);
  await h.actions.retry(); assert.deepEqual(h.calls[0], h.calls[1]);
});

test('旧回执只解除待确认状态，再读取当前计划，不覆盖成旧修订', async () => {
  let latest = state;
  const h = harness({ getState: async () => latest,
    getCommand: async (_, id) => ({ request_id: id, operation: 'save', schedule: summary, replayed: true }) });
  h.actions.activate(owner, 'flow'); await delay(); await h.actions.save(false);
  latest = { ...state, schedule: { ...summary, revision: 5, enabled: true } };
  await h.actions.recover();
  assert.equal(h.value().pending, null); assert.equal(h.saved(), null);
  assert.equal(h.value().state.schedule.revision, 5);
});

test('账号切换后迟到结果不进入新账号状态', async () => {
  let finish;
  const h = harness({ getState: () => new Promise(resolve => { finish = resolve; }) });
  h.actions.activate(owner, 'flow'); h.switchOwner(); finish(state); await delay();
  assert.equal(h.value().state, null);
});

test('旧预览响应不能覆盖新规则，关闭组件也丢弃结果', async () => {
  const completions = [];
  const h = harness({ preview: (_, body) => new Promise(resolve => completions.push(() => resolve({ ...body, preview_at: [] }))) });
  const stop = h.actions.activate(owner, 'flow'); await delay();
  const first = h.actions.requestPreview({ cron_expression: '0 8 * * *', timezone: 'UTC' });
  const second = h.actions.requestPreview({ cron_expression: '0 9 * * *', timezone: 'Asia/Shanghai' });
  completions[1](); await second; completions[0](); await first;
  assert.equal(h.value().preview.timezone, 'Asia/Shanghai');
  stop(); assert.equal(h.value().preview, null);
});

test('停用不依赖当前发布可用，仍使用已知计划修订', async () => {
  let received;
  const h = harness({ getState: async () => ({ ...state, schedule: { ...summary, enabled: true }, current_release: null, execution_available: false }),
    disable: async (_, request) => { received = request; throw new Error('unknown'); } });
  h.actions.activate(owner, 'flow'); await delay(); await h.actions.disable();
  assert.equal(received.expected_revision, 1);
  assert.equal(h.value().pending.operation, 'disable');
});

test('关闭再打开配置仍保留原请求，不自动再发写操作', async () => {
  const h = harness(); const stop = h.actions.activate(owner, 'flow'); await delay();
  await h.actions.save(false); const original = h.value().pending;
  stop(); h.actions.activate(owner, 'flow'); await delay();
  assert.deepEqual(h.value().pending, original); assert.equal(h.calls.length, 1);
});

test('浏览器无法保存原请求时禁止写操作', async () => {
  const h = harness(); h.actions.activate(owner, 'flow'); await delay();
  h.storage.setItem = () => { throw new Error('quota'); };
  await h.actions.save(false);
  assert.equal(h.calls.length, 0); assert.equal(h.value().error, 'workflowSchedule.storageFailed');
});

test('串到其他工作流的回执不能清掉待确认请求', async () => {
  const h = harness({ getCommand: async (_, id) => ({ request_id: id, operation: 'save', replayed: true,
    schedule: { ...summary, workflow_id: 'foreign' } }) });
  h.actions.activate(owner, 'flow'); await delay(); await h.actions.save(false);
  await h.actions.recover(); assert.ok(h.value().pending); assert.ok(h.saved());
  assert.equal(h.value().error, 'workflowSchedule.invalidResponse');
});

test('配置已开放但未取得服务探测时不能发送新启用请求', async () => {
  for (const runtime of [null, { ready: false, status: 'worker_waiting' }]) {
    const h = harness({ getState: async () => ({ ...state, runtime }) });
    h.actions.activate(owner, 'flow'); await delay(); await h.actions.save(true);
    assert.equal(h.calls.length, 0); assert.equal(h.value().pending, null);
    await h.actions.save(false); assert.equal(h.calls.length, 1);
  }
});
