import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/workflowTraceNavigation.ts', import.meta.url), 'utf8');
const exports = {};
vm.runInNewContext(ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports });

test('only a customer message with a canonical channel and original ID can open workflow records', () => {
  const message = {
    type: 'visitor', channelType: 251,
    channelId: '9f1e9a75-1e6b-4a1c-97fd-6dd8db54e833-vtr',
    sourceMessageId: 'original-42', messageId: 'im-42',
  };
  assert.deepEqual(JSON.parse(JSON.stringify(exports.customerWorkflowMessageLookup(message))), {
    channel_id: message.channelId, source_message_id: message.sourceMessageId,
  });
  assert.equal(exports.customerWorkflowMessageLookup({ ...message, type: 'staff' }), null);
  assert.equal(exports.customerWorkflowMessageLookup({ ...message, channelType: 1 }), null);
  assert.equal(exports.customerWorkflowMessageLookup({ ...message, sourceMessageId: undefined }), null);
  assert.equal(exports.customerWorkflowMessageLookup({ ...message, channelId: 'foreign-channel' }), null);
  assert.equal(exports.customerWorkflowMessageLookup({ ...message, sourceMessageId: 'x'.repeat(256) }), null);
});

test('navigation state validates the lookup before the workflow page uses it', () => {
  const lookup = {
    channel_id: '9f1e9a75-1e6b-4a1c-97fd-6dd8db54e833-vtr',
    source_message_id: 'original-42',
  };
  assert.equal(exports.isWorkflowMessageNavigation({ workflowMessageLookup: lookup }), true);
  assert.equal(exports.isWorkflowMessageNavigation({ workflowMessageLookup: { ...lookup, channel_id: 'other' } }), false);
  assert.equal(exports.isWorkflowMessageNavigation({ workflowMessageLookup: { ...lookup, source_message_id: '' } }), false);
  assert.equal(exports.isWorkflowMessageNavigation(null), false);
});
