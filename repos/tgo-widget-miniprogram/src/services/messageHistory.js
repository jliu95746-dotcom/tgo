/**
 * Message history sync service
 * API: POST /v1/visitors/messages/sync
 */
var adapter = require('../adapters/request')
var dataAccess = require('./visitorDataAccess')

function syncVisitorMessages(params) {
  var access
  try {access=dataAccess.captureVisitorDataAccess(params)} catch (error) {return Promise.reject(error)}
  var apiBase = (params.apiBase || '').replace(/\/$/, '')
  var platformApiKey = params.platformApiKey || ''
  var url = apiBase + '/v1/visitors/messages/sync'

  var body = {
    platform_api_key: platformApiKey,
    channel_id: params.channelId,
    channel_type: params.channelType,
    start_message_seq: params.startSeq != null ? params.startSeq : null,
    end_message_seq: params.endSeq != null ? params.endSeq : null,
    limit: params.limit != null ? params.limit : null,
    pull_mode: params.pullMode != null ? params.pullMode : null
  }

  return adapter.request({
    url: url,
    method: 'POST',
    headers: {
      Authorization:access.headers.Authorization,
      'Content-Type': 'application/json',
      'X-Platform-API-Key': platformApiKey
    },
    body: body,
    timeout: 10000
  }).then(function (res) {
    access.assertCurrent()
    if (!res.ok) {
      throw new Error('历史读取失败（HTTP '+res.status+'），请重新连接后重试。')
    }
    return res.json().then(function (data) {
      access.assertCurrent()
      access.assertSafeValue(data)
      if (!data || !Array.isArray(data.messages)) {
        throw new Error('[History] invalid response')
      }
      return data
    })
  })
}

module.exports = { syncVisitorMessages: syncVisitorMessages }
