/**
 * Platform configuration store (Observable singleton)
 */
var platformService = require('../services/platform')
var storage = require('../adapters/storage')

var WELCOME_KEY = function (apiBase, platformApiKey) {
  return 'tgo:welcome-shown:' + apiBase + ':' + platformApiKey
}

var defaultConfig = {
  position: 'bottom-right',
  theme_color: '#2f80ed',
  widget_title: 'Tgo',
  welcome_message: undefined,
  logo_url: undefined
}

function PlatformStore() {
  this._state = {
    loading: false,
    error: null,
    config: Object.assign({}, defaultConfig),
    welcomeInjected: false,
    _apiBase: '',
    _platformApiKey: ''
  }
  this._listeners = []
  this._revision = 0
}

PlatformStore.prototype.getState = function () {
  return this._state
}

PlatformStore.prototype._setState = function (partial) {
  Object.assign(this._state, partial)
  var state = this._state
  this._listeners.forEach(function (fn) { try { fn(state) } catch (e) {} })
}

PlatformStore.prototype.subscribe = function (fn) {
  this._listeners.push(fn)
  var self = this
  return function () {
    var idx = self._listeners.indexOf(fn)
    if (idx >= 0) self._listeners.splice(idx, 1)
  }
}

PlatformStore.prototype.markWelcomeInjected = function () {
  this._setState({ welcomeInjected: true })
  try {
    var apiBase = this._state._apiBase
    var apiKey = this._state._platformApiKey
    if (apiBase && apiKey) storage.setJSON(WELCOME_KEY(apiBase, apiKey), true)
  } catch (e) {}
}

PlatformStore.prototype.init = function (apiBase, platformApiKey) {
  if (!apiBase || !platformApiKey) return Promise.resolve()
  if (this._state.loading && this._state._apiBase === apiBase && this._state._platformApiKey === platformApiKey) return Promise.resolve()
  var self = this
  var revision = ++this._revision
  this._setState({loading:true,error:null,_apiBase:apiBase,_platformApiKey:platformApiKey,
    config:Object.assign({},defaultConfig),welcomeInjected:false})

  // Read welcome injected from storage
  try {
    var injected = !!storage.getJSON(WELCOME_KEY(apiBase, platformApiKey))
    if (injected) self._setState({ welcomeInjected: true })
  } catch (e) {}

  return platformService.fetchPlatformInfo({
    apiBase: apiBase,
    platformApiKey: platformApiKey
  }).then(function (info) {
    if (revision !== self._revision) return
    var cfg = (info && info.config) || {}
    self._setState({
      config: Object.assign({}, self._state.config, cfg)
    })
  }).catch(function (e) {
    if (revision !== self._revision) return
    self._setState({ error: e && e.message ? e.message : String(e) })
  }).then(function () {
    if (revision !== self._revision) return
    self._setState({ loading: false })
  })
}

module.exports = new PlatformStore()
