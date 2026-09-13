import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const headerModule = { exports: {} };
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/utils/mcpHeaders.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports: headerModule.exports });
const { serializeMcpHeaders, readMcpHeaders, redactMcpHeaderValues } = headerModule.exports;

// Execute the actual submit callback, including its async request boundary.
function submitHandler(file, overrides = {}) {
  const source = readFileSync(new URL(`../src/components/ai/${file}.tsx`, import.meta.url), 'utf8');
  const tree = ts.createSourceFile(file + '.tsx', source, ts.ScriptTarget.Latest, true);
  let callback;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(tree) === 'handleSubmit') callback = node.initializer;
    ts.forEachChild(node, visit);
  }
  visit(tree);
  assert.ok(callback);
  const calls = [];
  const feedback = [];
  const states = [];
  let closed = 0;
  let resets = 0;
  const request = async (...args) => { calls.push(args); };
  const scope = {
    formData: { name: 'weather', description: '天气查询', endpoint: 'https://example.test/weather',
      transport_type: 'http', method: 'POST', headers: [], parameters: [] },
    tool: { id: 'tool-1', config: { timeout: 12, extension: { keep: true } } },
    user: { project_id: 'project-1' }, validateForm: () => true,
    submittingRef: { current: false },
    setIsSubmitting: state => states.push(state),
    ProjectToolsApiService: { createAiTool: request }, updateTool: request,
    loadTools: async () => {},
    showToast: (...args) => feedback.push(args), t: (_key, fallback) => fallback,
    setFormData: () => { resets++; }, setErrors() {},
    onClose: () => { closed++; }, handleClose: () => { closed++; },
    console: { error() {} }, ...overrides,
    serializeMcpHeaders,
  };
  vm.runInNewContext(ts.transpileModule(`globalThis.submit = ${callback.getText(tree)}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, scope);
  return { scope, calls, feedback, states, submit: () => scope.submit({ preventDefault() {} }),
    closed: () => closed, resets: () => resets };
}

for (const file of ['AddToolModal', 'AddHTTPToolModal', 'EditToolModal', 'EditHTTPToolModal']) {
  test(`${file}: rapid repeated submission issues only one request`, async () => {
    let resolve;
    let requests = 0;
    const pending = new Promise(done => { resolve = done; });
    const request = () => { requests++; return pending; };
    const form = submitHandler(file, {
      ProjectToolsApiService: { createAiTool: request }, updateTool: request,
    });
    const first = form.submit();
    const second = form.submit();
    assert.equal(requests, 1);
    resolve();
    await Promise.all([first, second]);
    assert.equal(form.closed(), 1);
    assert.equal(form.feedback.filter(item => item[0] === 'success').length, 1);
    assert.equal(form.scope.submittingRef.current, false);
  });

  test(`${file}: failed save preserves input and unlocks retry`, async () => {
    let attempts = 0;
    const request = async () => { if (++attempts === 1) throw new Error('request failed'); };
    const form = submitHandler(file, {
      ProjectToolsApiService: { createAiTool: request }, updateTool: request,
    });
    await form.submit();
    assert.equal(form.closed(), 0);
    assert.equal(form.resets(), 0);
    assert.equal(form.feedback[0][0], 'error');
    assert.equal(form.scope.submittingRef.current, false);
    await form.submit();
    assert.equal(attempts, 2);
    assert.equal(form.closed(), 1);
    assert.deepEqual(form.states, [true, false, true, false]);
  });
}

test('editing HTTP tools preserves configuration not represented by the form', async () => {
  const form = submitHandler('EditHTTPToolModal');
  await form.submit();
  const [id, payload] = form.calls[0];
  assert.equal(id, 'tool-1');
  assert.equal(payload.config.timeout, 12);
  assert.equal(payload.config.extension.keep, true);
  assert.equal(payload.config.method, 'POST');
});

test('clearing an MCP tool description submits an explicit clear operation', async () => {
  const form = submitHandler('EditToolModal', {
    formData: { name: 'lookup', description: '   ', transport_type: 'http',
      endpoint: 'https://example.test/mcp' },
  });
  await form.submit();
  const [id, payload] = form.calls[0];
  assert.equal(id, 'tool-1');
  assert.equal(payload.name, 'lookup');
  assert.equal(payload.description, null);
  assert.equal(payload.endpoint, 'https://example.test/mcp');
});

test('MCP creation uses the backend MCP enum, not the obsolete Tool label', async () => {
  const form = submitHandler('AddToolModal');
  await form.submit();
  assert.equal(form.calls[0][0].tool_type, 'MCP');
});

test('HTTP creation uses FUNCTION with the http_webhook transport', async () => {
  const form = submitHandler('AddHTTPToolModal');
  await form.submit();
  assert.equal(form.calls[0][0].tool_type, 'FUNCTION');
  assert.equal(form.calls[0][0].transport_type, 'http_webhook');
});

for (const file of ['AddToolModal', 'EditToolModal']) {
  test(`${file}: saved MCP headers reach the request and retain extension settings`, async () => {
    const form = submitHandler(file);
    form.scope.formData.headers = [{ key: 'Authorization', value: 'Bearer fixture' }];
    await form.submit();
    const payload = form.calls[0].at(-1);
    assert.equal(payload.config.headers.Authorization, 'Bearer fixture');
    if (file === 'EditToolModal') assert.equal(payload.config.extension.keep, true);
  });
}

test('MCP headers reload into editable rows and can be explicitly cleared', () => {
  const rows = readMcpHeaders({ Authorization: 'Bearer fixture' });
  assert.equal(rows[0].key, 'Authorization');
  assert.equal(rows[0].value, 'Bearer fixture');
  assert.equal(Object.keys(serializeMcpHeaders([])).length, 0);
});

test('MCP headers reject duplicates, invalid names and line injection without exposing values', () => {
  for (const rows of [
    [{ key: 'Authorization', value: 'private' }, { key: 'authorization', value: 'private' }],
    [{ key: 'Invalid name', value: 'private' }],
    [{ key: 'Authorization', value: 'private\r\nInjected: bad' }],
  ]) {
    assert.throws(() => serializeMcpHeaders(rows), { message: 'INVALID_MCP_HEADERS' });
  }
});

test('tool save error text redacts header values without hiding HTTP status', () => {
  const message = redactMcpHeaderValues('HTTP 401: Bearer fixture-secret (fixture-secret)', {
    Authorization: 'Bearer fixture-secret',
  });
  assert.equal(message, 'HTTP 401: [redacted] ([redacted])');
});
