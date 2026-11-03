/** Merchant sessions live only in the caller's current request; never in store/data. */
var UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
var FAILURE = '客户业务身份暂不可确认，请重新验证后发送或转人工。'

function CustomerIdentityChangedError() {
  this.name = 'CustomerIdentityChangedError'
  this.message = FAILURE
}
CustomerIdentityChangedError.prototype = Object.create(Error.prototype)
CustomerIdentityChangedError.prototype.constructor = CustomerIdentityChangedError

function normalize(value) {
  if (!Array.isArray(value) || value.length > 20) throw new Error(FAILURE)
  var seen = Object.create(null)
  return Object.freeze(value.map(function (item) {
    if (!item || typeof item !== 'object' || Array.isArray(item) || Object.keys(item).length !== 2
      || Object.keys(item).some(function (key) { return key !== 'connection_id' && key !== 'customer_session' }) || typeof item.connection_id !== 'string'
      || !UUID.test(item.connection_id) || typeof item.customer_session !== 'string'
      || !item.customer_session.trim() || item.customer_session.length > 8192) throw new Error(FAILURE)
    var id = item.connection_id.toLowerCase()
    if (seen[id]) throw new Error(FAILURE)
    seen[id] = true
    return Object.freeze({ connection_id: id, customer_session: item.customer_session })
  }))
}

function ensureNoEcho(value, sessions) {
  var text = JSON.stringify(value) || ''
  if ((sessions || []).some(function (session) { return text.indexOf(session.customer_session) !== -1 })) throw new Error(FAILURE)
}

function normalizeIdentities(value) {
  if (!Array.isArray(value) || value.length > 20) throw new Error(FAILURE)
  var seen = Object.create(null)
  return Object.freeze(value.map(function (item) {
    if (!item || typeof item !== 'object' || Object.keys(item).length !== 2 || typeof item.connection_id !== 'string' || !UUID.test(item.connection_id)) throw new Error(FAILURE)
    var id = item.connection_id.toLowerCase()
    if (seen[id]) throw new Error(FAILURE)
    seen[id] = true
    if (Object.prototype.hasOwnProperty.call(item,'customer_session')) return normalize([item])[0]
    if (Object.keys(item).some(function (key) {return key !== 'connection_id' && key !== 'merchant_assertion'})
      || typeof item.merchant_assertion !== 'string' || !item.merchant_assertion.trim() || item.merchant_assertion.length > 16384) throw new Error(FAILURE)
    return Object.freeze({connection_id:id,merchant_assertion:item.merchant_assertion})
  }))
}

function resolveIdentities(inputs, context, registration, assertCurrent) {
  var adapter = require('../adapters/request')
  var output = []
  var chain = Promise.resolve()
  normalizeIdentities(inputs).forEach(function (input) {
    chain = chain.then(function () {
      assertCurrent()
      if (Object.prototype.hasOwnProperty.call(input,'customer_session')) {output.push(input);return}
      if (!registration || !registration.visitorAccessToken) throw new Error(FAILURE)
      return adapter.request({url:registration.apiBase.replace(/\/$/,'')+'/v1/business-connections/'+input.connection_id+'/customer-identities/exchange',
        method:'POST',headers:{'Content-Type':'application/json'},timeout:10000,
        body:{merchant_assertion:input.merchant_assertion,visitor_access_token:registration.visitorAccessToken}}).then(function (response) {
        return response.json().then(function (data) {
          assertCurrent()
          var text = JSON.stringify(data)
          if (text.length > 32768 || text.indexOf(input.merchant_assertion) !== -1 || text.indexOf(registration.visitorAccessToken) !== -1) throw new Error(FAILURE)
          if (!response.ok) {
            if (response.status === 409 && data && data.error && data.error.details
              && data.error.details.reason === 'visitor_owner_changed') throw new CustomerIdentityChangedError()
            throw new Error(FAILURE)
          }
          if (!data || data.visitor_id !== context.visitorId || data.project_id !== context.projectId || data.platform_id !== context.platformId
            || data.connection_id !== input.connection_id || data.can_enable !== false || typeof data.session_token !== 'string') throw new Error(FAILURE)
          output.push(normalize([{connection_id:input.connection_id,customer_session:data.session_token}])[0])
        })
      })
    })
  })
  return chain.then(function () {assertCurrent();return Object.freeze(output)}).catch(function (error) {
    assertCurrent()
    if (error instanceof CustomerIdentityChangedError) throw error
    throw new Error(FAILURE)
  })
}

function createProvider() {
  var callback = null
  var owner = null
  var revision = 0
  return {
    set: function (value) {
      if (value != null && typeof value !== 'function') throw new Error(FAILURE)
      revision += 1
      callback = value || null
      var current = {}
      owner = current
      return function () {
        if (owner !== current) return false
        callback = null; owner = null; revision += 1
        return true
      }
    },
    invalidate: function () { revision += 1 },
    capture: function () {
      var version = revision
      return {hasProvider:!!callback,assertCurrent:function () {if (version !== revision) throw new Error(FAILURE)}}
    },
    acquire: function (context, registration) {
      var version = revision
      var provider = callback
      var input = Object.freeze(Object.assign({}, context))
      function assertCurrent() { if (version !== revision) throw new Error(FAILURE) }
      return Promise.resolve().then(function () {
        assertCurrent()
        if (!provider) return undefined
        if (![input.visitorId, input.projectId, input.platformId].every(function (value) {
          return typeof value === 'string' && UUID.test(value)
        })) throw new Error(FAILURE)
        return provider(input)
      }).then(function (value) {
        assertCurrent()
        if (!provider) return {sessions:undefined,assertCurrent:assertCurrent}
        return resolveIdentities(value,context,registration,assertCurrent).then(function (sessions) {
          return {sessions:sessions,assertCurrent:assertCurrent}
        })
      }).catch(function (error) {
        assertCurrent()
        if (error instanceof CustomerIdentityChangedError) throw error
        throw new Error(FAILURE)
      })
    }
  }
}

module.exports = { CustomerIdentityChangedError:CustomerIdentityChangedError,
  normalizeCustomerSessions: normalize, normalizeCustomerIdentities:normalizeIdentities,
  resolveCustomerIdentities:resolveIdentities, ensureNoCustomerSessionEcho: ensureNoEcho, createProvider: createProvider }
