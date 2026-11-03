import { resolveServer, resolveApiKey } from './config.js';

export interface ApiErrorDetail {
  code?: string;
  message?: string;
  details?: Record<string, unknown>;
  status_code?: number;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public data: ApiErrorDetail,
    public requestId?: string,
  ) {
    super(data.message || `API error ${status}`);
    this.name = 'ApiError';
  }
}

export interface ClientOptions {
  server?: string;
  apiKey?: string;
}

export interface VisitorEndOutcome {
  accessEnded: boolean;
  upstreamStopStatus: 'confirmed' | 'pending';
  imDisconnectStatus: 'requested' | 'pending';
}

export interface VisitorRequestProof {
  readonly imToken: string;
  readonly assertCurrent: () => void;
}

export interface StreamCallbacks {
  onMessage: (event: string, data: unknown) => void;
  onError?: (error: Error) => void;
  onClose?: () => void;
  signal?: AbortSignal;
}

export class TgoWidgetClient {
  private server: string;
  private _apiKey?: string;

  constructor(opts?: ClientOptions) {
    const server = opts?.server || resolveServer();
    if (!server) throw new Error('No server configured. Run: tgo-widget init --api-key <key> --server <url>');
    this.server = server.replace(/\/+$/, '');
    this._apiKey = opts?.apiKey || resolveApiKey();
  }

  private url(endpoint: string): string {
    const path = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
    return `${this.server}${path}`;
  }

  get serverUrl(): string {
    return this.server;
  }

  get apiKey(): string | undefined {
    return this._apiKey;
  }

  private headers(contentType?: string): Record<string, string> {
    const h: Record<string, string> = {
      'X-User-Language': 'en',
    };
    if (contentType) h['Content-Type'] = contentType;
    if (this._apiKey) h['X-Platform-API-Key'] = this._apiKey;
    return h;
  }

  private async handleResponse<T>(res: Response): Promise<T> {
    const requestId = res.headers.get('x-request-id') || undefined;
    if (!res.ok) {
      let data: ApiErrorDetail = {};
      try {
        const body = await res.json();
        data = body.error || body;
      } catch {
        data = { message: await res.text().catch(() => `HTTP ${res.status}`) };
      }
      throw new ApiError(res.status, data, requestId);
    }
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    if (!text) return undefined as T;
    return JSON.parse(text) as T;
  }

  requireApiKey(): void {
    if (!this._apiKey) {
      throw new Error('No API key configured. Run: tgo-widget init --api-key <key>');
    }
  }

  async get<T>(endpoint: string): Promise<T> {
    this.requireApiKey();
    const res = await fetch(this.url(endpoint), {
      method: 'GET',
      headers: this.headers(),
    });
    return this.handleResponse<T>(res);
  }

  async post<T>(endpoint: string, data?: unknown): Promise<T> {
    this.requireApiKey();
    const res = await fetch(this.url(endpoint), {
      method: 'POST',
      headers: this.headers('application/json'),
      body: data !== undefined ? JSON.stringify(data) : undefined,
    });
    return this.handleResponse<T>(res);
  }

  /** The current visitor grant travels only in Authorization, never in JSON/URL. */
  async postVisitor<T>(endpoint: string, data: unknown, proof: VisitorRequestProof): Promise<T> {
    return this.visitorRequest<T>(endpoint, JSON.stringify(data), 'application/json', proof);
  }

  async getVisitor<T>(endpoint: string, proof: VisitorRequestProof): Promise<T> {
    return this.visitorRequest<T>(endpoint, undefined, undefined, proof, 'GET');
  }

  async postVisitorFormData<T>(endpoint: string, data: FormData, proof: VisitorRequestProof): Promise<T> {
    return this.visitorRequest<T>(endpoint, data, undefined, proof);
  }

