/**
 * WuKongIM EasyJSSDK wrapper for miniprogram
 * easyjssdk v1.1.0+ natively supports WeChat MiniProgram WebSocket
 */
var WKIM = require('easyjssdk').WKIM
var WKIMEvent = require('easyjssdk').WKIMEvent
var adapter = require('../adapters/request')

function WuKongIMService() {
  this._inited = false
  this._im = null
  this._cfg = null
  this._uid = null
  this._bound = false
  this._hConnect = null
  this._hDisconnect = null
  this._hError = null
  this._hMessage = null
  this._hCustom = null
  this._msgListeners = []
  this._statusListeners = []
  this._customListeners = []
}

WuKongIMService.prototype.isReady = function () {
  return this._inited && !!this._im && !!this._cfg
}

WuKongIMService.prototype.getUid = function () {
  return this._uid
}

WuKongIMService.prototype.init = function (opts) {
  var self = this
  this._cfg = Object.assign({}, opts, { channelType: opts.channelType || 'person' })
  var config = this._cfg
  this._uid = opts.uid

  return this._fetchRouteWsAddr(opts.apiBase, opts.uid, 10000).then(function (wsAddr) {
    if (self._cfg !== config) throw new Error('聊天身份已变化，请重新连接。')
    if (self._im && self._bound) {
      try { self._unbindInternalEvents() } catch (e) {}
    }
    self._im = WKIM.init(wsAddr, { uid: opts.uid, token: opts.token || '' })
    self._bindInternalEvents()
    self._inited = true
  })
}

WuKongIMService.prototype._bindInternalEvents = function () {
  if (!this._im || this._bound) return
  var self = this
  var connection = this._im
  var config = this._cfg
  function current() { return self._im === connection && self._cfg === config }

  this._hConnect = function (result) { if (current()) self._emitStatus('connected', result) }
  this._hDisconnect = function (info) { if (current()) self._emitStatus('disconnected', info) }
  this._hError = function (err) { if (current()) self._emitStatus('error', err) }
  this._hMessage = function (message) { if (current()) self._emitMessage(message) }
  this._hCustom = function (ev) { if (current()) self._emitCustom(ev) }

  this._im.on(WKIMEvent.Connect, this._hConnect)
  this._im.on(WKIMEvent.Disconnect, this._hDisconnect)
  this._im.on(WKIMEvent.Error, this._hError)
  this._im.on(WKIMEvent.Message, this._hMessage)
  this._im.on(WKIMEvent.CustomEvent, this._hCustom)
  this._bound = true
}

WuKongIMService.prototype._unbindInternalEvents = function () {
  if (!this._im || !this._bound) return
  if (this._hConnect) this._im.off(WKIMEvent.Connect, this._hConnect)
  if (this._hDisconnect) this._im.off(WKIMEvent.Disconnect, this._hDisconnect)
  if (this._hError) this._im.off(WKIMEvent.Error, this._hError)
  if (this._hMessage) this._im.off(WKIMEvent.Message, this._hMessage)
  if (this._hCustom) this._im.off(WKIMEvent.CustomEvent, this._hCustom)
  this._hConnect = this._hDisconnect = this._hError = this._hMessage = this._hCustom = null
  this._bound = false
}

WuKongIMService.prototype._emitMessage = function (m) {
  this._msgListeners.forEach(function (fn) { try { fn(m) } catch (e) {} })
}

WuKongIMService.prototype._emitStatus = function (s, info) {
  this._statusListeners.forEach(function (fn) { try { fn(s, info) } catch (e) {} })
}

WuKongIMService.prototype._emitCustom = function (e) {
  this._customListeners.forEach(function (fn) { try { fn(e) } catch (e2) {} })
}

WuKongIMService.prototype.onMessage = function (cb) {
  this._msgListeners.push(cb)
  var self = this
  return function () {
    var idx = self._msgListeners.indexOf(cb)
    if (idx >= 0) self._msgListeners.splice(idx, 1)
  }
}

WuKongIMService.prototype.onStatus = function (cb) {
  this._statusListeners.push(cb)
  var self = this
  return function () {
    var idx = self._statusListeners.indexOf(cb)
    if (idx >= 0) self._statusListeners.splice(idx, 1)
  }
}

WuKongIMService.prototype.onCustom = function (cb) {
  this._customListeners.push(cb)
  var self = this
  return function () {
    var idx = self._customListeners.indexOf(cb)
    if (idx >= 0) self._customListeners.splice(idx, 1)
  }
}

WuKongIMService.prototype.connect = function () {
  if (!this._im) throw new Error('WuKongIMService not initialized')
  this._emitStatus('connecting')
  return this._im.connect()
}

WuKongIMService.prototype.disconnect = function () {
  var connection = this._im
  this._unbindInternalEvents()
  this._im = null
  this._cfg = null
  this._uid = null
  this._inited = false
  if (connection && typeof connection.disconnect === 'function') {
    try { return Promise.resolve(connection.disconnect()) } catch (e) { return Promise.resolve() }
  }
  return Promise.resolve()
}

WuKongIMService.prototype._fetchRouteWsAddr = function (apiBase, uid, timeoutMs) {
  var base = apiBase.endsWith('/') ? apiBase : apiBase + '/'
  var url = base + 'v1/wukongim/route?uid=' + encodeURIComponent(uid)

  return adapter.request({
    url: url,
    method: 'GET',
    timeout: timeoutMs || 10000
  }).then(function (res) {
    if (!res.ok) throw new Error('route HTTP ' + res.status)
    return res.json().then(function (data) {
      // Priority 1: wss_addr
      if (data && data.wss_addr && typeof data.wss_addr === 'string' && data.wss_addr.trim()) {
        return data.wss_addr.trim()
      }
      // Priority 2: ws_addr fallbacks
      var addr = data.ws_addr || data.ws || data.ws_url || data.wsAddr || data.websocket
      if (!addr && data.wss) addr = data.wss
      if (!addr || typeof addr !== 'string') throw new Error('missing ws address')
      var wsAddr = String(addr)
      if (/^http(s)?:/i.test(wsAddr)) wsAddr = wsAddr.replace(/^http/i, 'ws')
      return wsAddr
    })
  }).catch(function (e) {
    var msg = e && e.message ? String(e.message) : String(e)
    throw new Error('[WuKongIM] route fetch failed: ' + msg)
  })
}

WuKongIMService.prototype.sendText = function (text, opts) {
  if (!this._im || !this._cfg) throw new Error('WuKongIMService not ready')
  opts = opts || {}
  var to = opts.to || this._cfg.target
  var payload = { type: 1, content: text }
  return this._im.send(to, opts.channelType || 251, payload, {
    clientMsgNo: opts.clientMsgNo,
    header: opts.header
  })
}

WuKongIMService.prototype.sendPayload = function (payload, opts) {
  if (!this._im || !this._cfg) throw new Error('WuKongIMService not ready')
  opts = opts || {}
  var to = opts.to || this._cfg.target
  return this._im.send(to, opts.channelType || 251, payload, {
    clientMsgNo: opts.clientMsgNo,
    header: opts.header
  })
}

// Singleton
var instance = new WuKongIMService()

module.exports = instance
