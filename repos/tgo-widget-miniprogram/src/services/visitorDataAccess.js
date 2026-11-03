var visitor = require('./visitor')
function failure() {return new Error('聊天访问已变化或尚未建立，请重新连接后重试。')}

function captureVisitorDataAccess(options) {
  var cached = visitor.loadCachedVisitor(options.apiBase,options.platformApiKey)
  if (!cached || !cached.visitor_id || typeof cached.im_token!=='string' || !cached.im_token || cached.im_token.length>8192) throw failure()
  if (options.channelId && [cached.channel_id,cached.visitor_id,cached.visitor_id+'-vtr'].indexOf(options.channelId)===-1) throw failure()
  var visitorId=cached.visitor_id,token=cached.im_token
  return Object.freeze({
    headers:Object.freeze({Authorization:'Bearer '+token}),
    assertCurrent:function () {
      var current=visitor.loadCachedVisitor(options.apiBase,options.platformApiKey)
      if (!current || current.visitor_id!==visitorId || current.im_token!==token) throw failure()
    },
    assertSafeValue:function (value) {
      try {if ((JSON.stringify(value) || '').indexOf(token)!==-1) throw failure()}
      catch (error) {throw failure()}
    }
  })
}
module.exports={captureVisitorDataAccess:captureVisitorDataAccess}
