import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
const exports = {};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/agentDeviceScriptValidation.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports, TextEncoder });
const { parseScriptFields: parse } = exports;
const field = (name, kind, extra = {}) => ({ name, kind, required: true,
  minimum: kind === 'integer' ? -10 : null, maximum: kind === 'integer' ? 10 : null,
  max_length: kind === 'string' ? 4096 : 0, choices: [], ...extra });

test('field conversion preserves false, zero, strings and omitted optional fields', () => {
  const value = parse([field('ready', 'boolean'), field('count', 'integer'), field('title', 'string'), field('optional', 'string', { required: false })],
    { ready: 'false', count: '0', title: '日报' });
  assert.equal(JSON.stringify(value), JSON.stringify({ ready: false, count: 0, title: '日报' }));
});
test('invalid integer syntax, unsafe values and unchecked booleans are refused', () => {
  for (const value of ['', '1.5', '1e1', '9007199254740993', '11', '-11', ' 1']) assert.equal(parse([field('count', 'integer')], { count: value }), null);
  for (const value of ['', 'yes', '0']) assert.equal(parse([field('ready', 'boolean')], { ready: value }), null);
});
test('result text permits reviewed multiline output while input text retains its stricter limits', () => {
  const fields = [field('summary', 'string')], values = { summary: '第一行\n第二行' };
  assert.equal(parse(fields, values), null);
  assert.equal(parse(fields, values, true).summary, values.summary);
  for (const value of ['\u0000', '\ud800']) assert.equal(parse(fields, { summary: value }, true), null);
});
test('business names, choices, Unicode length and inherited keys are checked', () => {
  assert.equal(parse([field('__proto__', 'string')], {}), null);
  assert.equal(parse([field('constructor', 'string')], {}), null);
  assert.equal(parse([field('code', 'string', { choices: ['ok'] })], { code: 'no' }), null);
  assert.equal(parse([field('title', 'string', { max_length: 1 })], { title: '🙂' }).title, '🙂');
  assert.equal(parse([field('title', 'string', { max_length: 1 })], { title: '两个' }), null);
  for (const value of ['\u0085', '\ufffd', '\u2028']) assert.equal(parse([field('title', 'string')], { title: value }), null);
});
test('escaped UTF-8 limits apply to the complete output map', () => {
  const fields = Array.from({ length: 10 }, (_, index) => field(`part${index}`, 'string'));
  const values = Object.fromEntries(fields.map(item => [item.name, '<'.repeat(1200)]));
  assert.equal(parse(fields, values), null);
  assert.equal(parse([field('value', 'string')], { value: '<'.repeat(1000) }).value.length, 1000);
});
