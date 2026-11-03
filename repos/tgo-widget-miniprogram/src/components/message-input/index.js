var i18n = require('../../core/i18n')

Component({
  properties: {
    sessionRevision: {type:Number,value:0},
    isStreaming: {
      type: Boolean,
      value: false
    },
    streamCanceling: {
      type: Boolean,
      value: false
    },
    themeColor: {
      type: String,
      value: '#2f80ed'
    },
    disabled: {
      type: Boolean,
      value: false
    }
  },
  data: {
    inputValue: '',
    sendLabel: '',
    interruptLabel: '',
    placeholder: ''
  },
  lifetimes: {
    attached: function () {
      this._attached = true
      this.setData({
        sendLabel: i18n.t('messageInput.send'),
        interruptLabel: i18n.t('messageInput.interrupt'),
        placeholder: i18n.t('messageInput.placeholder')
      })
    },
    detached:function () {this._attached = false}
  },
  observers: {sessionRevision:function () {this.setData({inputValue:''})}},
  methods: {
    onInput: function (e) {
      this.setData({ inputValue: e.detail.value })
    },

    onConfirm: function () {
      this._doSend()
    },

    onTapSend: function () {
      if (this.properties.disabled) return
      if (this.properties.isStreaming) {
        this.triggerEvent('cancel')
      } else {
        this._doSend()
      }
    },

    _doSend: function () {
      if (this.properties.disabled) return
      var text = (this.data.inputValue || '').trim()
      if (!text) return
      this.setData({ inputValue: '' })
      this.triggerEvent('send', { text: text })
    },

    onChooseImage: function () {
      if (this.properties.disabled) return
      var self = this
      var revision = this.properties.sessionRevision
      wx.chooseMedia({
        count: 1,
        mediaType: ['image'],
        sourceType: ['album', 'camera'],
        success: function (res) {
          if (!self._attached || revision !== self.properties.sessionRevision || self.properties.disabled) return
          var file = res.tempFiles && res.tempFiles[0]
          if (file) {
            self.triggerEvent('image', { tempFilePath: file.tempFilePath })
          }
        }
      })
    }
  }
})
