/**
 * File upload service using wx.uploadFile
 * API: POST /v1/chat/upload
 */
var dataAccess = require('./visitorDataAccess')

function uploadChatFile(params) {
  var apiBase = (params.apiBase || '').replace(/\/$/, '')
  var apiKey = params.apiKey || ''
  var channelId = params.channelId || ''
  var channelType = params.channelType || 251
  var filePath = params.filePath
  var onProgress = params.onProgress

  return new Promise(function (resolve, reject) {
    var access = dataAccess.captureVisitorDataAccess({apiBase:apiBase,platformApiKey:apiKey,channelId:channelId})
    var task = wx.uploadFile({
      url: apiBase + '/v1/chat/upload',
      filePath: filePath,
      name: 'file',
      header: {
        Authorization:access.headers.Authorization,
        'X-Platform-API-Key': apiKey
      },
      formData: {
        channel_id: channelId,
        channel_type: String(channelType)
      },
      success: function (res) {
        try {access.assertCurrent()} catch (error) {reject(error);return}
        if (res.statusCode >= 200 && res.statusCode < 300) {
          try {
            var data = JSON.parse(res.data)
            access.assertSafeValue(data)
            if (data.channel_id !== channelId || data.channel_type !== channelType) {
              return reject(new Error('附件会话不匹配，请重新连接。'))
            }
            if (!data || !data.file_id) {
              return reject(new Error('[Upload] invalid response'))
            }
            resolve(data)
          } catch (e) {
            reject(new Error('[Upload] invalid JSON response'))
          }
        } else {
          reject(new Error('[Upload] failed: ' + res.statusCode))
        }
      },
      fail: function (err) {
        void err
        reject(new Error('上传失败，请稍后重试。'))
      }
    })

    if (task && params.onTask) params.onTask(task)
    if (onProgress && task) {
      task.onProgressUpdate(function (r) {
        try {access.assertCurrent()} catch (error) {task.abort();reject(error);return}
        onProgress(r.progress)
      })
    }
  })
}

/**
 * Get image dimensions using wx.getImageInfo
 */
function getImageInfo(filePath) {
  return new Promise(function (resolve) {
    wx.getImageInfo({
      src: filePath,
      success: function (res) {
        resolve({ width: res.width, height: res.height })
      },
      fail: function () {
        resolve(null)
      }
    })
  })
}

module.exports = {
  uploadChatFile: uploadChatFile,
  getImageInfo: getImageInfo
}
