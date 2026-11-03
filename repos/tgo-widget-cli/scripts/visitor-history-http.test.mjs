import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { dirname, join, resolve, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

test('built history CLI binds its grant to the original session and hides stale or echoed responses', async () => {
  const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  const parent = resolve(repo, '../../.tmp');
  const directory = mkdtempSync(join(parent, 'visitor-history-cli-'));
  const config = join(directory, 'config.json');
  let mode = 'normal', requests = 0, failure;
  const token = 'owned-private-history-token';
  let identity;
  const save = value => writeFileSync(config, JSON.stringify(value));
  const server = createServer(async (request, response) => {
    try {
      requests++;
      let text = ''; for await (const chunk of request) text += chunk;
      const body = JSON.parse(text);
      assert.equal(request.url, '/v1/visitors/messages/sync');
      assert.equal(request.headers.authorization, `Bearer ${token}`);
      assert.equal(text.includes(token), false);
      assert.equal(body.channel_id, identity.channel_id);
      response.setHeader('Content-Type', 'application/json');
      if (mode === 'changed') save({ ...identity, visitor_id: 'visitor-B', im_token: 'B-history-token' });
      if (mode === 'echo') {
        response.statusCode = 401;
        response.end(JSON.stringify({ error: { message: token } }).replace('owned', '\\u006fwned'));
      } else response.end(JSON.stringify({ messages: [{ payload: { content: 'owned-history-A' } }], more: 0 }));
    } catch (error) { failure = error; response.statusCode = 500; response.end('{}'); }
  });
  await new Promise(done => server.listen(0, '127.0.0.1', done));
  const url = `http://127.0.0.1:${server.address().port}`;
  identity = { server: url, api_key: 'owned-public-key', visitor_id: 'visitor-A', im_token: token,
    channel_id: 'visitor-A-vtr', channel_type: 251 };
  const run = (options = []) => new Promise((done, reject) => {
    const child = spawn(process.execPath, [join(repo, 'dist/index.js'), ...options, 'chat', 'history'],
      { cwd: repo, windowsHide: true, env: { ...process.env, TGO_WIDGET_CONFIG_PATH: config,
        TGO_WIDGET_SERVER: '', TGO_WIDGET_API_KEY: '' } });
    let output = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('Owned history CLI timed out')); }, 20000);
    child.stdout.on('data', value => output += value); child.stderr.on('data', value => output += value);
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', code => { clearTimeout(timer); done({ output, code }); });
  });
  try {
    save(identity);
    const success = await run();
    assert.equal(success.code, 0); assert.ok(success.output.includes('owned-history-A'));
    assert.equal(success.output.includes(token), false);
    mode = 'changed'; save(identity);
    const changed = await run();
    assert.notEqual(changed.code, 0); assert.equal(changed.output.includes('owned-history-A'), false);
    assert.equal(changed.output.includes(token), false);
    mode = 'echo'; save(identity);
    const echoed = await run();
    assert.notEqual(echoed.code, 0); assert.equal(echoed.output.includes(token), false);
    assert.equal(echoed.output.includes('\\u006fwned'), false);
    const requestCount = requests;
    save(identity);
    const wrongServer = await run(['--server', `${url}/other`]);
    assert.notEqual(wrongServer.code, 0);
    const wrongKey = await run(['--api-key', 'other-key']);
    assert.notEqual(wrongKey.code, 0);
    save({ ...identity, im_token: undefined });
    const missing = await run(); assert.notEqual(missing.code, 0);
    assert.equal(requests, requestCount); assert.ifError(failure);
  } finally {
    await new Promise(done => server.close(done));
    const inside = relative(parent, resolve(directory));
    assert.ok(inside && !inside.startsWith('..') && !isAbsolute(inside));
    rmSync(directory, { recursive: true, force: true });
  }
});
