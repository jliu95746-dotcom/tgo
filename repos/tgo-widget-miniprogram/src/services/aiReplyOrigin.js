var adapter = require('../adapters/request')
var dataAccess = require('./visitorDataAccess')

function verifyAIReplyOrigins(params) {
  var access
  try {access=dataAccess.captureVisitorDataAccess(params)} catch (error) {return Promise.reject(error)}
  return adapter.request({
    url:params.apiBase.replace(/\/+$/,'')+'/v1/ai/runs/reply-origins/verify-by-visitor',
    method:'POST',
    headers:{Authorization:access.headers.Authorization,'Content-Type':'application/json'},
    body:{platform_api_key:params.platformApiKey,channel_id:params.channelId,
      client_msg_nos:params.clientMsgNos},
    timeout:10000
  }).then(function (response) {
    access.assertCurrent()
    if (!response.ok) throw new Error('AI 回复来源核验失败：HTTP '+response.status)
    return response.json().then(function (evidence) {
      access.assertCurrent()
      access.assertSafeValue(evidence)
      if (!Array.isArray(evidence)) throw new Error('AI 回复来源核验结果无效')
      return evidence
    })
  })
}

module.exports={verifyAIReplyOrigins:verifyAIReplyOrigins}
