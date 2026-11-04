/**
 * <tgo-chat> main component
 *
 * Usage:
 *   <tgo-chat api-key="ak_live_xxx" api-base="https://your-api.com" />
 */
var chatStore = require('../core/chatStore')
var platformStore = require('../core/platformStore')
var i18n = require('../core/i18n')

Component({
  options: {
    multipleSlots: true
  },

  properties: {
    apiKey: {
      type: String,
      value: ''
    },
    apiBase: {
      type: String,
      value: ''
    },
    themeColor: {
      type: String,
      value: ''
    },
    lang: {
      type: String,
      value: 'zh'
    },
    visitorName: {
      type: String,
      value: ''
    },
    visitorAvatar: {
      type: String,
      value: ''
    },
    customAttrs: {
      type: Object,
      value: null
    }
  },

  data: {
    messages: [],
    isStreaming: false,
    streamCanceling: false,
    online: false,
    historyLoading: false,
    historyHasMore: true,
    initializing: false,
    error: null,
    aiDisclosure: '',
    humanRequestLabel: '',
    aiLabel: '',
    voicePlayLabel: '',
    voiceStopLabel: '',
    voiceUnavailableLabel: '',
    handoffSending: false,
    sessionRevision: 0,
    resolvedThemeColor: '#2f80ed',
    title: 'Tgo',
    logoUrl: ''
  },

  lifetimes: {
    attached: function () {
      this._attached = true
      this._hidden = false
      this._startRevision = 0
      this._sessionOwner = chatStore.claimSessionOwner()
      // Set i18n language
      i18n.setLang(this.properties.lang)
      this._updateServiceNotice()

      // Subscribe to stores
      this._bindStores()

      // Initialize
      this._init()
    },

    detached: function () {
      this._attached = false
      this._startRevision += 1
      if (this._sessionOwner) Promise.resolve(this._sessionOwner.release()).catch(function () {})
      if (this._releaseBusinessCustomer) { this._releaseBusinessCustomer(); this._releaseBusinessCustomer = null }
      if (this._unsubChat) { this._unsubChat(); this._unsubChat = null }
      if (this._unsubPlatform) { this._unsubPlatform(); this._unsubPlatform = null }
    }
  },

  pageLifetimes: {
    hide:function () {
      this._hidden = true
      this._startRevision += 1
      if (this._ownsSession()) Promise.resolve(chatStore.endSession()).catch(function () {})
    },
    show:function () {this._hidden = false;if (this._ownsSession()) this._init()}
  },

  observers: {
    'lang':function () {this._updateServiceNotice()},
    'apiBase, apiKey':function () {
      if (!this._ownsSession()) return
      this._startRevision += 1
      var self = this
      var revision = this._startRevision
      Promise.resolve(chatStore.endSession()).then(function () {
        if (self._ownsSession() && revision === self._startRevision && !self._hidden) self._init()
      }).catch(function () {if (self._ownsSession()) self.setData({error:'旧会话退出未完成，请重试。'})})
    }
  },

  methods: {
    _updateServiceNotice:function () {
      i18n.setLang(this.properties.lang)
      this.setData({
        aiDisclosure:i18n.t('customerService.aiDisclosure'),
        humanRequestLabel:i18n.t(this.data.handoffSending ? 'customerService.requestingHuman' : 'customerService.requestHuman'),
        aiLabel:i18n.t('messageList.aiLabel'),
        voicePlayLabel:i18n.t('voice.play'),
        voiceStopLabel:i18n.t('voice.stop'),
        voiceUnavailableLabel:i18n.t('voice.unavailable')
      })
    },
    _ownsSession:function () {return !!(this._attached && this._sessionOwner && this._sessionOwner.isCurrent())},
    setBusinessCustomerSessionProvider: function (provider) {
      if (!this._ownsSession()) throw new Error('当前聊天组件尚未就绪或已被替换。')
      if (provider != null && typeof provider !== 'function') throw new Error('客户业务身份回调必须为函数或 null。')
      this._releaseBusinessCustomer = chatStore.setBusinessCustomerSessionProvider(provider)
    },
    notifyBusinessCustomerChanged:function () {
      if (!this._ownsSession()) return Promise.resolve()
      return chatStore.notifyBusinessCustomerChanged()
    },
    _bindStores: function () {
      var self = this

      // Subscribe to chat store
      this._unsubChat = chatStore.subscribe(function (state) {
        if (!self._ownsSession()) return
        self.setData({
          messages: state.messages,
          isStreaming: state.isStreaming,
          streamCanceling: state.streamCanceling,
          online: state.online,
          historyLoading: state.historyLoading,
          historyHasMore: state.historyHasMore,
          initializing: state.initializing,
          error: state.error,
          sessionRevision: state.sessionRevision
        })
      })

      // Subscribe to platform store
      this._unsubPlatform = platformStore.subscribe(function (state) {
        if (!self._ownsSession() || state._apiBase !== self.properties.apiBase || state._platformApiKey !== self.properties.apiKey) return
        var cfg = state.config || {}
        self.setData({
          title: cfg.widget_title || 'Tgo',
          logoUrl: cfg.logo_url || '',
          resolvedThemeColor: self.properties.themeColor || cfg.theme_color || '#2f80ed'
        })

        // Inject welcome message
        if (cfg.welcome_message && !state.welcomeInjected) {
          chatStore.ensureWelcomeMessage(cfg.welcome_message)
          platformStore.markWelcomeInjected()
        }
      })
    },

    _init: function () {
      if (!this._ownsSession() || this._hidden) return Promise.resolve()
      var self = this
      var revision = ++this._startRevision
      var apiBase = this.properties.apiBase
      var apiKey = this.properties.apiKey

      if (!apiBase || !apiKey) {
        console.error('[tgo-chat] api-base and api-key are required')
        return
      }

      // Init platform config
      platformStore.init(apiBase, apiKey)

      // Set resolved theme color
      this.setData({
        resolvedThemeColor: this.properties.themeColor || '#2f80ed'
      })

      // Init chat IM
      return Promise.resolve(this._sessionOwner.ready).then(function () {
        if (!self._ownsSession() || self._hidden || revision !== self._startRevision) return
        return chatStore.initIM({apiBase:apiBase,platformApiKey:apiKey})
      }).catch(function () {
        if (self._ownsSession() && revision === self._startRevision) self.setData({error:'聊天初始化失败，请重试。'})
      })
    },

    // Event handlers from child components
    onSendMessage: function (e) {
      if (!this._ownsSession() || this._hidden) return
      var text = e.detail && e.detail.text
      if (text) {
        chatStore.sendMessage(text)
      }
    },

    onRequestHuman: function () {
      if (!this._ownsSession() || this._hidden || !this.data.online || this.data.initializing || this.data.handoffSending) return
      var self = this
      this.setData({handoffSending:true,humanRequestLabel:i18n.t('customerService.requestingHuman')})
      var done = function () {
        if (!self._ownsSession()) return
        self.setData({handoffSending:false,humanRequestLabel:i18n.t('customerService.requestHuman')})
      }
      Promise.resolve(chatStore.sendMessage('转人工')).then(done,done)
    },

    onCancelStream: function () {
      if (!this._ownsSession() || this._hidden) return
      chatStore.cancelStreaming('user_cancel')
    },

    onLoadMore: function () {
      if (!this._ownsSession() || this._hidden) return
      if (!chatStore.getState().historyLoading && chatStore.getState().historyHasMore) {
        chatStore.loadMoreHistory(20)
      }
    },

    onChooseImage: function (e) {
      if (!this._ownsSession() || this._hidden) return
      var tempFilePath = e.detail && e.detail.tempFilePath
      if (tempFilePath) {
        chatStore.uploadImage(tempFilePath)
      }
    }
  }
})
