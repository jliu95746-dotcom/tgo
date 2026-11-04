import { resolveServer, resolveToken } from './config.js';

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
  token?: string;
}

export interface StreamCallbacks {
  onMessage: (event: string, data: unknown) => void;
  onError?: (error: Error) => void;
  onClose?: () => void;
  signal?: AbortSignal;
}

export class TgoClient {
  private server: string;
  private _token?: string;

  constructor(opts?: ClientOptions) {
    const server = opts?.server || resolveServer();
    if (!server) throw new Error('No server configured. Run: tgo auth login -s <url> -u <user> -p <pass>');
    // Ensure server ends without slash
    this.server = server.replace(/\/+$/, '');
    this._token = opts?.token || resolveToken();
  }

  private url(endpoint: string): string {
    const path = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
    return `${this.server}${path}`;
  }

  /** Expose server URL for WuKongIM route resolution */
  get serverUrl(): string {
    return this.server;
  }

  /** Expose token for WuKongIM authentication */
  get token(): string | undefined {
    return this._token;
  }

  private headers(contentType?: string): Record<string, string> {
    const h: Record<string, string> = {
      'X-User-Language': 'en',
    };
    if (contentType) h['Content-Type'] = contentType;
    if (this._token) h['Authorization'] = `Bearer ${this._token}`;
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
    // Handle 204 No Content
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    if (!text) return undefined as T;
    return JSON.parse(text) as T;
  }

  requireAuth(): void {
    if (!this._token) {
      throw new Error('Not authenticated. Run: tgo auth login -s <url> -u <user> -p <pass>');
    }
  }

  async get<T>(endpoint: string): Promise<T> {
    this.requireAuth();
    const res = await fetch(this.url(endpoint), {
      method: 'GET',
      headers: this.headers(),
    });
    return this.handleResponse<T>(res);
  }

  async post<T>(endpoint: string, data?: unknown, extraHeaders?: Record<string, string>): Promise<T> {
    this.requireAuth();
    const res = await fetch(this.url(endpoint), {
      method: 'POST',
      headers: { ...extraHeaders, ...this.headers('application/json') },
      body: data !== undefined ? JSON.stringify(data) : undefined,
    });
    return this.handleResponse<T>(res);
  }

  async put<T>(endpoint: string, data?: unknown): Promise<T> {
    this.requireAuth();
    const res = await fetch(this.url(endpoint), {
      method: 'PUT',
      headers: this.headers('application/json'),
      body: data !== undefined ? JSON.stringify(data) : undefined,
    });
    return this.handleResponse<T>(res);
  }

  async patch<T>(endpoint: string, data?: unknown): Promise<T> {
    this.requireAuth();
    const res = await fetch(this.url(endpoint), {
      method: 'PATCH',
      headers: this.headers('application/json'),
      body: data !== undefined ? JSON.stringify(data) : undefined,
    });
    return this.handleResponse<T>(res);
  }

  async delete<T>(endpoint: string): Promise<T> {
    this.requireAuth();
    const res = await fetch(this.url(endpoint), {
      method: 'DELETE',
      headers: this.headers(),
    });
    return this.handleResponse<T>(res);
  }

  /** POST with application/x-www-form-urlencoded (used for login) */
  async postForm<T>(endpoint: string, data: Record<string, string>): Promise<T> {
    const body = new URLSearchParams(data).toString();
    const res = await fetch(this.url(endpoint), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-User-Language': 'en',
      },
      body,
    });
    return this.handleResponse<T>(res);
  }

  /** POST with multipart/form-data (used for file uploads) */
  async postFormData<T>(endpoint: string, formData: FormData): Promise<T> {
    this.requireAuth();
    const h: Record<string, string> = { 'X-User-Language': 'en' };
    if (this._token) h['Authorization'] = `Bearer ${this._token}`;
    // Don't set Content-Type - fetch will set it with boundary
    const res = await fetch(this.url(endpoint), {
      method: 'POST',
      headers: h,
      body: formData,
    });
    return this.handleResponse<T>(res);
  }

  /** SSE streaming request */
  async stream(endpoint: string, data: unknown, callbacks: StreamCallbacks): Promise<void> {
    this.requireAuth();
    const res = await fetch(this.url(endpoint), {
      method: 'POST',
      headers: this.headers('application/json'),
      body: JSON.stringify(data),
      signal: callbacks.signal,
    });

    if (!res.ok) {
      let errData: ApiErrorDetail = {};
      try { errData = await res.json(); } catch { /* ignore */ }
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
              try {
                callbacks.onMessage(currentEvent, JSON.parse(rawData));
              } catch {
                callbacks.onMessage(currentEvent, rawData);
              }
            }
            currentEvent = 'message';
          }
        }
      }
    } catch (err) {
      if (callbacks.onError && err instanceof Error) callbacks.onError(err);
      else throw err;
    } finally {
      callbacks.onClose?.();
    }
  }
}

/** Create a client from global CLI options */
export function createClient(opts?: ClientOptions): TgoClient {
  return new TgoClient(opts);
}
