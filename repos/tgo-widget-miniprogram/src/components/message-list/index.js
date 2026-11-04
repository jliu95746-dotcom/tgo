var types = require('../../core/types')
var timeUtil = require('../../utils/time')
var fileAccess = require('../../services/chatFileAccess')
var aiReplyOrigin = require('../../services/aiReplyOrigin')

Component({
  properties: {
    messages: {
      type: Array,
      value: []
    },
    apiBase: {type:String,value:''},
    platformApiKey: {type:String,value:''},
    sessionRevision: {type:Number,value:0},
    historyLoading: {
      type: Boolean,
      value: false
    },
    historyHasMore: {
      type: Boolean,
      value: true
    },
    isStreaming: {
      type: Boolean,
      value: false
    },
    themeColor: {
      type: String,
      value: '#2f80ed'
    },
    aiLabel: {
      type: String,
      value: 'AI 客服'
    },
    voicePlayLabel: {type:String,value:'播放语音'},
    voiceStopLabel: {type:String,value:'停止播放'},
    voiceUnavailableLabel: {type:String,value:'语音暂时无法播放，请重试。'}
  },

  data: {
    displayMessages: [],
    scrollTop: 0,
    scrollAnimated: true,
    _prevLen: 0,
    _scrollCounter: 0,
    playingVoiceId: '',
    voiceErrorId: ''
  },

  lifetimes: {
    attached: function () {
      this._detached = false
      if (this.properties.messages.length) {
        this._processMessages(this.properties.messages)
      }
    },
    detached: function () {
      this._detached = true
      this._stopVoice()
      this._fileRevision = (this._fileRevision || 0)+1
      if (this._fileTimer) clearTimeout(this._fileTimer)
    }
  },
  pageLifetimes: {
    hide: function () { this._stopVoice() }
  },
  observers: {
    'messages, apiBase, platformApiKey, sessionRevision, aiLabel':function () {
      this._processMessages(this.properties.messages)
    }
  },

  methods: {
    _processMessages: function (messages) {
      if (this._detached) return
      var self = this
      var voiceContext = JSON.stringify([this.properties.apiBase,this.properties.platformApiKey,this.properties.sessionRevision])
      if (this._voiceContext !== voiceContext) {
        this._voiceContext = voiceContext
        this._stopVoice()
      }
      if (this._voicePlayingId && !(messages || []).some(function (m) { return m.id === self._voicePlayingId })) {
        this._stopVoice()
      }
      var revision = this._fileRevision = (this._fileRevision || 0)+1
      if (this._fileTimer) clearTimeout(this._fileTimer)
      var options = {apiBase:this.properties.apiBase,platformApiKey:this.properties.platformApiKey}
      var sessionRevision = this.properties.sessionRevision
      var originContext = JSON.stringify([options.apiBase,options.platformApiKey,sessionRevision])
      if (this._originContext!==originContext) {
        this._originContext=originContext
        this._originVerified=Object.create(null)
        this._originChecked=Object.create(null)
        this._originPending=Object.create(null)
      }
      var originCandidates=Object.create(null)
      var pending = []
      var expiries = []
      var prevLen = this.data._prevLen
      var list = (messages || []).map(function (m) {
        var isSystem = m.payload && types.isSystemMessageType(m.payload.type)
        var hasUiParts = Array.isArray(m.uiParts) && m.uiParts.length > 0
        var safeUiParts = hasUiParts ? fileAccess.maskPrivateChatStructured(m.uiParts) : null
        var payload = Object.assign({},m.payload || {})
        var row = {
          id: m.id,
          role: m.role,
          payload: payload,
          streamData: fileAccess.maskPrivateChatMarkdown(m.streamData),
          errorMessage: m.errorMessage,
          status: m.status,
          uploadProgress: m.uploadProgress,
          isSystem: isSystem,
          isSelf: m.role === 'user',
          senderKind: '',
          _time: m.time ? timeUtil.formatMessageTime(m.time) : '',
          uiParts: safeUiParts ? safeUiParts.value : null,
          hasUiParts: hasUiParts
        }
        if (safeUiParts && safeUiParts.changed) {
          expiries.push(Date.now()+65000)
          pending.push(fileAccess.authorizeChatStructured(m.uiParts,options).then(function (parts) {
            row.uiParts=parts
          }).catch(function () {row.uiParts=safeUiParts.value}))
        }
        var aiCandidate=m.role==='agent' && m.senderKind==='ai' &&
          m.channelType===251 && m.channelId && m.clientMsgNo
        if (aiCandidate) {
          var originKey=JSON.stringify([m.channelId,m.clientMsgNo])
          if (self._originVerified[originKey]) row.senderKind='ai'
          if (!self._originChecked[originKey] && !self._originPending[originKey]) {
            if (!originCandidates[m.channelId]) originCandidates[m.channelId]=[]
            if (originCandidates[m.channelId].indexOf(m.clientMsgNo)===-1) {
              originCandidates[m.channelId].push(m.clientMsgNo)
            }
            self._originPending[originKey]=true
          }
        }
        if (payload.type === types.MSG_TYPE_VOICE) {
          // Resolve the source only when tapped; do not retain audio links in setData.
          payload.url = ''
          payload.content = ''
        } else if (fileAccess.isPrivateChatFile(payload.url)) {
          var originalUrl = payload.url
          payload.url = ''
          pending.push(fileAccess.readChatFileAccess(originalUrl,options).then(function (result) {
            payload.url = result.url
            if (result.expiresAt) expiries.push(result.expiresAt)
          }).catch(function () {payload.url=''}))
        }
        if (typeof payload.content==='string') {
          var originalContent = payload.content
          payload.content = fileAccess.maskPrivateChatMarkdown(originalContent)
          if (payload.content !== originalContent) {
            expiries.push(Date.now()+65000)
            pending.push(fileAccess.authorizeChatMarkdown(originalContent,options).then(function (value) {payload.content=value}))
          }
        }
        if (row.streamData !== (m.streamData || '')) {
          expiries.push(Date.now()+65000)
          pending.push(fileAccess.authorizeChatMarkdown(m.streamData,options).then(function (value) {row.streamData=value}))
        }
        return row
      })

      var shouldScroll = false
      if (list.length > prevLen) {
        shouldScroll = true
      } else if (list.length > 0) {
        var last = list[list.length - 1]
        if (last.streamData) {
          shouldScroll = true
        }
      }

      var setObj = {
        displayMessages: list,
        _prevLen: list.length
      }

      if (shouldScroll && list.length > 0) {
        // Toggle between two large values to force scroll-top re-trigger
        var c = this.data._scrollCounter + 1
        setObj.scrollTop = 999999 + (c % 2)
        setObj._scrollCounter = c
        setObj.scrollAnimated = !last || !last.streamData
      }

      this.setData(setObj)
      Object.keys(originCandidates).forEach(function (channelId) {
        var clientNos=originCandidates[channelId]
        for (var start=0;start<clientNos.length;start+=50) {
          (function (batch) {
            aiReplyOrigin.verifyAIReplyOrigins({apiBase:options.apiBase,
              platformApiKey:options.platformApiKey,channelId:channelId,
              clientMsgNos:batch}).then(function (evidence) {
              if (self._detached || self._originContext!==originContext) return
              batch.forEach(function (clientNo) {
                var key=JSON.stringify([channelId,clientNo])
                self._originChecked[key]=true
                delete self._originPending[key]
              })
              evidence.forEach(function (item) {
                if (item.status==='verified' && batch.indexOf(item.client_msg_no)!==-1) {
                  self._originVerified[JSON.stringify([channelId,item.client_msg_no])]=true
                }
              })
              self._processMessages(self.properties.messages)
            }).catch(function () {
              if (self._detached || self._originContext!==originContext) return
              batch.forEach(function (clientNo) {
                var key=JSON.stringify([channelId,clientNo])
                self._originChecked[key]=true
                delete self._originPending[key]
              })
            })
          })(clientNos.slice(start,start+50))
        }
      })
      if (pending.length) Promise.all(pending).then(function () {
        if (self._detached || self._fileRevision!==revision || self.properties.sessionRevision!==sessionRevision ||
            self.properties.apiBase!==options.apiBase || self.properties.platformApiKey!==options.platformApiKey) return
        self.setData({displayMessages:list})
        if (expiries.length) self._fileTimer=setTimeout(function () {
          if (!self._detached && self._fileRevision===revision) self._processMessages(self.properties.messages)
        },Math.max(1000,Math.min(240000,Math.min.apply(Math,expiries)-Date.now()-5000)))
      }).catch(function () {})
    },

    onScrollToUpper: function () {
      this.triggerEvent('loadmore')
    },

    _stopVoice: function () {
      this._voiceRequestRevision = (this._voiceRequestRevision || 0) + 1
      var player = this._voicePlayer
      this._voicePlayer = null
      this._voicePlayingId = ''
      if (player) {
        try { player.stop() } catch (error) {}
        try { player.destroy() } catch (error) {}
      }
      if (!this._detached && (this.data.playingVoiceId || this.data.voiceErrorId)) {
        this.setData({playingVoiceId:'',voiceErrorId:''})
      }
    },

    onTapVoice: function (event) {
      if (this._detached) return
      var id = event.currentTarget.dataset.id
      var messages = this.properties.messages || []
      var originalUrl = ''
      for (var i=0; i<messages.length; i++) {
        if (messages[i].id === id && messages[i].payload && messages[i].payload.type === types.MSG_TYPE_VOICE) {
          originalUrl = messages[i].payload.url
          break
        }
      }
      if (!originalUrl) return
      if (this._voicePlayingId === id) { this._stopVoice(); return }
      this._stopVoice()
      var self = this
      var requestRevision = this._voiceRequestRevision
      var voiceContext = this._voiceContext
      var options = {apiBase:this.properties.apiBase,platformApiKey:this.properties.platformApiKey}
      var sessionRevision = this.properties.sessionRevision
      function isCurrent() {
        if (self._detached || self._voiceRequestRevision !== requestRevision || self._voiceContext !== voiceContext ||
            self.properties.sessionRevision !== sessionRevision || self.properties.apiBase !== options.apiBase ||
            self.properties.platformApiKey !== options.platformApiKey) return false
        var current = self.properties.messages || []
        for (var j=0; j<current.length; j++) {
          if (current[j].id === id && current[j].payload && current[j].payload.type === types.MSG_TYPE_VOICE &&
              current[j].payload.url === originalUrl) return true
        }
        return false
      }
      fileAccess.authorizeChatFileUrl(originalUrl,options).then(function (currentUrl) {
        if (!isCurrent()) return
        if (!/^https?:\/\//i.test(currentUrl || '') || typeof wx.createInnerAudioContext !== 'function') {
          throw new Error('voice unavailable')
        }
        var player = wx.createInnerAudioContext()
        self._voicePlayer = player
        self._voicePlayingId = id
        player.onEnded(function () { if (self._voicePlayer === player) self._stopVoice() })
        player.onStop(function () { if (self._voicePlayer === player) self._stopVoice() })
        player.onError(function () {
          if (self._voicePlayer !== player) return
          self._stopVoice()
          if (!self._detached) self.setData({voiceErrorId:id})
        })
        player.src = currentUrl
        self.setData({playingVoiceId:id,voiceErrorId:''})
        player.play()
      }).catch(function () {
        if (!isCurrent()) return
        self._stopVoice()
        if (!self._detached) self.setData({voiceErrorId:id})
      })
    },

    onTapImage: function (e) {
      var url = e.currentTarget.dataset.url
      var self = this
      var revision = this._fileRevision
      if (url && !this._detached) fileAccess.authorizeChatFileUrl(url,{
        apiBase:this.properties.apiBase,platformApiKey:this.properties.platformApiKey
      }).then(function (currentUrl) {
        if (!currentUrl || self._detached || self._fileRevision!==revision) return
        wx.previewImage({
          current: currentUrl,
          urls: [currentUrl]
        })
      }).catch(function () {})
    },

    onJsonRenderSend: function (e) {
      this.triggerEvent('sendmessage', e.detail)
    }
  }
})
