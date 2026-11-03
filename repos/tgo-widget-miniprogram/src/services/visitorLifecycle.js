/** Retain only an end grant while old readable visitor access is removed. */
var storage = require('../adapters/storage')
var adapter = require('../adapters/request')
var visitor = require('./visitor')
var unknown = {accessEnded:false,upstreamStopStatus:'pending',imDisconnectStatus:'pending'}

function key(options) { return 'tgo:visitor-ends:'+options.apiBase+':'+options.platformApiKey }
function pending(options) {
  var value=storage.getJSON(key(options))
  if (!Array.isArray(value)) return []
  return value.slice(0,20).filter(function (item) {
    return item && typeof item==='object' && Object.keys(item).length===2
      && typeof item.visitor_id==='string' && item.visitor_id
      && typeof item.visitor_end_token==='string' && item.visitor_end_token && item.visitor_end_token.length<=8192
  })
}
function submit(options, identity) {
  return adapter.request({url:options.apiBase.replace(/\/$/,'')+'/v1/visitors/access/end',
    method:'POST',headers:{'Content-Type':'application/json'},timeout:8000,
    body:{platform_api_key:options.platformApiKey,visitor_id:identity.visitor_id,visitor_end_token:identity.visitor_end_token}
  }).then(function (response) {
    if (!response.ok) return unknown
    return response.json().then(function (data) {
      if (!data || typeof data!=='object' || Array.isArray(data) || Object.keys(data).length!==3
        || data.status!=='ended' || ['confirmed','pending'].indexOf(data.upstream_stop_status)===-1
        || ['requested','pending'].indexOf(data.im_disconnect_status)===-1) return unknown
      var outcome={accessEnded:true,upstreamStopStatus:data.upstream_stop_status,imDisconnectStatus:data.im_disconnect_status}
      if (outcome.upstreamStopStatus==='confirmed' && outcome.imDisconnectStatus==='requested') {
        storage.setJSON(key(options),pending(options).filter(function (item) {return item.visitor_id!==identity.visitor_id}))
      }
      return outcome
    })
  }).catch(function () {return unknown})
}
function endCachedVisitorAccess(options) {
  try {
    var cached=visitor.loadCachedVisitor(options.apiBase,options.platformApiKey)
    if (!cached) return Promise.resolve(unknown)
    visitor.removeCachedVisitor(options.apiBase,options.platformApiKey)
    if (visitor.loadCachedVisitor(options.apiBase,options.platformApiKey)!==null) throw new Error('旧会话访问缓存无法清理，请先清理存储。')
    if (typeof cached.visitor_end_token!=='string' || !cached.visitor_end_token || cached.visitor_end_token.length>8192) return Promise.resolve(unknown)
    return endIssuedVisitorAccess(options,{id:cached.visitor_id,visitor_end_token:cached.visitor_end_token})
  } catch (error) {return Promise.reject(error)}
}
function endIssuedVisitorAccess(options,issued) {
  try {
    if (typeof issued.visitor_end_token!=='string' || !issued.visitor_end_token || issued.visitor_end_token.length>8192) return Promise.resolve(unknown)
    var identity={visitor_id:issued.id,visitor_end_token:issued.visitor_end_token}
    var previous=pending(options).filter(function (item) {return item.visitor_id!==identity.visitor_id})
    if (previous.length>=20) throw new Error('旧会话退出尚待确认，请先重试退出。')
    storage.setJSON(key(options),previous.concat([identity]))
    if (!pending(options).some(function (item) {return item.visitor_id===identity.visitor_id && item.visitor_end_token===identity.visitor_end_token})) {
      throw new Error('退出重试凭据无法保存，请检查存储。')
    }
    return submit(options,identity)
  } catch (error) {return Promise.reject(error)}
}
function retryPendingVisitorEnds(options) {
  var results=[],chain=Promise.resolve()
  pending(options).forEach(function (identity) {
    chain=chain.then(function () {return submit(options,identity)}).then(function (result) {results.push(result)})
  })
  return chain.then(function () {return results})
}
module.exports={endCachedVisitorAccess:endCachedVisitorAccess,endIssuedVisitorAccess:endIssuedVisitorAccess,retryPendingVisitorEnds:retryPendingVisitorEnds}
