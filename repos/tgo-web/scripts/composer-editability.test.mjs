import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// Exercise the actual JSX attributes: a delayed delivery-status lookup must
// block transmission without turning a draft into a read-only mobile field.
const source = readFileSync(new URL('../src/components/chat/MessageInput.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('MessageInput.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let textarea;
let sendButton;
function visit(node) {
  if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(ast) === 'textarea') textarea = node;
  if (ts.isJsxOpeningElement(node) && node.tagName.getText(ast) === 'button'
    && node.attributes.properties.some(attribute => ts.isJsxAttribute(attribute)
      && attribute.name.getText(ast) === 'onClick'
      && attribute.initializer?.expression?.getText(ast) === 'handleSend')) sendButton = node;
  ts.forEachChild(node, visit);
}
visit(ast);
assert.ok(textarea);
assert.ok(sendButton);

function attribute(element, name, state) {
  const value = element.attributes.properties.find(item => ts.isJsxAttribute(item) && item.name.getText(ast) === name);
  assert.ok(value?.initializer && ts.isJsxExpression(value.initializer), `missing ${name}`);
  return vm.runInNewContext(value.initializer.expression.getText(ast), {
    isManualDisabled: false, isSending: false, isSendingLocal: false,
    message: '已编辑的草稿', pastedItems: [], selectedFiles: [], ...state,
  });
}

for (const state of [{ isSending: true }, { isSendingLocal: true }]) {
  test(`draft remains editable while transmission is blocked: ${JSON.stringify(state)}`, () => {
    assert.equal(attribute(textarea, 'disabled', state), false);
    assert.equal(attribute(textarea, 'readOnly', state), false);
    assert.equal(attribute(sendButton, 'disabled', state), true);
  });
}

test('automatic AI mode still prevents manual editing and transmission', () => {
  const state = { isManualDisabled: true };
  assert.equal(attribute(textarea, 'disabled', state), true);
  assert.equal(attribute(sendButton, 'disabled', state), true);
});

test('a ready assist draft can be edited and confirmed', () => {
  assert.equal(attribute(textarea, 'readOnly', {}), false);
  assert.equal(attribute(sendButton, 'disabled', {}), false);
});
