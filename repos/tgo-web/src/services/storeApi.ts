import apiClient from '@/services/api';
import type {
  ToolStoreLoginResponse, 
  ToolStoreRefreshResponse
} from '@/types';
import { STORAGE_KEYS } from '@/constants';

// 获取商店 API 地址 (通过 tgo-api 代理)
const PROXY_PATH = '/v1/store/proxy';

// 获取商店的 access token（从 localStorage 中的 Zustand 存储读取）
const getStoreAccessToken = (): string | null => {
  try {
    const authDataStr = localStorage.getItem(STORAGE_KEYS.TOOLSTORE_AUTH);
    if (authDataStr) {
      const authData = JSON.parse(authDataStr);
      return authData.state?.accessToken || null;
    }
  } catch (e) {
    console.error('Failed to get store access token', e);
  }
  return null;
};

// 获取 API base URL
const getApiBaseUrl = (): string => {
  // 优先使用运行时配置 (由 docker-entrypoint.sh 设置)
  if (typeof window !== 'undefined' && (window as any).ENV?.VITE_API_BASE_URL) {
    return (window as any).ENV.VITE_API_BASE_URL;
  }
  // 其次使用构建时环境变量
  if (import.meta.env.VITE_API_BASE_URL) {
    return import.meta.env.VITE_API_BASE_URL;
  }
  return '/api';
};

// 带商店认证的请求函数
const storeAuthFetch = async <T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> => {
  const url = `${getApiBaseUrl()}${endpoint}`;
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  // 添加 TGO API 的 token
  const tgoToken = localStorage.getItem('tgo-auth-token');
  if (tgoToken) {
    (headers as Record<string, string>)['Authorization'] = `Bearer ${tgoToken}`;
  }

  // 添加商店的 token 作为自定义头
  const storeToken = getStoreAccessToken();
  if (storeToken) {
    (headers as Record<string, string>)['X-Store-Authorization'] = `Bearer ${storeToken}`;
  }

  const response = await fetch(url, { ...options, headers });
  
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `HTTP ${response.status}`);
  }

  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
};

export const storeApi = {
  // --- 认证相关 ---
  
  login: async (credentials: { username: string; password: string }): Promise<ToolStoreLoginResponse> => {
    const response = await storeAuthFetch<ToolStoreLoginResponse>(`${PROXY_PATH}/auth/login`, {
      method: 'POST',
      body: JSON.stringify({
        username: credentials.username,
        password: credentials.password,
      }),
    });

    // 登录成功后自动绑定到当前项目
    try {
      await apiClient.post('/v1/store/bind', {
        access_token: response.access_token
      });
    } catch (e) {
      console.error('Failed to bind Store credential automatically', e);
    }

    return response;
  },

  exchangeCode: async (code: string, codeVerifier: string): Promise<ToolStoreLoginResponse> => {
    const response = await storeAuthFetch<ToolStoreLoginResponse>(`${PROXY_PATH}/auth/exchange?code=${code}&code_verifier=${codeVerifier}`, {
      method: 'POST',
    });

    // 交换成功后自动绑定到当前项目
    try {
      await apiClient.post('/v1/store/bind', {
        access_token: response.access_token
      });
    } catch (e) {
      console.error('Failed to bind Store credential automatically', e);
    }

    return response;
  },

  refreshToken: async (refreshToken: string): Promise<ToolStoreRefreshResponse> => {
    const response = await storeAuthFetch<ToolStoreRefreshResponse>(`${PROXY_PATH}/auth/refresh?refresh_token=${refreshToken}`, {
      method: 'POST',
    });
    return response;
  },

  logout: async (refreshToken: string): Promise<void> => {
    await storeAuthFetch(`${PROXY_PATH}/auth/logout?refresh_token=${refreshToken}`, {
      method: 'POST',
    });
  },

  getMe: async () => {
    const response = await storeAuthFetch<any>(`${PROXY_PATH}/auth/me`, {
      method: 'GET',
    });
    return response;
  },

  // --- 工具相关 ---


  getStoreConfig: async () => {
    const response = await apiClient.get<{ store_web_url: string; store_api_url: string }>('/v1/store/config');
    return response;
  },
};

export default storeApi;
