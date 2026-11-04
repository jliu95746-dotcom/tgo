import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

test('concurrent init, reset, and end do not restore superseded visitor access', async () => {
  const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..')
  const parent = resolve(repo, '../../.tmp')
  const directory = mkdtempSync(join(parent, 'visitor-concurrent-'))
  const configPath = join(directory, 'config.json')
  let releaseSlowRegistration = () => {}
  let releaseSlowEnd = () => {}
  let releaseResetRegistration = () => {}
  let signalSlowRegistration = () => {}
  let signalSlowEnd = () => {}
  let signalResetRegistration = () => {}
  const registrationStarted = new Promise(resolve => { signalSlowRegistration = resolve })
  const endStarted = new Promise(resolve => { signalSlowEnd = resolve })
  const resetRegistrationStarted = new Promise(resolve => { signalResetRegistration = resolve })
  const registrationGate = new Promise(resolve => { releaseSlowRegistration = resolve })
  const endGate = new Promise(resolve => { releaseSlowEnd = resolve })
  const resetRegistrationGate = new Promise(resolve => { releaseResetRegistration = resolve })
  let slowEnd = false
  const server = createServer(async (request, response) => {
    let input = ''
    for await (const chunk of request) input += chunk
    const body = JSON.parse(input || '{}')
    response.setHeader('Content-Type', 'application/json')
    if (request.url === '/v1/visitors/register') {
      if (body.name === 'slow') {
        signalSlowRegistration()
        await registrationGate
      }
      if (body.name === 'reset-race') {
        signalResetRegistration()
        await resetRegistrationGate
      }
      response.statusCode = 201
      response.end(JSON.stringify({
        id: `visitor-${body.name}`, platform_open_id: `open-${body.name}`,
        channel_id: 'channel', channel_type: 251, platform_id: 'platform',
        im_token: `read-im-${body.name}`, visitor_access_token: `read-data-${body.name}`,
        visitor_end_token: `end-only-${body.name}`,
      }))
    } else if (request.url === '/v1/visitors/access/end') {
      if (slowEnd && body.visitor_id === 'visitor-first') {
        signalSlowEnd()
        await endGate
      }
      response.end(JSON.stringify({ status: 'ended', upstream_stop_status: 'confirmed', im_disconnect_status: 'requested' }))
    } else {
      response.statusCode = 404
      response.end('{}')
    }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const run = args => new Promise((resolveRun, reject) => {
    const child = spawn(process.execPath, [join(repo, 'dist/index.js'),
      '--server', `http://127.0.0.1:${server.address().port}`, '--api-key', 'fixture-key', ...args],
    { cwd: repo, windowsHide: true, env: { ...process.env, TGO_WIDGET_CONFIG_PATH: configPath } })
    let output = ''
    const timer = setTimeout(() => { child.kill(); reject(new Error('Fixture CLI timed out')) }, 15000)
    child.stdout.on('data', chunk => { output += chunk })
    child.stderr.on('data', chunk => { output += chunk })
    child.on('error', error => { clearTimeout(timer); reject(error) })
    child.on('close', code => { clearTimeout(timer); resolveRun({ code, output }) })
  })
  const state = () => JSON.parse(readFileSync(configPath, 'utf8'))
  try {
    const earlier = run(['init', '--name', 'slow'])
    await registrationStarted
    assert.equal((await run(['init', '--name', 'newer'])).code, 0)
    releaseSlowRegistration()
    assert.match((await earlier).output, /较新的登录或退出操作取代/)
    assert.equal(state().visitor_id, 'visitor-newer')
    assert.equal(state().pending_visitor_ends?.length || 0, 0)

    const resetting = run(['init', '--name', 'reset-race'])
    await resetRegistrationStarted
    await run(['reset'])
    releaseResetRegistration()
    assert.match((await resetting).output, /较新的登录或退出操作取代/)
    assert.equal(state().visitor_id, undefined)
    assert.equal(state().pending_visitor_ends?.length || 0, 0)

    assert.equal((await run(['init', '--name', 'first'])).code, 0)
    slowEnd = true
    const ending = run(['session', 'end'])
    await endStarted
    assert.equal((await run(['init', '--name', 'after-end'])).code, 0)
    releaseSlowEnd()
    await ending
    assert.equal(state().visitor_id, 'visitor-after-end')
    assert.equal(state().pending_visitor_ends?.length || 0, 0)

    writeFileSync(configPath, '{broken json')
    const damaged = await run(['init', '--name', 'damaged'])
    assert.match(damaged.output, /本地配置文件损坏/)
    assert.equal(readFileSync(configPath, 'utf8'), '{broken json')

    const brokenPending = JSON.stringify({ pending_visitor_ends: [{ visitor_id: 'missing-end-authority' }] })
    writeFileSync(configPath, brokenPending)
    const invalidPending = await run(['init', '--name', 'damaged-pending'])
    assert.match(invalidPending.output, /退出重试记录损坏/)
    assert.equal(readFileSync(configPath, 'utf8'), brokenPending)
  } finally {
    releaseSlowRegistration()
    releaseSlowEnd()
    releaseResetRegistration()
    await new Promise(resolve => server.close(resolve))
    const inside = relative(parent, resolve(directory))
    assert.ok(inside && !inside.startsWith('..') && !isAbsolute(inside))
    rmSync(directory, { recursive: true, force: true })
  }
})
