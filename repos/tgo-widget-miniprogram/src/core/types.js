/**
 * Message type constants and helper functions
 */

// Payload types
var MSG_TYPE_TEXT = 1
var MSG_TYPE_IMAGE = 2
var MSG_TYPE_FILE = 3
var MSG_TYPE_VOICE = 4
var MSG_TYPE_MIXED = 12
var MSG_TYPE_COMMAND = 99
var MSG_TYPE_AI_LOADING = 100

// System message types (1000-2000)
function isSystemMessageType(type) {
  return typeof type === 'number' && type >= 1000 && type <= 2000
}

/**
 * Try to parse a string as JSON, falling back to base64 decode + JSON parse.
 */
function tryParsePayloadString(str) {
  try { return JSON.parse(str) } catch (e) {}
  // Mini program doesn't have atob, use wx base64 decode
  try {
    var buf = wx.base64ToArrayBuffer(str)
    var text = String.fromCharCode.apply(null, new Uint8Array(buf))
    return JSON.parse(text)
  } catch (e) {}
  return null
}

/**
 * Normalize raw payload to structured format
 */
function toPayloadFromAny(raw) {
  if (typeof raw === 'string') {
    var parsed = tryParsePayloadString(raw)
    if (parsed && typeof parsed === 'object') return toPayloadFromAny(parsed)
    return { type: MSG_TYPE_TEXT, content: raw }
  }
  if (!raw) return { type: MSG_TYPE_TEXT, content: '' }

  var t = raw.type
  if (t === MSG_TYPE_TEXT && typeof raw.content === 'string') {
    return { type: MSG_TYPE_TEXT, content: raw.content }
  }
  if (t === MSG_TYPE_IMAGE && typeof raw.url === 'string') {
    return { type: MSG_TYPE_IMAGE, url: raw.url, width: raw.width || 0, height: raw.height || 0 }
  }
  if (t === MSG_TYPE_FILE && typeof raw.url === 'string' && typeof raw.name === 'string') {
    return { type: MSG_TYPE_FILE, content: raw.content || '[文件]', url: raw.url, name: raw.name, size: raw.size || 0 }
  }
  if (t === MSG_TYPE_VOICE) {
    var voiceUrl = typeof raw.url === 'string' && raw.url ? raw.url : raw.content
    if (typeof voiceUrl === 'string' && voiceUrl) {
      return { type: MSG_TYPE_VOICE, url: voiceUrl, content: typeof raw.content === 'string' ? raw.content : '' }
    }
  }
  if (t === MSG_TYPE_MIXED && typeof raw.content === 'string' && Array.isArray(raw.images)) {
    var images = raw.images
      .filter(function (i) { return i && typeof i.url === 'string' })
      .map(function (i) { return { url: i.url, width: i.width || 0, height: i.height || 0 } })
    return { type: MSG_TYPE_MIXED, content: raw.content, images: images }
  }
  if (t === MSG_TYPE_COMMAND && typeof raw.cmd === 'string') {
    return { type: MSG_TYPE_COMMAND, cmd: raw.cmd, param: raw.param || {} }
  }
  if (t === MSG_TYPE_AI_LOADING) {
    return raw.sender_kind === 'ai'
      ? { type: MSG_TYPE_AI_LOADING, sender_kind: 'ai' }
      : { type: MSG_TYPE_AI_LOADING }
  }
  if (isSystemMessageType(t) && typeof raw.content === 'string') {
    return { type: t, content: raw.content, extra: Array.isArray(raw.extra) ? raw.extra : undefined }
  }
  return { type: MSG_TYPE_TEXT, content: typeof raw.content === 'string' ? raw.content : JSON.stringify(raw) }
}

/**
 * Map history API message to ChatMessage format
 */
var jsonRenderUtils = require('../utils/jsonRender')

