import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { STORAGE_KEYS } from '@/constants';

import { authAPI, APIError, RegistrationLoginError } from '@/services/api';
import { wukongimWebSocketService } from '@/services/wukongimWebSocket';
import { useChatStore } from './chatStore';
import type { LoginFormData, RegisterFormData } from '@/types';


interface User {
  id: string;
  project_id: string;
  username: string;
  nickname: string | null;
  avatar_url: string | null;
  role: 'user' | 'admin' | 'agent';
  status: 'online' | 'offline' | 'busy';
  agent_id: string | null;
  created_at: string;
  updated_at: string;
}

interface AuthState {
  // State
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;

  // Actions
  login: (credentials: LoginFormData) => Promise<void>;
  register: (userData: RegisterFormData) => Promise<void>;
  logout: () => Promise<void>;
  clearError: () => void;
  setLoading: (loading: boolean) => void;
}

/**
 * Authentication Store
 * Manages user authentication state and actions
 */
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      // Initial state
      user: null,
      token: null,
      isAuthenticated: false,
      isLoading: false,
      error: null,

      // Login action
      login: async (credentials: LoginFormData) => {
        set({ isLoading: true, error: null });

        try {
          // Call real API
          const response = await authAPI.login({
            username: credentials.email, // Using email as username
            password: credentials.password
          });

          // Convert API response to User format
          const user: User = {
            id: response.staff.id,
            project_id: response.staff.project_id,
            username: response.staff.username,
            nickname: response.staff.nickname,
            avatar_url: response.staff.avatar_url,
            role: response.staff.role,
            status: response.staff.status,
            agent_id: response.staff.agent_id,
            created_at: response.staff.created_at,
            updated_at: response.staff.updated_at
          };

          console.log('🔐 Auth Store: Login successful, setting token', {
            hasToken: !!response.access_token,
            tokenLength: response.access_token?.length || 0,
            userId: user.id
          });

          set({
            user,
            token: response.access_token,
            isAuthenticated: true,
            isLoading: false,
            error: null
          });

          console.log('🔐 Auth Store: State updated after login');

        } catch (error) {
          let errorMessage = '登录失败';

          if (error instanceof APIError) {
            errorMessage = error.getUserMessage();
          } else if (error instanceof Error) {
            errorMessage = error.message;
          }
          set({
            isLoading: false,
            error: errorMessage
          });
          throw error;
        }
      },

      // Register action
      register: async (userData: RegisterFormData) => {
        set({ isLoading: true, error: null });

        try {
          // Call real API for registration
          const registeredAccount = await authAPI.register({
            username: userData.email, // Using email as username
            password: userData.password,
            nickname: userData.email.split('@')[0],
            project_name: userData.workspaceName.trim(),
          });

          // After successful registration, automatically log in
          const loginResponse = await authAPI.login({
            username: registeredAccount.username,
            password: userData.password
          }).catch(() => { throw new RegistrationLoginError(); });

          // Convert API response to User format
          const user: User = {
            id: loginResponse.staff.id,
            project_id: loginResponse.staff.project_id,
            username: loginResponse.staff.username,
            nickname: loginResponse.staff.nickname,
            avatar_url: loginResponse.staff.avatar_url,
            role: loginResponse.staff.role,
            status: loginResponse.staff.status,
            agent_id: loginResponse.staff.agent_id,
            created_at: loginResponse.staff.created_at,
            updated_at: loginResponse.staff.updated_at
          };

          console.log('🔐 Auth Store: Registration successful, setting token', {
            hasToken: !!loginResponse.access_token,
            tokenLength: loginResponse.access_token?.length || 0,
            userId: user.id
          });

          set({
            user,
            token: loginResponse.access_token, // ← FIXED: Missing token assignment
            isAuthenticated: true,
            isLoading: false,
            error: null
          });

          console.log('🔐 Auth Store: State updated after registration');

        } catch (error) {
          let errorMessage = '注册失败';

          if (error instanceof APIError) {
            errorMessage = error.getUserMessage();
          } else if (error instanceof Error) {
            errorMessage = error.message;
          }

          set({
            isLoading: false,
            error: errorMessage
          });
          throw error;
        }
      },

      // Logout action
      logout: async () => {
        console.log('🔐 Auth Store: Starting logout process');

        try {
          // 1. Disconnect WebSocket connection
          console.log('🔐 Auth Store: Disconnecting WebSocket');
          wukongimWebSocketService.safeDisconnect();

          // 2. Clear chat store data
          console.log('🔐 Auth Store: Clearing chat store data');
          try {
            const clearChatStore = useChatStore.getState().clearStore;
            clearChatStore();
          } catch (error) {
            console.warn('🔐 Auth Store: Failed to clear chat store:', error);
          }

          // 3. Clear API token
          console.log('🔐 Auth Store: Clearing API token');
          authAPI.logout();

          // 4. Clear auth state
          set({
            user: null,
            token: null,
            isAuthenticated: false,
            error: null
          });

          // 4. Clear all localStorage data
          console.log('🔐 Auth Store: Clearing localStorage');
          const keysToRemove = [
            STORAGE_KEYS.AUTH,
            STORAGE_KEYS.CHAT,
            STORAGE_KEYS.UI,
            STORAGE_KEYS.AUTH_TOKEN
          ];

          keysToRemove.forEach(key => {
            try {
              localStorage.removeItem(key);
            } catch (error) {
              console.warn(`Failed to remove localStorage key: ${key}`, error);
            }
          });

          // 5. Clear sessionStorage as well
          try {
            sessionStorage.clear();
          } catch (error) {
            console.warn('Failed to clear sessionStorage:', error);
          }

          console.log('🔐 Auth Store: Logout completed successfully');

          // 6. Navigate to login page
          // Use window.location to ensure complete page refresh and prevent back navigation
          window.location.href = '/login';

        } catch (error) {
          console.error('🔐 Auth Store: Error during logout:', error);

          // Even if there's an error, still clear the auth state and redirect
          set({
            user: null,
            token: null,
            isAuthenticated: false,
            error: null
          });

          window.location.href = '/login';
        }
      },

      // Clear error action
      clearError: () => {
        set({ error: null });
      },

      // Set loading action
      setLoading: (loading: boolean) => {
        set({ isLoading: loading });
      }
    }),
    {
      name: STORAGE_KEYS.AUTH,
      partialize: (state) => {
        const persistedData = {
          user: state.user,
          token: state.token,
          isAuthenticated: state.isAuthenticated
        };

        console.log('🔐 Auth Store: Persisting to localStorage', {
          hasUser: !!persistedData.user,
          hasToken: !!persistedData.token,
          tokenLength: persistedData.token?.length || 0,
          isAuthenticated: persistedData.isAuthenticated
        });

        return persistedData;
      },
      onRehydrateStorage: () => {
        console.log('🔐 Auth Store: Starting rehydration from localStorage');

        return (state, error) => {
          if (error) {
            console.error('🔐 Auth Store: Rehydration failed', error);
          } else {
            console.log('🔐 Auth Store: Rehydration successful', {
              hasUser: !!state?.user,
              hasToken: !!state?.token,
              tokenLength: state?.token?.length || 0,
              isAuthenticated: !!state?.isAuthenticated
            });
          }
        };
      }
    }
  )
);
