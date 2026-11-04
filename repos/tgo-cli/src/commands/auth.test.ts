import { describe, it, expect, vi, beforeEach } from 'vitest';
import { authLogin, authLogout, authWhoami } from './auth.js';
import type { TgoClient } from '../client.js';

// Mock config module
vi.mock('../config.js', () => ({
  resolveServer: vi.fn(),
  resolveToken: vi.fn(),
  updateConfig: vi.fn(),
  beginAuthLogin: vi.fn(() => 'request-1'),
  completeAuthLogin: vi.fn(() => true),
  cancelAuthLogin: vi.fn(),
  clearAuthSession: vi.fn(),
  loadConfig: vi.fn(() => ({})),
}));

// Mock TgoClient
vi.mock('../client.js', () => ({
  TgoClient: vi.fn().mockImplementation((opts: any) => ({
    postForm: vi.fn(),
    get: vi.fn(),
    post: vi.fn(),
    _opts: opts,
  })),
  ApiError: class extends Error {
    constructor(public status: number, public data: any) { super(); }
  },
}));

import { TgoClient as MockedTgoClient } from '../client.js';
import { beginAuthLogin, cancelAuthLogin, clearAuthSession, completeAuthLogin } from '../config.js';

describe('auth commands', () => {
  beforeEach(() => {
    vi.mocked(MockedTgoClient).mockClear();
    vi.mocked(beginAuthLogin).mockClear();
    vi.mocked(completeAuthLogin).mockClear();
    vi.mocked(cancelAuthLogin).mockClear();
    vi.mocked(clearAuthSession).mockClear();
  });

  describe('authLogin', () => {
    it('should call postForm with correct credentials and save token', async () => {
      const mockPostForm = vi.fn().mockResolvedValue({
        access_token: 'new-token-123',
        token_type: 'bearer',
        staff: { id: '1', name: 'Admin' },
      });
      vi.mocked(MockedTgoClient).mockImplementation(() => ({
        postForm: mockPostForm,
      }) as any);

      const result = await authLogin('http://localhost:8000/api', 'admin', 'password');

      // Verify TgoClient was created with the server
      expect(MockedTgoClient).toHaveBeenCalledWith({ server: 'http://localhost:8000/api' });

      // Verify postForm was called correctly
      expect(mockPostForm).toHaveBeenCalledWith('/v1/staff/login', {
        username: 'admin',
        password: 'password',
        grant_type: 'password',
      });

      expect(beginAuthLogin).toHaveBeenCalledOnce();
      expect(completeAuthLogin).toHaveBeenCalledWith('request-1', 'http://localhost:8000/api', 'new-token-123');
      expect(cancelAuthLogin).not.toHaveBeenCalled();

      // Verify return value
      expect(result).toEqual({
        success: true,
        message: 'Logged in successfully',
        staff: { id: '1', name: 'Admin' },
      });
    });

    it('should propagate API errors', async () => {
      const mockPostForm = vi.fn().mockRejectedValue(new Error('Invalid credentials'));
      vi.mocked(MockedTgoClient).mockImplementation(() => ({
        postForm: mockPostForm,
      }) as any);

      await expect(authLogin('http://test', 'bad', 'bad')).rejects.toThrow('Invalid credentials');
      expect(completeAuthLogin).not.toHaveBeenCalled();
      expect(cancelAuthLogin).toHaveBeenCalledWith('request-1');
    });
  });

  describe('authLogout', () => {
    it('should clear token from config', () => {
      const result = authLogout();
      expect(clearAuthSession).toHaveBeenCalledOnce();
      expect(result).toEqual({ success: true, message: 'Logged out' });
    });
  });

  describe('authWhoami', () => {
    it('should call GET /v1/staff/me', async () => {
      const mockGet = vi.fn().mockResolvedValue({ id: '1', name: 'Admin', role: 'admin' });
      vi.mocked(MockedTgoClient).mockImplementation(() => ({ get: mockGet }) as any);

      const result = await authWhoami({ server: 'http://test', token: 'tok' });

      expect(MockedTgoClient).toHaveBeenCalledWith({ server: 'http://test', token: 'tok' });
      expect(mockGet).toHaveBeenCalledWith('/v1/staff/me');
      expect(result).toEqual({ id: '1', name: 'Admin', role: 'admin' });
    });
  });
});
