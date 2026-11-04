var adapter = require('../adapters/request')
var dataAccess = require('./visitorDataAccess')
var privateFiles = /(?:https?:\/\/[^\s<>()"']+)?(?:\/api)?\/v1\/chat\/files\/[a-f\d-]{36}(?:\?[^\s<>()"']*)?/gi

function isPrivateChatFile(url) {
  return /\/v1\/chat\/files\/([a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12})(?:\?|$)/i.test(url || '')
}

function readChatFileAccess(url, options) {
  var match = (url || '').match(/\/v1\/chat\/files\/([a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12})(?:\?|$)/i)
  if (!match) return Promise.resolve({url:url})
  if (!options.apiBase || !options.platformApiKey) return Promise.resolve({url:''})
  var access
  try {access=dataAccess.captureVisitorDataAccess(options)} catch (error) {return Promise.reject(error)}
  var endpoint = options.apiBase.replace(/\/+$/, '')+'/v1/chat/files/'+match[1]
  return adapter.request({url:endpoint+'/access',method:'POST',timeout:10000,
    headers:{Authorization:access.headers.Authorization,'X-Platform-API-Key':options.platformApiKey}}).then(function (response) {
    access.assertCurrent()
    if (!response.ok) throw new Error('附件访问失败，请重新连接后重试。')
    return response.json().then(function (data) {
      access.assertCurrent()
      access.assertSafeValue(data)
      var prefix = endpoint+'?file_token='
      var expiry = data && typeof data.expires_at==='string' ? Date.parse(data.expires_at) : NaN
      if (!data || typeof data.access_url!=='string' || data.access_url.indexOf(prefix)!==0 ||
          !/^[A-Za-z0-9_.-]{1,16384}$/.test(data.access_url.slice(prefix.length)) || !isFinite(expiry) || expiry<=Date.now()) {
        throw new Error('附件链接未通过安全检查。')
      }
      return {url:data.access_url,expiresAt:expiry}
    })
  })
}

function authorizeChatFileUrl(url, options) {
  return readChatFileAccess(url,options).then(function (result) {return result.url})
}

function maskPrivateChatMarkdown(content) {return (content || '').replace(privateFiles,'')}

function authorizeChatMarkdown(content, options) {
  var urls = (content || '').match(privateFiles) || []
  return Promise.all(urls.map(function (url) {
    return authorizeChatFileUrl(url,options).catch(function () {return ''})
  })).then(function (links) {
    return (content || '').replace(privateFiles,function (url) {return links[urls.indexOf(url)] || ''})
  })
}

function maskPrivateChatStructured(value) {
  var changed = false
  function visit(item, depth) {
    if (typeof item==='string') {
      var masked = maskPrivateChatMarkdown(item)
      if (masked!==item) changed=true
      return masked
    }
    if (item===null || typeof item!=='object') return item
    if (depth>32) {changed=true;return null}
    if (Array.isArray(item)) return item.map(function (entry) {return visit(entry,depth+1)})
    var result={}
    Object.keys(item).forEach(function (key) {
      if (key==='__proto__' || key==='constructor' || key==='prototype') return
      result[key]=visit(item[key],depth+1)
    })
    return result
  }
  return {value:visit(value,0),changed:changed}
}

function authorizeChatStructured(value, options) {
  function visit(item, depth) {
    if (typeof item==='string') {
      return maskPrivateChatMarkdown(item)===item ? Promise.resolve(item) : authorizeChatMarkdown(item,options)
    }
    if (item===null || typeof item!=='object') return Promise.resolve(item)
    if (depth>32) return Promise.resolve(null)
    if (Array.isArray(item)) return Promise.all(item.map(function (entry) {return visit(entry,depth+1)}))
    var keys=Object.keys(item).filter(function (key) {
      return key!=='__proto__' && key!=='constructor' && key!=='prototype'
    })
    return Promise.all(keys.map(function (key) {return visit(item[key],depth+1)})).then(function (values) {
      var result={}
      keys.forEach(function (key,index) {result[key]=values[index]})
      return result
    })
  }
  return visit(value,0)
}

module.exports={readChatFileAccess:readChatFileAccess,authorizeChatFileUrl:authorizeChatFileUrl,
  isPrivateChatFile:isPrivateChatFile,maskPrivateChatMarkdown:maskPrivateChatMarkdown,authorizeChatMarkdown:authorizeChatMarkdown,
  maskPrivateChatStructured:maskPrivateChatStructured,authorizeChatStructured:authorizeChatStructured}
