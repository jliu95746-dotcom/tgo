const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const test = require('node:test')

const sourceUrl = 'https://untrusted.test/v1/chat/files/11111111-1111-4111-8111-111111111111?file_token=old'
const approvedUrl = 'https://owned.test/api/v1/chat/files/11111111-1111-4111-8111-111111111111?file_token=short-lived'

function loadTypes() {
  const module = { exports: {} }
  const filename = path.join(__dirname, '../src/core/types.js')
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    module, require: () => ({}), Date, JSON, console,
  })
  return module.exports
}

function deferred() {
  let resolve
  return { promise: new Promise(done => { resolve = done }), resolve: value => resolve(value) }
}

function fixture() {
  const types = loadTypes()
  const access = []
  const players = []
  let definition
  const fileAccess = {
    isPrivateChatFile: url => url === sourceUrl,
    maskPrivateChatMarkdown: value => value || '',
    authorizeChatMarkdown: async value => value,
    readChatFileAccess: async () => ({ url: approvedUrl, expiresAt: Date.now() + 60000 }),
    authorizeChatFileUrl: () => { const task = deferred(); access.push(task); return task.promise },
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/components/message-list/index.js'), 'utf8'), {
    Component: value => { definition = value }, Date, JSON, Promise,
    setTimeout: () => 1, clearTimeout: () => {},
    require: name => name.includes('core/types') ? types : name.includes('chatFileAccess') ? fileAccess
      : name.includes('aiReplyOrigin') ? {} : { formatMessageTime: () => '' },
    wx: { createInnerAudioContext: () => {
      const player = { playCalls: 0, stopCalls: 0, destroyCalls: 0,
        play() { this.playCalls++ }, stop() { this.stopCalls++ }, destroy() { this.destroyCalls++ },
        onPlay(callback) { this.playCallback = callback },
        onEnded(callback) { this.endCallback = callback },
        onStop(callback) { this.stopCallback = callback },
        onError(callback) { this.errorCallback = callback },
      }
      players.push(player)
      return player
    } },
  })
  const props = {
    messages: [{ id: 'voice-1', role: 'agent', payload: { type: 4, content: sourceUrl, url: sourceUrl } }],
    apiBase: 'https://owned.test/api', platformApiKey: 'public-key', sessionRevision: 1,
  }
  const component = { properties: props, data: { ...definition.data }, setData(value) { Object.assign(this.data, value) } }
  Object.assign(component, definition.methods)
  definition.lifetimes.attached.call(component)
  return { definition, component, props, access, players }
}

test('mini live and historical voice payloads retain type 4', () => {
  const types = loadTypes()
  const payload = { type: 4, content: sourceUrl, url: sourceUrl }
  assert.equal(types.toPayloadFromAny(payload).type, 4)
  assert.equal(types.toPayloadFromAny(JSON.stringify(payload)).url, sourceUrl)
  const restored = types.mapHistoryToChatMessage({ payload, message_seq: 1, timestamp: 1, from_uid: 'staff' }, 'visitor-vtr')
  assert.equal(restored.payload.type, 4)
  assert.equal(restored.payload.url, sourceUrl)
})

test('mini voice playback obtains current-session file access and stops on identity change', async () => {
  const { definition, component, props, access, players } = fixture()
  assert.equal(component.data.displayMessages[0].payload.url, '')
  assert.equal(component.data.displayMessages[0].payload.content, '')
  component.onTapVoice({ currentTarget: { dataset: { id: 'voice-1' } } })
  assert.equal(access.length, 1)
  assert.equal(players.length, 0)
  access[0].resolve(approvedUrl)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(players.length, 1)
  assert.equal(players[0].src, approvedUrl)
  assert.equal(players[0].playCalls, 1)
  props.sessionRevision = 2
  component._processMessages(props.messages)
  assert.equal(players[0].stopCalls, 1)
  assert.equal(players[0].destroyCalls, 1)
  assert.equal(component.data.playingVoiceId, '')
  definition.lifetimes.detached.call(component)
})

test('mini voice ignores a late old-session link and stops when hidden', async () => {
  const { definition, component, props, access, players } = fixture()
  component.onTapVoice({ currentTarget: { dataset: { id: 'voice-1' } } })
  props.sessionRevision = 2
  component._processMessages(props.messages)
  access[0].resolve(approvedUrl)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(players.length, 0)
  component.onTapVoice({ currentTarget: { dataset: { id: 'voice-1' } } })
  access[1].resolve(approvedUrl)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(players.length, 1)
  definition.pageLifetimes.hide.call(component)
  assert.equal(players[0].destroyCalls, 1)
  definition.lifetimes.detached.call(component)
})

test('mini voice rejects a non-web link and releases a failed player', async () => {
  const { definition, component, access, players } = fixture()
  component.onTapVoice({ currentTarget: { dataset: { id: 'voice-1' } } })
  access[0].resolve('javascript:old-session')
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(players.length, 0)
  assert.equal(component.data.voiceErrorId, 'voice-1')

  component.onTapVoice({ currentTarget: { dataset: { id: 'voice-1' } } })
  access[1].resolve(approvedUrl)
  await new Promise(resolve => setImmediate(resolve))
  players[0].errorCallback()
  assert.equal(players[0].destroyCalls, 1)
  assert.equal(component.data.playingVoiceId, '')
  assert.equal(component.data.voiceErrorId, 'voice-1')
  definition.lifetimes.detached.call(component)
})

test('mini message list has a voice play control', () => {
  const template = fs.readFileSync(path.join(__dirname, '../src/components/message-list/index.wxml'), 'utf8')
  assert.match(template, /item\.payload\.type === 4/)
  assert.match(template, /bindtap="onTapVoice"/)
})
