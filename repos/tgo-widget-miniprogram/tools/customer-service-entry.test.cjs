const assert = require('node:assert/strict')
const test = require('node:test')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

test('mini human service button sends once while the first request is pending', async () => {
  let definition
  let resolveRequest
  const pending = new Promise(resolve => { resolveRequest = resolve })
  const messages = []
  const chatStore = {sendMessage(message) {messages.push(message); return pending}}
  const i18n = require('../src/core/i18n')
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/chat/index.js'), 'utf8'), {
    Component(value) {definition = value},
    require(name) {
      if (name === '../core/chatStore') return chatStore
      if (name === '../core/platformStore') return {}
      if (name === '../core/i18n') return i18n
      throw new Error('Unexpected dependency: ' + name)
    },
    Promise,
  })
  const instance = {
    ...definition.methods,
    _attached: true,
    _hidden: false,
    _sessionOwner: {isCurrent: () => true},
    properties: {lang: 'zh'},
    data: {online: true, initializing: false, handoffSending: false},
    setData(update) {Object.assign(this.data, update)},
  }

  instance.onRequestHuman()
  instance.onRequestHuman()
  assert.deepEqual(messages, ['转人工'])
  assert.equal(instance.data.handoffSending, true)

  resolveRequest()
  await pending
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(instance.data.handoffSending, false)
  assert.equal(instance.data.humanRequestLabel, '转人工')

  instance.data.online = false
  instance.onRequestHuman()
  assert.equal(messages.length, 1)
})
