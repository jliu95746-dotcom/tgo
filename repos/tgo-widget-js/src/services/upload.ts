import { resolveApiKey } from '../utils/url'
import { captureVisitorDataAccess } from './visitorDataAccess'

export type ChatFileUploadResponse = {
  file_id: string
  file_name: string
  file_size: number
  file_type: string
  file_url: string
  channel_id: string
  channel_type: number
  uploaded_at: string
  uploaded_by?: string | null
}

export type UploadParams = {
  apiBase: string
  platformApiKey?: string
  channelId: string
  channelType: number
  file: File
  onProgress?: (percent: number, loaded: number, total: number) => void
  signal?: AbortSignal
}

export function makeChatFileUrl(apiBase: string, fileId: string, opts?: { includeApiKey?: boolean; apiKeyOverride?: string }): string {
  const base = apiBase.replace(/\/$/, '')
  void opts
  return `${base}/v1/chat/files/${encodeURIComponent(fileId)}`
}

// Use XMLHttpRequest to support upload progress callbacks
export function uploadChatFile({ apiBase, platformApiKey, channelId, channelType, file, onProgress, signal }: UploadParams): Promise<ChatFileUploadResponse> {
  return new Promise((resolve, reject) => {
    const apiKey = platformApiKey || resolveApiKey() || ''
    const access = captureVisitorDataAccess({apiBase,platformApiKey:apiKey,channelId})
    const xhr = new XMLHttpRequest()
    const base = apiBase.replace(/\/$/, '')
    const url = `${base}/v1/chat/upload`
    xhr.open('POST', url, true)

    if (apiKey) xhr.setRequestHeader('X-Platform-API-Key', apiKey)
    xhr.setRequestHeader('Authorization',access.headers.Authorization)

    xhr.responseType = 'json'

    if (xhr.upload && onProgress) {
      xhr.upload.onprogress = (e: ProgressEvent) => {
        try {access.assertCurrent()} catch (error) {cleanup();xhr.abort();reject(error);return}
        if (!e.lengthComputable) return onProgress(0, 0, 0)
        const p = Math.min(100, Math.round((e.loaded / Math.max(1, e.total)) * 100))
        onProgress(p, e.loaded, e.total)
      }
    }

    const onAbort = () => { try { xhr.abort() } catch {} }
    if (signal) {
      if (signal.aborted) return reject(new DOMException('Aborted', 'AbortError'))
      signal.addEventListener('abort', onAbort, { once: true })
    }

    xhr.onerror = () => {
      cleanup()
      reject(new Error('上传失败，请稍后重试。'))
    }
    xhr.onabort = () => {cleanup();reject(new DOMException('Aborted','AbortError'))}
    xhr.onload = () => {
      cleanup()
      try {access.assertCurrent();access.assertSafeValue(xhr.response)} catch (error) {reject(error);return}
      const status = xhr.status
      if (status >= 200 && status < 300) {
        const data = xhr.response as ChatFileUploadResponse
        if (data && (data.channel_id !== channelId || data.channel_type !== channelType)) {
          return reject(new Error('附件会话不匹配，请重新连接。'))
        }
        if (!data || !data.file_id) {
          return reject(new Error('[Upload] invalid response'))
        }
        resolve(data)
      } else {
        reject(new Error(`上传失败（HTTP ${status}）。`))
      }
    }

    const cleanup = () => {
      try { if (signal) signal.removeEventListener('abort', onAbort) } catch {}
    }

    const form = new FormData()
    form.append('file', file)
    form.append('channel_id', channelId)
    form.append('channel_type', String(channelType))
    // dual auth supported: we already set X-Platform-API-Key header; body field optional

    xhr.send(form)
  })
}

export async function readImageDimensions(file: File): Promise<{ width: number; height: number } | null> {
  if (!file.type.startsWith('image/')) return null
  // Prefer createImageBitmap if available
  try {
    // @ts-ignore
    if (typeof createImageBitmap === 'function') {
      // @ts-ignore
      const bmp = await createImageBitmap(file)
      const dims = { width: bmp.width, height: bmp.height }
      try { bmp.close?.() } catch {}
      return dims
    }
  } catch {}
  return new Promise(resolve => {
    try {
      const url = URL.createObjectURL(file)
      const img = new Image()
      img.onload = () => {
        const dims = { width: img.naturalWidth || img.width, height: img.naturalHeight || img.height }
        try { URL.revokeObjectURL(url) } catch {}
        resolve(dims)
      }
      img.onerror = () => { try { URL.revokeObjectURL(url) } catch {}; resolve(null) }
      img.src = url
    } catch { resolve(null) }
  })
}

