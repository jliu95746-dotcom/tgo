import type { BusinessCustomerSessionContext, BusinessCustomerSessionInput, BusinessCustomerIdentityInput } from '../types/businessCustomer'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const FAILURE = '客户业务身份暂不可确认，请重新验证后发送或转人工。'

export class CustomerIdentityChangedError extends Error {
  constructor() { super(FAILURE) }
}

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

export function normalizeCustomerSessions(value: unknown): readonly BusinessCustomerSessionInput[] {
  if (!Array.isArray(value) || value.length > 20) throw new Error(FAILURE)
  const connections = new Set<string>()
  return Object.freeze(value.map((item: unknown) => {
    if (!record(item) || Object.keys(item).length !== 2
      || Object.keys(item).some(key => !['connection_id', 'customer_session'].includes(key)) || typeof item.connection_id !== 'string'
      || !UUID.test(item.connection_id) || typeof item.customer_session !== 'string'
      || !item.customer_session.trim() || item.customer_session.length > 8192) throw new Error(FAILURE)
    const connection = item.connection_id.toLowerCase()
    if (connections.has(connection)) throw new Error(FAILURE)
    connections.add(connection)
    return Object.freeze({ connection_id: connection, customer_session: item.customer_session })
  }))
}

export function normalizeCustomerIdentities(value: unknown): readonly BusinessCustomerIdentityInput[] {
  if (!Array.isArray(value) || value.length > 20) throw new Error(FAILURE)
  const seen = new Set<string>()
  return Object.freeze(value.map((item: unknown) => {
    if (!record(item) || Object.keys(item).length !== 2 || typeof item.connection_id !== 'string' || !UUID.test(item.connection_id)) throw new Error(FAILURE)
    const id = item.connection_id.toLowerCase()
    if (seen.has(id)) throw new Error(FAILURE)
    seen.add(id)
    if ('customer_session' in item) return normalizeCustomerSessions([item])[0]
    if (Object.keys(item).some(key => !['connection_id','merchant_assertion'].includes(key)) || typeof item.merchant_assertion !== 'string'
      || !item.merchant_assertion.trim() || item.merchant_assertion.length > 16384) throw new Error(FAILURE)
    return Object.freeze({connection_id:id,merchant_assertion:item.merchant_assertion})
  }))
}

export function ensureNoCustomerSessionEcho(value: unknown, sessions: readonly BusinessCustomerSessionInput[] = []): void {
  const text = JSON.stringify(value) || ''
  if (sessions.some(session => text.includes(session.customer_session))) throw new Error(FAILURE)
}

type Pending = {
  context: BusinessCustomerSessionContext
  resolve: (value: readonly BusinessCustomerIdentityInput[] | undefined) => void
  reject: (error: Error) => void
  timer: ReturnType<typeof setTimeout>
}

/** Only the exact host and matching one-shot request can supply private sessions. */
export class BusinessCustomerSessionBridge {
  private readonly parentOrigin: string | null
  private readonly pending = new Map<string, Pending>()
  private enabled: boolean
  private generation: number | undefined
  private closed = false
  private epoch = 0
  private ready: Promise<boolean> = Promise.resolve(true)
  private resolveReady: ((ready: boolean) => void) | undefined
  private readyTimer: ReturnType<typeof setTimeout> | undefined
  private readonly listeners = new Set<() => void>()

  get revision(): number { return this.epoch }
  get isClosed(): boolean { return this.closed }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  async snapshot(): Promise<{ hasProvider: boolean; assertCurrent: () => void }> {
    const revision = this.epoch
    if (!(await this.ready) || this.closed || this.epoch !== revision || (this.enabled && !this.parentOrigin)) throw new Error(FAILURE)
    return { hasProvider: this.enabled, assertCurrent: () => {
      if (this.closed || this.epoch !== revision) throw new Error(FAILURE)
    } }
  }

  private changed(): void {
    for (const listener of this.listeners) { try { listener() } catch {} }
  }

