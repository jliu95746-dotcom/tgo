/**
 * wx storage adapter (replaces localStorage)
 * Supports optional TTL expiry, same format as Web version.
 */

function setJSON(key, value, ttlMs) {
  var rec = { v: value }
  if (ttlMs && ttlMs > 0) {
    rec.e = Date.now() + ttlMs
  }
  try {
    wx.setStorageSync(key, JSON.stringify(rec))
  } catch (e) {
    console.warn('[storage] setJSON failed')
  }
}

function getJSON(key) {
  try {
    var raw = wx.getStorageSync(key)
    if (!raw) return null
    var rec = JSON.parse(raw)
    if (rec && typeof rec === 'object') {
      if (rec.e && Date.now() > rec.e) {
        wx.removeStorageSync(key)
        return null
      }
      return rec.v
    }
  } catch (e) {
    try { wx.removeStorageSync(key) } catch (_) {}
  }
  return null
}

function remove(key) {
  try {
    wx.removeStorageSync(key)
  } catch (e) {}
}

module.exports = { setJSON: setJSON, getJSON: getJSON, remove: remove }