function mapHistoryToChatMessage(m, myUid) {
  // Check event_meta for stream content (Stream API v2)
  var streamContent
  if (m && m.event_meta && m.event_meta.has_events) {
    var events = m.event_meta.events || []
    for (var i = 0; i < events.length; i++) {
      var ev = events[i]
      if (ev.event_key === 'main' && ev.snapshot && ev.snapshot.kind === 'text' && ev.snapshot.text) {
        streamContent = ev.snapshot.text
        break
      }
    }
  }

  // Parse mixed content from historical snapshot using MixedStreamParser
  var uiParts
  var rawContent = streamContent
  if (rawContent) {
    try {
      var parts = []
      var parser = jsonRenderUtils.createMixedStreamParser({
        onText: function (text) { parts.push({ type: 'text', text: text + '\n' }) },
        onPatch: function (patch) { parts.push({ type: 'data-spec', data: { type: 'patch', patch: patch } }) }
      })
      var normalised = rawContent.replace(/([^\n])```spec/g, '$1\n```spec')
      parser.push(normalised)
      parser.flush()

      // Backwards compatibility: old messages may have a separate "json_render"
      // event channel whose snapshot.text contains concatenated JSON patch arrays
      if (m && m.event_meta && m.event_meta.has_events) {
        var evts = m.event_meta.events || []
        for (var k = 0; k < evts.length; k++) {
          var jrEvt = evts[k]
          if (jrEvt.event_key === 'json_render' && jrEvt.snapshot && jrEvt.snapshot.text) {
            try {
              var norm = '[' + jrEvt.snapshot.text.replace(/\]\s*\[/g, ',') + ']'
              var outer = JSON.parse(norm)
              var flat = Array.isArray(outer) ? outer : [outer]
              // Flatten nested arrays
              var stack = flat.slice()
              while (stack.length > 0) {
                var item = stack.pop()
                if (Array.isArray(item)) {
                  for (var x = 0; x < item.length; x++) stack.push(item[x])
                } else if (item && typeof item === 'object' && 'op' in item && 'path' in item) {
                  parts.push({ type: 'data-spec', data: { type: 'patch', patch: item } })
                }
              }
            } catch (e) { /* ignore legacy parse failures */ }
            break
          }
        }
      }

      if (parts.length > 0) {
        var hasNonEmpty = false
        for (var p = 0; p < parts.length; p++) {
          if (parts[p].type !== 'text' || (parts[p].text && parts[p].text.trim())) {
            hasNonEmpty = true
            break
          }
        }
        if (hasNonEmpty) uiParts = parts
      }
    } catch (e) {
      console.warn('Failed to parse mixed stream content for historical message:', e)
    }
  }

  // Build display content from text parts, or use streamContent as-is
  var displayContent
  if (uiParts) {
    displayContent = ''
    for (var d = 0; d < uiParts.length; d++) {
      if (uiParts[d].type === 'text') displayContent += uiParts[d].text || ''
    }
  } else {
    displayContent = streamContent
  }

  var anchorPayload = toPayloadFromAny(m && m.payload)
  var isAiReply = anchorPayload.type === MSG_TYPE_AI_LOADING && anchorPayload.sender_kind === 'ai'
  var payload
  if (displayContent) {
    payload = { type: MSG_TYPE_TEXT, content: displayContent }
  } else {
    payload = anchorPayload
  }

  var errorMessage = m && m.error ? String(m.error) : undefined

  var result = {
    id: (m.message_id_str || m.client_msg_no || 'h-' + m.message_seq),
    role: (m.from_uid && myUid && m.from_uid === myUid) ? 'user' : 'agent',
    payload: payload,
    time: new Date((m.timestamp || 0) * 1000),
    messageSeq: typeof m.message_seq === 'number' ? m.message_seq : undefined,
    clientMsgNo: m.client_msg_no ? String(m.client_msg_no) : undefined,
    fromUid: m.from_uid ? String(m.from_uid) : undefined,
    channelId: m.channel_id ? String(m.channel_id) : undefined,
    channelType: typeof m.channel_type === 'number' ? m.channel_type : undefined,
    errorMessage: errorMessage
  }
  if (uiParts) result.uiParts = uiParts
  if (isAiReply) result.senderKind = 'ai'
  return result
}

module.exports = {
  MSG_TYPE_TEXT: MSG_TYPE_TEXT,
  MSG_TYPE_IMAGE: MSG_TYPE_IMAGE,
  MSG_TYPE_FILE: MSG_TYPE_FILE,
  MSG_TYPE_VOICE: MSG_TYPE_VOICE,
  MSG_TYPE_MIXED: MSG_TYPE_MIXED,
  MSG_TYPE_COMMAND: MSG_TYPE_COMMAND,
  MSG_TYPE_AI_LOADING: MSG_TYPE_AI_LOADING,
  isSystemMessageType: isSystemMessageType,
  toPayloadFromAny: toPayloadFromAny,
  mapHistoryToChatMessage: mapHistoryToChatMessage
}