  constructor(private readonly win: Window, referrer: string) {
    const url = new URL(win.location.href)
    this.enabled = win.parent !== win && url.searchParams.get('businessIdentity') === '1'
    let origin: string | null = null
    try {
      const declared = url.searchParams.get('hostOrigin')
      const reference = referrer ? new URL(referrer).origin : null
      origin = reference || (declared ? new URL(declared).origin : null)
      if ((declared && new URL(declared).origin !== origin) || !origin || !/^https?:\/\//.test(origin)) origin = null
    } catch { origin = null }
    this.parentOrigin = origin
    if (win.parent !== win && origin) {
      if (url.searchParams.has('hostOrigin') || this.enabled) {
        this.ready = new Promise(resolve => {
          this.resolveReady = resolve
          this.readyTimer = setTimeout(() => { this.resolveReady = undefined; resolve(false) }, 8000)
        })
      }
      win.addEventListener('message', this.receive)
      win.parent.postMessage({ type: 'TGO_BUSINESS_SESSION_STATE_REQUEST' }, origin)
    }
  }

  private readonly receive = (event: MessageEvent<unknown>): void => {
    if (this.closed || event.source !== this.win.parent || event.origin !== this.parentOrigin || !record(event.data)) return
    const data = event.data
    if (data.type === 'TGO_BUSINESS_SESSION_STATE' && typeof data.enabled === 'boolean'
      && Number.isSafeInteger(data.generation) && (data.generation as number) >= 0) {
      const changed = this.generation !== undefined && (this.generation !== data.generation || this.enabled !== data.enabled)
      if (changed) {
        this.epoch += 1
        this.rejectPending()
      }
      this.generation = data.generation as number
      this.enabled = data.enabled
      clearTimeout(this.readyTimer)
      this.resolveReady?.(true)
      this.resolveReady = undefined
      if (changed) this.changed()
      return
    }
    if (data.type !== 'TGO_BUSINESS_SESSION_RESPONSE' || typeof data.requestId !== 'string') return
    const pending = this.pending.get(data.requestId)
    if (!pending || !record(data.context) || Object.keys(pending.context).some(key =>
      data.context && (data.context as Record<string, unknown>)[key] !== pending.context[key as keyof BusinessCustomerSessionContext])) return
    clearTimeout(pending.timer)
    this.pending.delete(data.requestId)
    try {
      if (data.ok !== true || !Number.isSafeInteger(data.generation) || (data.generation as number) < 0
        || (this.generation !== undefined && this.generation !== data.generation)) throw new Error(FAILURE)
      this.generation = data.generation as number
      pending.resolve(normalizeCustomerIdentities(data.sessions))
    } catch { pending.reject(new Error(FAILURE)) }
  }

  async request(context: BusinessCustomerSessionContext): Promise<readonly BusinessCustomerIdentityInput[] | undefined> {
    if (this.closed) return Promise.reject(new Error(FAILURE))
    const epoch = this.epoch
    if (!(await this.ready) || this.closed || epoch !== this.epoch) throw new Error(FAILURE)
    if (!this.enabled || this.win.parent === this.win) return Promise.resolve(undefined)
    if (!this.parentOrigin || this.pending.size >= 20 || ![context.visitorId, context.projectId, context.platformId].every(id => UUID.test(id))) {
      return Promise.reject(new Error(FAILURE))
    }
    const requestId = this.win.crypto.randomUUID()
    if (this.pending.has(requestId)) return Promise.reject(new Error(FAILURE))
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId)
        reject(new Error(FAILURE))
      }, 8000)
      this.pending.set(requestId, { context: Object.freeze({ ...context }), resolve, reject, timer })
      try {
        this.win.parent.postMessage({ type: 'TGO_BUSINESS_SESSION_REQUEST', requestId, context }, this.parentOrigin!)
      } catch {
        clearTimeout(timer)
        this.pending.delete(requestId)
        reject(new Error(FAILURE))
      }
    })
  }

  private rejectPending(): void {
    for (const item of this.pending.values()) {
      clearTimeout(item.timer)
      item.reject(new Error(FAILURE))
    }
    this.pending.clear()
  }

  dispose(): void {
    this.closed = true
    this.epoch += 1
    this.enabled = false
    this.rejectPending()
    this.win.removeEventListener('message', this.receive)
    clearTimeout(this.readyTimer)
    this.resolveReady?.(false)
    this.resolveReady = undefined
    this.changed()
    this.listeners.clear()
  }
}

let current: BusinessCustomerSessionBridge | undefined

