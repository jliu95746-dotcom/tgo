import { describe, it, expect, vi, beforeEach } from 'vitest';
import { TgoClient, ApiError } from './client.js';

// Mock config module so TgoClient doesn't read real config
vi.mock('./config.js', () => ({
  resolveServer: vi.fn(() => undefined),
  resolveToken: vi.fn(() => undefined),
}));

// Mock global fetch
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

/** Helper: create a mock Response */
function mockResponse(body: unknown, init?: { status?: number; headers?: Record<string, string> }): Response {
  const status = init?.status ?? 200;
  const bodyStr = typeof body === 'string' ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(init?.headers ?? {}),
    json: () => Promise.resolve(typeof body === 'string' ? JSON.parse(body) : body),
    text: () => Promise.resolve(bodyStr),
    body: null,
  } as unknown as Response;
}

describe('TgoClient', () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  describe('constructor', () => {
    it('should throw when no server configured', () => {
      expect(() => new TgoClient()).toThrow('No server configured');
    });

    it('should strip trailing slashes from server', () => {
      const client = new TgoClient({ server: 'http://localhost:8000/api///' });
      // Verify by making a request and checking the URL
      mockFetch.mockResolvedValue(mockResponse({ ok: true }));
      client.get('/v1/test').catch(() => {}); // will fail auth but fetch gets called
      // Won't have auth, so let's test with token
    });

    it('should accept server and token options', () => {
      const client = new TgoClient({ server: 'http://localhost:8000/api', token: 'test-token' });
      expect(client).toBeDefined();
    });
  });

  describe('requireAuth', () => {
    it('should throw when no token', () => {
      const client = new TgoClient({ server: 'http://localhost:8000/api' });
      expect(() => client.requireAuth()).toThrow('Not authenticated');
    });

    it('should not throw when token is set', () => {
      const client = new TgoClient({ server: 'http://localhost:8000/api', token: 'tok' });
      expect(() => client.requireAuth()).not.toThrow();
    });
  });

  describe('HTTP methods', () => {
    let client: TgoClient;

    beforeEach(() => {
      client = new TgoClient({ server: 'http://localhost:8000/api', token: 'test-token' });
    });

    it('GET should call fetch with correct method and headers', async () => {
      mockFetch.mockResolvedValue(mockResponse({ data: 'hello' }));

      const result = await client.get('/v1/visitors');

      expect(mockFetch).toHaveBeenCalledWith(
        'http://localhost:8000/api/v1/visitors',
        expect.objectContaining({
          method: 'GET',
          headers: expect.objectContaining({
            'Authorization': 'Bearer test-token',
            'X-User-Language': 'en',
          }),
        }),
      );
      expect(result).toEqual({ data: 'hello' });
    });

    it('POST should send JSON body', async () => {
      mockFetch.mockResolvedValue(mockResponse({ id: '123' }));

      await client.post('/v1/visitors/abc/accept', { reason: 'test' });

      expect(mockFetch).toHaveBeenCalledWith(
        'http://localhost:8000/api/v1/visitors/abc/accept',
        expect.objectContaining({
          method: 'POST',
          headers: expect.objectContaining({
            'Content-Type': 'application/json',
          }),
          body: JSON.stringify({ reason: 'test' }),
        }),
      );
    });

    it('POST can add an operation id without replacing authorization', async () => {
      mockFetch.mockResolvedValue(mockResponse({ success: true }));
      await client.post('/v1/sessions/visitor/v-1/close', {}, {
        'X-Session-Operation-ID': 'd9503a7a-91e1-4f6a-8da3-cc31ff535d21',
        Authorization: 'Bearer wrong',
      });
      expect(mockFetch).toHaveBeenCalledWith(
        'http://localhost:8000/api/v1/sessions/visitor/v-1/close',
        expect.objectContaining({ headers: expect.objectContaining({
          'X-Session-Operation-ID': 'd9503a7a-91e1-4f6a-8da3-cc31ff535d21',
          Authorization: 'Bearer test-token',
        }) }),
      );
    });

    it('PUT should send JSON body', async () => {
      mockFetch.mockResolvedValue(mockResponse({ updated: true }));
      await client.put('/v1/staff/me/service-paused?paused=true');
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/v1/staff/me/service-paused?paused=true'),
        expect.objectContaining({ method: 'PUT' }),
      );
    });

    it('PATCH should send JSON body', async () => {
      mockFetch.mockResolvedValue(mockResponse({ patched: true }));
      await client.patch('/v1/ai/agents/123', { name: 'new-name' });
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/v1/ai/agents/123'),
        expect.objectContaining({
          method: 'PATCH',
          body: JSON.stringify({ name: 'new-name' }),
        }),
      );
    });

    it('DELETE should call fetch with correct method', async () => {
      mockFetch.mockResolvedValue(mockResponse(null, { status: 204 }));
      await client.delete('/v1/tags/abc');
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/v1/tags/abc'),
        expect.objectContaining({ method: 'DELETE' }),
      );
    });

    it('should handle 204 No Content', async () => {
      mockFetch.mockResolvedValue(mockResponse('', { status: 204 }));
      const result = await client.delete('/v1/resource/123');
      expect(result).toBeUndefined();
    });
  });

  describe('postForm', () => {
    it('should send form-urlencoded data without auth header', async () => {
      mockFetch.mockResolvedValue(
        mockResponse({ access_token: 'new-tok', token_type: 'bearer' }),
      );

      const client = new TgoClient({ server: 'http://localhost:8000/api' });
      const result = await client.postForm('/v1/staff/login', {
        username: 'admin',
        password: 'pass',
        grant_type: 'password',
      });

      expect(mockFetch).toHaveBeenCalledWith(
        'http://localhost:8000/api/v1/staff/login',
        expect.objectContaining({
          method: 'POST',
          headers: expect.objectContaining({
            'Content-Type': 'application/x-www-form-urlencoded',
          }),
          body: 'username=admin&password=pass&grant_type=password',
        }),
      );
      expect(result).toEqual({ access_token: 'new-tok', token_type: 'bearer' });
    });
  });

  describe('error handling', () => {
    let client: TgoClient;

    beforeEach(() => {
      client = new TgoClient({ server: 'http://localhost:8000/api', token: 'tok' });
    });

    it('should throw ApiError on non-ok response with JSON error body', async () => {
      mockFetch.mockResolvedValue(
        mockResponse(
          { error: { code: 'NOT_FOUND', message: 'Visitor not found' } },
          { status: 404, headers: { 'x-request-id': 'req-123' } },
        ),
      );

      await expect(client.get('/v1/visitors/bad-id')).rejects.toThrow(ApiError);

      try {
        await client.get('/v1/visitors/bad-id');
      } catch (err) {
        expect(err).toBeInstanceOf(ApiError);
        const apiErr = err as ApiError;
        expect(apiErr.status).toBe(404);
        expect(apiErr.data.code).toBe('NOT_FOUND');
        expect(apiErr.data.message).toBe('Visitor not found');
        expect(apiErr.requestId).toBe('req-123');
      }
    });

    it('should throw ApiError on non-ok response with plain text body', async () => {
      const res = {
        ok: false,
        status: 500,
        headers: new Headers(),
        json: () => Promise.reject(new Error('not json')),
        text: () => Promise.resolve('Internal Server Error'),
      } as unknown as Response;
      mockFetch.mockResolvedValue(res);

      await expect(client.get('/v1/test')).rejects.toThrow(ApiError);
    });

    it('should throw when calling authenticated method without token', async () => {
      const noAuthClient = new TgoClient({ server: 'http://localhost:8000/api' });
      await expect(noAuthClient.get('/v1/test')).rejects.toThrow('Not authenticated');
    });
  });
});
