/**
 * Simple i18n module (zh/en)
 */
var locales = {
  zh: {
    'customerService.aiDisclosure': '本窗口支持 AI 客服回复，您也可以请求人工服务。',
    'customerService.requestHuman': '转人工',
    'customerService.requestingHuman': '请求中…',
    'messageList.aiLabel': 'AI 客服',
    'common.retry': '重试',
    'common.cancel': '取消',
    'common.loading': '加载中…',
    'messageInput.placeholder': '提出问题...',
    'messageInput.send': '发送',
    'messageInput.interrupt': '中断',
    'messageList.loadingHistory': '加载中…',
    'messageList.noMoreMessages': '没有更多消息',
    'messageList.sending': '发送中…',
    'messageList.uploading': '上传中…',
    'voice.play': '播放语音',
    'voice.stop': '停止播放',
    'voice.unavailable': '语音暂时无法播放，请重试。',
    'errors.authFail': '认证失败，无法发送消息',
    'errors.networkError': '网络异常或超时，请检查网络后重试',
    'errors.systemError': '系统错误，请稍后重试',
    'system.staffAssigned': '已为您接入人工客服，客服 {name0} 为您服务。',
    'system.sessionTransferred': '会话已转接。客服 {name0} 已将您转接给客服 {name1}。',
    'system.sessionClosed': '会话已结束。客服 {name0} 已完成本次服务。',
    'system.sessionClosedNoAgent': '会话已结束。'
  },
  en: {
    'customerService.aiDisclosure': 'AI support may reply in this chat. You can request a human agent.',
    'customerService.requestHuman': 'Human agent',
    'customerService.requestingHuman': 'Requesting…',
    'messageList.aiLabel': 'AI support',
    'common.retry': 'Retry',
    'common.cancel': 'Cancel',
    'common.loading': 'Loading...',
    'messageInput.placeholder': 'Ask a question...',
    'messageInput.send': 'Send',
    'messageInput.interrupt': 'Stop',
    'messageList.loadingHistory': 'Loading...',
    'messageList.noMoreMessages': 'No more messages',
    'messageList.sending': 'Sending...',
    'messageList.uploading': 'Uploading...',
    'voice.play': 'Play voice',
    'voice.stop': 'Stop playback',
    'voice.unavailable': 'Voice is unavailable. Please try again.',
    'errors.authFail': 'Authentication failed, cannot send message',
    'errors.networkError': 'Network error or timeout, please check your connection',
    'errors.systemError': 'System error, please try again later',
    'system.staffAssigned': 'You have been connected to customer service. Agent {name0} will assist you.',
    'system.sessionTransferred': 'Session transferred. Agent {name0} has transferred you to Agent {name1}.',
    'system.sessionClosed': 'Session ended. Agent {name0} has completed the service.',
    'system.sessionClosedNoAgent': 'Session ended.'
  }
}

var currentLang = 'zh'

function setLang(lang) {
  currentLang = (lang === 'en') ? 'en' : 'zh'
}

function getLang() {
  return currentLang
}

function t(key, params) {
  var dict = locales[currentLang] || locales.zh
  var str = dict[key] || key
  if (params) {
    Object.keys(params).forEach(function (k) {
      str = str.replace(new RegExp('\\{' + k + '\\}', 'g'), params[k])
    })
  }
  return str
}

module.exports = { setLang: setLang, getLang: getLang, t: t }
