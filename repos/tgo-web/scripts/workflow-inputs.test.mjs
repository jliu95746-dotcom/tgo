import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const context = { exports: {} };
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/workflowInputs.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, context);
const { workflowInputIssues, parseWorkflowInput } = context.exports;

test('required strings reject whitespace while false and zero are actual values', () => {
  const declarations = [{ name: 'order', type: 'string', required: true }, { name: 'consent', type: 'boolean', required: true }, { name: 'quantity', type: 'number', required: true }];
  assert.equal(workflowInputIssues(declarations, { order: ' \n', consent: false, quantity: 0 }).length, 1);
  assert.equal(workflowInputIssues(declarations, { order: 'L001', consent: false, quantity: 0 }).length, 0);
});

test('an untouched boolean is missing; selection and clearing do not silently supply false or zero', () => {
  assert.equal(parseWorkflowInput('', 'boolean'), undefined);
  assert.equal(parseWorkflowInput('false', 'boolean'), false);
  assert.equal(parseWorkflowInput('', 'number'), undefined);
  assert.equal(parseWorkflowInput('0', 'number'), 0);
  assert.equal(workflowInputIssues([{ name: 'consent', type: 'boolean', required: true }], {}).length, 1);
});

test('strict types and finite numbers match server checks without echoing values', () => {
  for (const [type, value] of [['number', NaN], ['number', Infinity], ['number', true], ['number', '12'], ['boolean', 'false'], ['string', { token: 'private' }]]) {
    const issues = workflowInputIssues([{ name: 'value', type }], { value });
    assert.equal(issues.length, 1);
    assert.equal(issues[0].code, 'type');
    assert.equal(JSON.stringify(issues).includes('private'), false);
  }
});

test('older optional declarations accept missing inputs and leave the input object unchanged', () => {
  const input = Object.freeze({ extra: 'context' });
  assert.equal(workflowInputIssues([{ name: 'old', type: 'number' }], input).length, 0);
  assert.deepEqual(input, { extra: 'context' });
});

test('declared text length and number range match server rules without exposing input', () => {
  const declarations = [
    { name: 'question', type: 'string', min_length: 2, max_length: 6 },
    { name: 'quantity', type: 'number', minimum: 0, maximum: 10 },
  ];
  const rejected = workflowInputIssues(declarations, { question: '客户私密内容很长', quantity: 11 });
  assert.equal(rejected.map(issue => issue.code).join(','), 'length,range');
  assert.equal(JSON.stringify(rejected).includes('客户私密内容'), false);
  assert.equal(workflowInputIssues(declarations, { question: '订单号', quantity: 0 }).length, 0);
});

test('business choices reject unknown text without exposing the submitted value', () => {
  const declarations = [{ name: 'reason', type: 'string', allowed_values: ['退货', '换货'] }];
  assert.equal(workflowInputIssues(declarations, { reason: '退货' }).length, 0);
  const issues = workflowInputIssues(declarations, { reason: 'PRIVATE-CUSTOMER-TEXT' });
  assert.equal(issues[0].code, 'choice');
  assert.equal(JSON.stringify(issues).includes('PRIVATE-CUSTOMER-TEXT'), false);
});
