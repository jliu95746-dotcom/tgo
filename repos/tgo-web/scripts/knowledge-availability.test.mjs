import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/platforms/KnowledgeAvailabilitySummary.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('summary.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const node = ast.statements.find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'knowledgeAvailabilityStatus');
const scope = {};
vm.runInNewContext(ts.transpileModule(node.getText(ast).replace('export ', ''), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText + ';globalThis.status = knowledgeAvailabilityStatus;', scope);

test('readiness distinguishes default empty, channel blocked, explicit disabled and unbound agents', () => {
  const report = { binding_mode: 'project_default', collections: [], issues: [] };
  assert.equal(scope.status(report), 'empty');
  report.collections = [{ eligible_chunk_count: 0 }];
  assert.equal(scope.status(report), 'blocked');
  report.collections.push({ eligible_chunk_count: 1 });
  assert.equal(scope.status(report), 'ready');
  report.binding_mode = 'explicit';
  report.collections = [];
  report.issues = ['disabled_binding'];
  assert.equal(scope.status(report), 'blocked');
  report.binding_mode = 'unbound';
  assert.equal(scope.status(report), 'unbound');
});

test('all admission reasons and binding modes have Chinese and English explanations', () => {
  for (const locale of ['zh', 'en']) {
    const translations = JSON.parse(readFileSync(new URL(`../src/i18n/locales/${locale}.json`, import.meta.url), 'utf8'));
    for (const reason of ['not_approved', 'not_yet_effective', 'expired', 'automatic_reply_disabled', 'customer_content', 'channel_not_allowed', 'deleted', 'invalid_time_context']) {
      assert.ok(translations.knowledge.availability.reasons[reason]);
    }
    for (const mode of ['project_default', 'explicit', 'unbound']) assert.ok(translations.knowledge.availability.mode[mode]);
    assert.ok(translations.knowledge.availability.inactive);
    assert.ok(translations.knowledge.availability.issues.inactive_agent);
  }
});

test('inactive employees stay unavailable even when knowledge is eligible', () => {
  const report = { binding_mode: 'project_default', collections: [{ eligible_chunk_count: 7 }], issues: ['inactive_agent'] };
  assert.equal(scope.status(report), 'inactive');
  report.collections = [];
  assert.equal(scope.status(report), 'inactive');
});