  private async visitorRequest<T>(endpoint: string, body: BodyInit | undefined, contentType: string | undefined,
    proof: VisitorRequestProof, method: 'GET' | 'POST' = 'POST'): Promise<T> {
    this.requireApiKey();
    if (!proof.imToken || proof.imToken.length > 8192) throw new Error('当前聊天访问尚未建立，请重新初始化。');
    proof.assertCurrent();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), endpoint === '/v1/chat/completion' ? 120000 : 10000);
    try {
      const response = await fetch(this.url(endpoint), {
        method,headers:{...this.headers(contentType),Authorization:`Bearer ${proof.imToken}`},
        body,signal:controller.signal,
      });
      proof.assertCurrent();
      const text = await response.text();
      proof.assertCurrent();
      let value: unknown;
      try {value = text ? JSON.parse(text) : null;}
      catch {throw new Error('聊天请求响应无效，请稍后重试。');}
      if (text.includes(proof.imToken) || (JSON.stringify(value) || '').includes(proof.imToken)) {
        throw new Error('聊天请求响应未通过安全检查。');
      }
      const result = await this.handleResponse<T>(new Response(response.status === 204 ? null : text, {
        status: response.status, statusText: response.statusText, headers: response.headers,
      }));
      proof.assertCurrent();
      return result;
    } finally {clearTimeout(timer);}
  }

  /** POST without auth header (api_key goes in body, e.g. visitor register) */
  async postPublic<T>(endpoint: string, data?: unknown): Promise<T> {
    const res = await fetch(this.url(endpoint), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-User-Language': 'en' },
      body: data !== undefined ? JSON.stringify(data) : undefined,
    });
    return this.handleResponse<T>(res);
  }

  async endVisitorAccess(identity: { visitorId: string; visitorEndToken: string }): Promise<VisitorEndOutcome> {
    const unknown: VisitorEndOutcome = {accessEnded:false,upstreamStopStatus:'pending',imDisconnectStatus:'pending'};
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(this.url('/v1/visitors/access/end'), {
        method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,
        body:JSON.stringify({platform_api_key:this._apiKey,visitor_id:identity.visitorId,visitor_end_token:identity.visitorEndToken}),
      });
      if (!response.ok) return unknown;
      const value: unknown = await response.json();
      if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== 3) return unknown;
      const receipt = value as Record<string,unknown>;
      if (receipt.status !== 'ended' || !['confirmed','pending'].includes(String(receipt.upstream_stop_status))
          || !['requested','pending'].includes(String(receipt.im_disconnect_status))) return unknown;
      return {accessEnded:true,upstreamStopStatus:receipt.upstream_stop_status as 'confirmed'|'pending',
        imDisconnectStatus:receipt.im_disconnect_status as 'requested'|'pending'};
    } catch { return unknown; }
    finally { clearTimeout(timer); }
  }

  /** POST with multipart/form-data (file uploads) */
  async postFormData<T>(endpoint: string, formData: FormData): Promise<T> {
    this.requireApiKey();
    const h: Record<string, string> = { 'X-User-Language': 'en' };
    if (this._apiKey) h['X-Platform-API-Key'] = this._apiKey;
    const res = await fetch(this.url(endpoint), {
      method: 'POST',
      headers: h,
      body: formData,
    });
    return this.handleResponse<T>(res);
  }

  async streamVisitor(endpoint: string, data: unknown, callbacks: StreamCallbacks, proof: VisitorRequestProof): Promise<void> {
    await this.stream(endpoint, data, callbacks, proof);
  }

  /** Caller-bound visitor streams use the same original grant as JSON requests. */
  async stream(endpoint: string, data: unknown, callbacks: StreamCallbacks, proof?: VisitorRequestProof): Promise<void> {
    if (proof && (!proof.imToken || proof.imToken.length > 8192)) throw new Error('当前聊天访问尚未建立，请重新初始化。');
    proof?.assertCurrent();
    const res = await fetch(this.url(endpoint), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-User-Language': 'en',
        ...(proof ? {Authorization:`Bearer ${proof.imToken}`} : {}) },
      body: JSON.stringify(data),
      signal: callbacks.signal,
    });
    proof?.assertCurrent();

    if (!res.ok) {
      let errData: ApiErrorDetail = {};
      try { errData = await res.json(); } catch { /* ignore */ }
      if (proof && (JSON.stringify(errData) || '').includes(proof.imToken)) throw new Error('聊天响应未通过安全检查。');
      throw new ApiError(res.status, errData);
    }

    const reader = res.body?.getReader();
    if (!reader) throw new Error('No response body');

    const decoder = new TextDecoder();
    let buffer = '';
    let currentEvent = 'message';

    try {
      while (true) {
        const { done, value } = await reader.read();
        proof?.assertCurrent();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event:')) {
            currentEvent = line.slice(6).trim();
          } else if (line.startsWith('data:')) {
            const rawData = line.slice(5).trim();
            if (rawData) {
              let parsed: unknown;
              try { parsed = JSON.parse(rawData); } catch { parsed = rawData; }
              if (proof && (rawData.includes(proof.imToken) || (JSON.stringify(parsed) || '').includes(proof.imToken))) {
                throw new Error('聊天响应未通过安全检查。');
              }
              proof?.assertCurrent();
              callbacks.onMessage(currentEvent, parsed);
            }
            currentEvent = 'message';
          }
        }
      }
    } catch (err) {
      if (!proof && callbacks.onError && err instanceof Error) callbacks.onError(err);
      else throw err;
    } finally {
      if (proof) await reader.cancel().catch(() => {});
      callbacks.onClose?.();
    }
  }
}

/** Create a client from global CLI options */
export function createClient(opts?: ClientOptions): TgoWidgetClient {
  return new TgoWidgetClient(opts);
}
