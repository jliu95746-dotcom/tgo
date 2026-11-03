import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { spawn } from 'node:child_process'
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs'
import { dirname, join, relative, resolve, isAbsolute } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

test('built CLI consumes ephemeral stdin proof through real JSON/SSE HTTP without storing or printing it', async () => {
  const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..')
  const temporaryRoot = resolve(repo, '../../.tmp')
  const directory = mkdtempSync(join(temporaryRoot, 'customer-sdk-http-'))
  const config = join(directory, 'config.json')
  let configured
  const proof = [{ connection_id: '20000000-0000-0000-0000-000000000001', customer_session: 'private-native-cli-fixture-session' }]
  const bodies = []
  const grant = 'owned-cli-header-grant', headers = []
  let mode = 'normal'
  const server = createServer(async (request, response) => {
    const chunks = []; for await (const chunk of request) chunks.push(chunk)
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    bodies.push(body)
    headers.push(request.headers)
    if (mode === 'echo') { response.writeHead(403, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ message: proof[0].customer_session })); return }
    if (!body.stream) { response.writeHead(200, { 'Content-Type': 'application/json' }); response.end(JSON.stringify({ success: true, content: '已发货' })); return }
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    for (const event of [
      { event_type: 'agent_content_chunk', data: { data: { content_chunk: '草稿' } } },
      { event_type: 'workflow_completed', data: { data: { success: true, final_content: '已发货' } } },
    ]) response.write('data: ' + JSON.stringify(event) + '\n\n')
    response.end()
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  configured = JSON.stringify({server:`http://127.0.0.1:${address.port}`,api_key:'fixture-only-public-key',
    visitor_id:'owned-visitor-id',im_token:grant,platform_open_id:'owned-visitor',channel_id:'owned-channel',channel_type:251})
  writeFileSync(config,configured)
  const run = args => new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [join(repo, 'dist/index.js'), '--server', `http://127.0.0.1:${address.port}`,
      '--api-key', 'fixture-only-public-key', 'chat', 'send', '--message', '查询订单', '--customer-sessions-stdin', ...args],
      { cwd: repo, env: { ...process.env, TGO_WIDGET_CONFIG_PATH: config }, windowsHide: true })
    let stdout = '', stderr = ''
    const timer = setTimeout(() => { child.kill(); reject(new Error('Owned CLI did not finish')) }, 15000)
    child.stdout.on('data', value => { stdout += value })
    child.stderr.on('data', value => { stderr += value })
    child.on('error', error => { clearTimeout(timer); reject(error) })
    child.on('close', code => { clearTimeout(timer); resolve({ code, stdout, stderr }) })
    child.stdin.end(JSON.stringify(proof))
  })
  try {
    for (const args of [['--no-stream'], []]) {
      const result = await run(args)
      assert.equal(result.code, 0, result.stderr.replaceAll(grant,'[redacted]').replaceAll(proof[0].customer_session,'[redacted]'))
      assert.ok(result.stdout.includes('已发货'))
      assert.equal(result.stdout.includes('草稿'), false)
      assert.equal((result.stdout + result.stderr).includes(proof[0].customer_session), false)
    }
    mode = 'echo'
    const denied = await run(['--no-stream'])
    assert.equal(denied.code, 1)
    assert.equal((denied.stdout + denied.stderr).includes(proof[0].customer_session), false)
    assert.ok(denied.stderr.includes('403'))
    assert.deepEqual(bodies.map(body => body.customer_sessions), [proof, proof, proof])
    assert.ok(headers.every(header => header.authorization === 'Bearer '+grant))
    assert.equal(JSON.stringify(bodies).includes(grant),false)
    assert.equal(readFileSync(config, 'utf8'), configured)
  } finally {
    await new Promise(resolve => server.close(resolve))
    const target = resolve(directory), relation = relative(temporaryRoot, target)
    assert.ok(relation && !relation.startsWith('..') && !isAbsolute(relation))
    rmSync(target, { recursive: true, force: true })
  }
})
