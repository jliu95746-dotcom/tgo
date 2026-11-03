/**
 * Chat completion & cancel services
 * API: POST /v1/chat/completion
 * API: POST /v1/ai/runs/cancel-by-client
 */
var adapter = require('../adapters/request')
var businessCustomer = require('./businessCustomerSession')
var dataAccess = require('./visitorDataAccess')

function sendChatCompletion(params) {
  var access
  try {access=dataAccess.captureVisitorDataAccess(params)} catch (error) {return Promise.reject(error)}
  var apiBase = (params.apiBase || '').replace(/\/$/, '')
  var url = apiBase + '/v1/chat/completion'

  var body = {
    api_key: params.platformApiKey,
    message: params.message,
    from_uid: params.fromUid,
    wukongim_only: true,
    forward_user_message_to_wukongim: false,
    stream: false
  }
  if (params.channelId) body.channel_id = params.channelId
  if (params.channelType != null) body.channel_type = params.channelType
  if (params.sourceMessageId) body.source_message_id = params.sourceMessageId
  if (params.customerSessions && params.customerSessions.length) {
    body.customer_sessions = businessCustomer.normalizeCustomerSessions(params.customerSessions)
  }

  return adapter.request({
    url: url,
    method: 'POST',
    headers: {Authorization:access.headers.Authorization, 'Content-Type': 'application/json' },
    body: body,
    timeout: 30000
  }).then(function (res) {
    access.assertCurrent()
    return res.json().then(function (data) {
      access.assertCurrent()
      access.assertSafeValue(data)
      businessCustomer.ensureNoCustomerSessionEcho(data, params.customerSessions)
      if (data && data.event_type === 'error') {
        throw new Error(data.message || data.detail || 'Unknown error')
      }
      if (!res.ok) {
        throw new Error('/v1/chat/completion failed: ' + (data.message || data.detail || res.status))
      }
      return data
    })
  })
}

function cancelStreaming(params) {
  var access
  try {access=dataAccess.captureVisitorDataAccess(params)} catch (error) {return Promise.reject(error)}
  var apiBase = (params.apiBase || '').replace(/\/$/, '')
  var url = apiBase + '/v1/ai/runs/cancel-by-client'

  return adapter.request({
    url: url,
    method: 'POST',
    headers: {Authorization:access.headers.Authorization, 'Content-Type': 'application/json' },
    body: {
      platform_api_key: params.platformApiKey,
      client_msg_no: params.clientMsgNo,
      reason: params.reason || 'user_cancel'
    },
    timeout: 10000
  }).then(function (res) {
    access.assertCurrent()
    return res.json().then(function (data) {
      access.assertCurrent()
      access.assertSafeValue(data)
      if (!res.ok || !data || data.accepted !== true ||
          data.status !== 'cancelled' || data.client_msg_no !== params.clientMsgNo) {
        throw new Error('尚未确认停止，请稍后重试。')
      }
      return data
    })
  })
}

module.exports = {
  sendChatCompletion: sendChatCompletion,
  cancelStreaming: cancelStreaming
}
