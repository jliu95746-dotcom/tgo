/**
 * Visitor registration & caching service
 * API: POST /v1/visitors/register
 */
var adapter = require('../adapters/request')
var storage = require('../adapters/storage')
var business = require('./businessCustomerSession')

function _cacheKey(apiBase, platformApiKey) {
  return 'tgo:visitor:' + apiBase + ':' + platformApiKey
}

function loadCachedVisitor(apiBase, platformApiKey) {
  return storage.getJSON(_cacheKey(apiBase, platformApiKey))
}

function removeCachedVisitor(apiBase, platformApiKey) {
  storage.remove(_cacheKey(apiBase, platformApiKey))
}

function saveCachedVisitor(apiBase, platformApiKey, v, expiresAtMs) {
  var cached = {
    apiBase: apiBase,
    platform_api_key: platformApiKey,
    visitor_id: v.id,
    platform_open_id: v.platform_open_id,
    channel_id: v.channel_id,
    channel_type: v.channel_type,
    im_token: v.im_token,
    visitor_access_token: v.visitor_access_token,
    visitor_end_token: v.visitor_end_token,
    business_protected: v.business_protected,
    business_access_expires_at: v.business_access_expires_at,
    project_id: v.project_id,
    platform_id: v.platform_id,
    created_at: v.created_at,
    updated_at: v.updated_at,
    expires_at: expiresAtMs
  }
  storage.setJSON(_cacheKey(apiBase, platformApiKey), cached)
  var saved = loadCachedVisitor(apiBase, platformApiKey)
  if (!saved || saved.visitor_id !== v.id || saved.visitor_access_token !== v.visitor_access_token) {
    throw new Error('[Visitor] Failed to retain visitor access credential')
  }
}

function registerVisitor(params) {
  var apiBase = (params.apiBase || '').replace(/\/$/, '')
  var platformApiKey = params.platformApiKey || ''
  var extra = params.extra || {}
  var url = apiBase + '/v1/visitors/register'

  var body = Object.assign({ platform_api_key: platformApiKey }, extra)

  return adapter.request({
    url: url,
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body,
    timeout: 10000
  }).then(function (res) {
    if (!res.ok) {
      var error = new Error('[Visitor] register failed: HTTP ' + res.status)
      error.registrationStatus = res.status
      throw error
    }
    return res.json().then(function (data) {
      if (!data || !data.id || !data.channel_id) {
        throw new Error('[Visitor] invalid register response: missing id/channel_id')
      }
      business.ensureNoCustomerSessionEcho(data, extra.customer_sessions || [])
      if (!data.im_token) throw new Error('[Visitor] Missing im_token in refreshed session')
      if (typeof data.visitor_access_token !== 'string' || !data.visitor_access_token || data.visitor_access_token.length > 256) {
        throw new Error('[Visitor] Missing visitor access credential; update the server before connecting')
      }
      if (data.visitor_end_token !== undefined && (typeof data.visitor_end_token !== 'string'
        || !data.visitor_end_token || data.visitor_end_token.length > 8192)) throw new Error('退出凭据无效，请刷新重试。')
      return data
    })
  })
}

function refreshVisitorSession(params) {
  var lifecycle = require('./visitorLifecycle')
  var state = params.captureCustomerSessionState ? params.captureCustomerSessionState() : null
  var cached = loadCachedVisitor(params.apiBase, params.platformApiKey)
  var lease = null
  var issued = null
  var saved = false
  var startedFresh = !cached
  function assertCurrent(response) {
    try {
      if (params.assertCurrent) params.assertCurrent()
      if (state) state.assertCurrent()
    } catch (error) {
      if (response && response.visitor_end_token) {
        Promise.resolve(lifecycle.endIssuedVisitorAccess(params,response)).catch(function () {})
      }
      throw error
    }
  }
  function endOriginal() {
    assertCurrent()
    return Promise.resolve(lifecycle.endCachedVisitorAccess(params)).then(function () {
      assertCurrent(); cached = null; lease = null; startedFresh = true
    })
  }
  return Promise.resolve().then(function () {
    assertCurrent()
    if (cached && state && state.hasProvider !== !!cached.business_protected) return endOriginal()
  }).then(function () {
    assertCurrent()
    if (cached && !cached.platform_open_id) throw new Error('[Visitor] Missing cached visitor identity')
    if (cached && cached.visitor_id && cached.project_id && cached.platform_id && params.acquireCustomerSessions) {
      return params.acquireCustomerSessions({visitorId: cached.visitor_id, projectId: cached.project_id, platformId: cached.platform_id},
        {apiBase:params.apiBase,visitorAccessToken:cached.visitor_access_token || ''})
    }
  }).then(function (captured) {
    assertCurrent()
    lease = captured
    if (lease) lease.assertCurrent()
    if (cached && cached.business_protected && (!lease || !lease.sessions || !lease.sessions.length)) {
      throw new Error('[Visitor] Current customer verification required')
    }
  }).catch(function (error) {
    assertCurrent()
    if (cached && error instanceof business.CustomerIdentityChangedError) return endOriginal()
    if (!cached) throw error
    return endOriginal().then(function () {throw error})
  }).then(function () {
    assertCurrent()
    var extra = Object.assign({}, params.extra || {})
    if (cached) Object.assign(extra, {visitor_id: cached.visitor_id, platform_open_id: cached.platform_open_id, visitor_access_token: cached.visitor_access_token,
      im_token: cached.visitor_access_token ? undefined : cached.im_token, customer_sessions: lease ? lease.sessions : undefined})
    return registerVisitor({apiBase: params.apiBase, platformApiKey: params.platformApiKey, extra: extra}).catch(function (error) {
      assertCurrent()
      if (!cached || error.registrationStatus !== 401) throw error
      return endOriginal().then(function () {
        return registerVisitor({apiBase: params.apiBase, platformApiKey: params.platformApiKey, extra: params.extra})
      })
    })
  }).then(function (response) {
    issued = response
    assertCurrent(response)
    if (lease) lease.assertCurrent()
    if (startedFresh && response.project_id && response.platform_id && params.acquireCustomerSessions) {
      return params.acquireCustomerSessions({visitorId:response.id,projectId:response.project_id,platformId:response.platform_id},
        {apiBase:params.apiBase,visitorAccessToken:response.visitor_access_token}).then(function (fresh) {
        assertCurrent(response)
        lease = fresh
        lease.assertCurrent()
        if (!lease.sessions || !lease.sessions.length) return response
        return registerVisitor({apiBase:params.apiBase,platformApiKey:params.platformApiKey,extra:{visitor_id:response.id,
          platform_open_id:response.platform_open_id,visitor_access_token:response.visitor_access_token,customer_sessions:lease.sessions}})
      })
    }
    return response
  }).then(function (response) {
    issued = response
    assertCurrent(response)
    if (lease) lease.assertCurrent()
    saveCachedVisitor(params.apiBase, params.platformApiKey, response)
    saved = true
    return loadCachedVisitor(params.apiBase, params.platformApiKey)
  }).catch(function (error) {
    if (!saved && issued && issued.visitor_end_token) {
      Promise.resolve(lifecycle.endIssuedVisitorAccess(params,issued)).catch(function () {})
    }
    throw error
  })
}

module.exports = {
  loadCachedVisitor: loadCachedVisitor,
  removeCachedVisitor: removeCachedVisitor,
  saveCachedVisitor: saveCachedVisitor,
  registerVisitor: registerVisitor,
  refreshVisitorSession: refreshVisitorSession
}
