import {loadCachedVisitor} from './visitor'

const failure = () => new Error('聊天访问已变化或尚未建立，请重新连接后重试。')

export function captureVisitorDataAccess(options: {
  apiBase: string; platformApiKey: string; channelId?: string; visitorId?: string
}): Readonly<{
  headers: Readonly<Record<string,string>>
  assertCurrent: () => void
  assertSafeValue: (value: unknown) => void
}> {
  const cached = loadCachedVisitor(options.apiBase, options.platformApiKey)
  if (!cached?.visitor_id || typeof cached.im_token !== 'string' || !cached.im_token || cached.im_token.length>8192) throw failure()
  if (options.visitorId && options.visitorId !== cached.visitor_id) throw failure()
  if (options.channelId && ![cached.channel_id,cached.visitor_id,`${cached.visitor_id}-vtr`].includes(options.channelId)) throw failure()
  const visitorId = cached.visitor_id, token = cached.im_token
  return Object.freeze({
    headers:Object.freeze({Authorization:`Bearer ${token}`}),
    assertCurrent:() => {
      const current = loadCachedVisitor(options.apiBase, options.platformApiKey)
      if (!current || current.visitor_id !== visitorId || current.im_token !== token) throw failure()
    },
    assertSafeValue:(value:unknown) => {
      try {if ((JSON.stringify(value) || '').includes(token)) throw failure()}
      catch {throw failure()}
    },
  })
}
