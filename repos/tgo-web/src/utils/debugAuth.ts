/**
 * Debug utilities for authentication and token persistence
 * Use these functions in browser console to debug auth issues
 */

/**
 * Check current localStorage auth data
 */
export const checkAuthStorage = () => {
  const rawData = localStorage.getItem('auth-storage');
  console.log('🔍 Raw localStorage data:', rawData);
  
  if (rawData) {
    try {
      const parsed = JSON.parse(rawData);
      console.log('🔍 Parsed localStorage data:', {
        hasState: !!parsed.state,
        hasUser: !!parsed.state?.user,
        hasToken: !!parsed.state?.token,
        tokenLength: parsed.state?.token?.length || 0,
        tokenPreview: parsed.state?.token ? `${parsed.state.token.substring(0, 10)}...` : 'none',
        isAuthenticated: parsed.state?.isAuthenticated,
        version: parsed.version
      });
      return parsed;
    } catch (error) {
      console.error('🔍 Failed to parse localStorage data:', error);
    }
  } else {
    console.log('🔍 No auth data found in localStorage');
  }
  return null;
};

/**
 * Clear auth storage (for testing)
 */
export const clearAuthStorage = () => {
  localStorage.removeItem('auth-storage');
  console.log('🔍 Auth storage cleared');
};

/**
 * Set test token (for testing)
 */
export const setTestToken = (token: string = 'test-token-123456789') => {
  const testData = {
    state: {
      user: {
        id: 'test-user',
        username: 'test@example.com',
        nickname: 'Test User'
      },
      token,
      isAuthenticated: true
    },
    version: 0
  };
  
  localStorage.setItem('auth-storage', JSON.stringify(testData));
  console.log('🔍 Test token set:', token);
  console.log('🔍 Refresh the page to test token persistence');
};

/**
 * Check if Zustand store is working
 */
export const checkZustandStore = () => {
  // This will be available in browser console when the store is imported
  console.log('🔍 To check Zustand store, run: window.__ZUSTAND_STORE__?.getState?.()');
  console.log('🔍 Or import useAuthStore and call useAuthStore.getState()');
};

// Make functions available globally for browser console debugging
if (typeof window !== 'undefined') {
  (window as any).debugAuth = {
    checkAuthStorage,
    clearAuthStorage,
    setTestToken,
    checkZustandStore
  };
  
  console.log('🔍 Debug functions available: window.debugAuth');
  console.log('🔍 Available methods: checkAuthStorage, clearAuthStorage, setTestToken, checkZustandStore');
}
