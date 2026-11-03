const assert = require('node:assert/strict')
const test = require('node:test')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')
const business = require('../src/services/businessCustomerSession')
const {loadMiniDataAccess} = require('./visitor-access-fixture.cjs')
const sessions = [{ connection_id: '20000000-0000-0000-0000-000000000001', customer_session: 'private-mini-session-fixture' }]

function fixture(eventType = 'accepted') {
  const posts = [], im = [], logs = []
  const adapter = { request: async params => { posts.push(params); return { ok: true, status: 200, json: async () => ({ event_type: eventType }) } } }
  const load = (file, require) => {
    const module = { exports: {} }
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src', file), 'utf8'), { module, require,
      setTimeout() {}, clearTimeout() {}, console: { error: (...values) => logs.push(values) } })
    return module.exports
  }
  const access = loadMiniDataAccess(() => ({visitor_id:'10000000-0000-0000-0000-000000000001',channel_id:'owned-channel',im_token:'owned-mini-header-grant'}))
  const chat = load('services/chat.js', name => name === './businessCustomerSession' ? business : name === './visitorDataAccess' ? access : adapter)
  const store = load('core/chatStore.js', name => {
    if (name === '../services/chat') return chat
    if (name === '../services/businessCustomerSession') return business
    if (name === '../services/visitor') return {loadCachedVisitor:()=>null}
    if (name === '../services/wukongim') return { isReady: () => true, sendText: async (...args) => { im.push(args); return { reasonCode: 1 } }, disconnect() {} }
    if (name === '../utils/uid') return { generateClientMsgNo: () => 'owned-source' }
    if (name === 'easyjssdk') return { ReasonCode: { Success: 1, Unknown: 0 } }
    return {}
  })
  Object.assign(store._state, { apiBase: 'http://owned.test', platformApiKey: 'fixture-key', myUid: '10000000-0000-0000-0000-000000000001-vtr',
    visitorId: '10000000-0000-0000-0000-000000000001', projectId: '10000000-0000-0000-0000-000000000002',
    platformId: '10000000-0000-0000-0000-000000000003', channelId: 'owned-channel', channelType: 251 })
  store._reconcileReplyHistory = async () => {}
  return { store, posts, im, logs }
}

test('real mini send carries sessions only in completion, not IM/state/error logs', async () => {
  const f = fixture()
  f.store.setBusinessCustomerSessionProvider(async () => sessions)
  await f.store.sendMessage('查询订单')
  assert.deepEqual(JSON.parse(JSON.stringify(f.posts[0].body.customer_sessions)), sessions)
  assert.equal(f.posts[0].body.source_message_id, 'owned-source')
  assert.equal(f.posts[0].body.forward_user_message_to_wukongim, false)
  assert.equal(JSON.stringify([f.im, f.store.getState(), f.logs]).includes(sessions[0].customer_session), false)
})

test('mini identity changes during callback prevent both IM and completion', async () => {
  const f = fixture()
  f.store.setBusinessCustomerSessionProvider(async () => { f.store._setState({ myUid: 'different' }); return sessions })
  await f.store.sendMessage('查询订单')
  assert.equal(f.im.length, 0)
  assert.equal(f.posts.length, 0)
  assert.equal(JSON.stringify(f.logs).includes(sessions[0].customer_session), false)
})

test('mini ordinary text works without a provider or customer_sessions field', async () => {
  const f = fixture()
  await f.store.sendMessage('你好')
  assert.equal(f.posts.length, 1)
  assert.equal(f.posts[0].body.customer_sessions, undefined)
})

test('mini direct human request can be sent when business identity provider fails', async () => {
  const f = fixture()
  f.store.setBusinessCustomerSessionProvider(async () => { throw new Error('business identity unavailable') })
  await f.store.sendMessage('转人工')
  assert.equal(f.im.length, 1)
  assert.equal(f.posts.length, 1)
  assert.equal(f.posts[0].body.message, '转人工')
  assert.equal(f.posts[0].body.customer_sessions, undefined)
})

test('mini human handoff does not poll for an AI reply', async () => {
  const f = fixture('human_handoff')
  let polls = 0
  f.store._reconcileReplyHistory = async () => { polls++ }
  await f.store.sendMessage('转人工')
  assert.equal(f.posts.length, 1)
  assert.equal(polls, 0)
})