export function initializeBusinessCustomerSessions(): void {
  if (typeof window !== 'undefined') current ||= new BusinessCustomerSessionBridge(window, document.referrer)
}

export function subscribeBusinessCustomerSessionChanges(listener: () => void): () => void {
  initializeBusinessCustomerSessions()
  return current?.subscribe(listener) || (() => {})
}

export async function captureBusinessCustomerSessionState(): Promise<{hasProvider:boolean;assertCurrent:()=>void}> {
  if (typeof window === 'undefined') return {hasProvider:false,assertCurrent() {}}
  initializeBusinessCustomerSessions()
  const captured = current!
  const state = await captured.snapshot()
  return {hasProvider:state.hasProvider,assertCurrent:()=>{
    if (current !== captured) throw new Error(FAILURE)
    state.assertCurrent()
  }}
}

export async function getBusinessCustomerSessions(context: BusinessCustomerSessionContext): Promise<readonly BusinessCustomerSessionInput[] | undefined> {
  if (typeof window === 'undefined') return Promise.resolve(undefined)
  current ||= new BusinessCustomerSessionBridge(window, document.referrer)
  const input = await current.request(context)
  return input ? normalizeCustomerSessions(input) : undefined
}

export function disposeBusinessCustomerSessions(): void {
  current?.dispose()
  current = undefined
}

export async function acquireBusinessCustomerSessions(context: BusinessCustomerSessionContext, registration?: {
  apiBase: string; visitorAccessToken: string; assertCurrent?: () => void
}): Promise<{
  sessions: readonly BusinessCustomerSessionInput[] | undefined
  assertCurrent: () => void
}> {
  if (typeof window === 'undefined') return { sessions: undefined, assertCurrent() {} }
  current ||= new BusinessCustomerSessionBridge(window, document.referrer)
  const captured = current
  const revision = captured.revision
  const inputs = await captured.request(context)
  const assertCurrent = () => {
    if (current !== captured || captured.isClosed || captured.revision !== revision) throw new Error(FAILURE)
    registration?.assertCurrent?.()
  }
  assertCurrent()
  const sessions = inputs ? await resolveCustomerIdentities(inputs, context, registration, assertCurrent) : undefined
  return { sessions, assertCurrent }
}

export async function resolveCustomerIdentities(inputs: readonly BusinessCustomerIdentityInput[], context: BusinessCustomerSessionContext,
  registration: {apiBase:string;visitorAccessToken:string} | undefined, assertCurrent: () => void): Promise<readonly BusinessCustomerSessionInput[]> {
  const result: BusinessCustomerSessionInput[] = []
  for (const input of normalizeCustomerIdentities(inputs)) {
    assertCurrent()
    if ('customer_session' in input) { result.push(input); continue }
    if (!registration?.visitorAccessToken) throw new Error(FAILURE)
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 10000)
    try {
      const response = await fetch(`${registration.apiBase.replace(/\/$/,'')}/v1/business-connections/${input.connection_id}/customer-identities/exchange`, {
        method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,
        body:JSON.stringify({merchant_assertion:input.merchant_assertion,visitor_access_token:registration.visitorAccessToken})
      })
      const text = await response.text()
      assertCurrent()
      if (text.length > 32768 || text.includes(input.merchant_assertion) || text.includes(registration.visitorAccessToken)) throw new Error(FAILURE)
      const data: unknown = JSON.parse(text)
      if (!response.ok) {
        if (response.status===409 && record(data) && record(data.error) && record(data.error.details)
          && data.error.details.reason==='visitor_owner_changed') throw new CustomerIdentityChangedError()
        throw new Error(FAILURE)
      }
      if (!record(data) || data.visitor_id !== context.visitorId || data.project_id !== context.projectId || data.platform_id !== context.platformId
        || data.connection_id !== input.connection_id || data.can_enable !== false || typeof data.session_token !== 'string') throw new Error(FAILURE)
      result.push(normalizeCustomerSessions([{connection_id:input.connection_id,customer_session:data.session_token}])[0])
    } catch(error) {
      if(error instanceof CustomerIdentityChangedError) throw error
      throw new Error(FAILURE)
    } finally { clearTimeout(timer) }
  }
  assertCurrent()
  return Object.freeze(result)
}
