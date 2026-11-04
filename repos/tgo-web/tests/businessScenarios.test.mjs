import assert from 'node:assert/strict';
import { test } from 'node:test';

import { buildScenarioWorkflow } from '../src/utils/businessScenarios.ts';

const agentId = 'ce128a46-8d8c-4529-931f-b60fc0d3bb14';
const approverStaffId = 'b4ab77a2-d303-44bd-8d83-61554d41910a';

test('售后政策模板仍只解释政策', () => {
  const draft = buildScenarioWorkflow('aftersales', { name: '售后政策', agentId });
  assert.deepEqual(draft.nodes.map(node => node.type), ['input', 'agent', 'answer']);
});

test('售后核对模板将内部待办放在明确的人工审批之后', () => {
  const draft = buildScenarioWorkflow('aftersales', {
    name: '售后人工核对',
    review: { approverStaffId, caseCategory: 'quality_issue' },
  });
  assert.deepEqual(draft.nodes.map(node => node.type), ['input', 'approval', 'answer']);
  const approval = draft.nodes[1];
  assert.equal(approval.data.approver_staff_id, approverStaffId);
  assert.equal(approval.data.after_approval_action, 'create_aftersales_review');
  assert.equal(approval.data.case_category, 'quality_issue');
  assert.equal(approval.data.summary, '请核对客户售后诉求：{{input.question}}');
  assert.equal(draft.edges[0].target, approval.id);
  assert.equal(draft.edges[1].source, approval.id);
  assert.ok(!draft.nodes.some(node => ['agent', 'api', 'tool'].includes(node.type)));
});

test('售后核对模板拒绝不完整的审批配置', () => {
  assert.throws(() => buildScenarioWorkflow('aftersales', {
    name: '售后人工核对', agentId,
    review: { approverStaffId: '', caseCategory: 'return_request' },
  }), /审批员工/);
  assert.throws(() => buildScenarioWorkflow('aftersales', {
    name: '售后人工核对', agentId,
    review: { approverStaffId, caseCategory: 'invalid' },
  }), /售后类别/);
});
