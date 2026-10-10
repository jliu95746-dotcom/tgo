import React, { useEffect, useContext } from 'react';
import { useTranslation } from 'react-i18next';
import { RouterProvider } from 'react-router-dom';
import { router } from './router';
import ToastContainer, { ToastContext } from './components/ui/ToastContainer';
import { WebSocketManager } from './components/WebSocketManager';
import { QueueNotificationManager } from './components/QueueNotificationManager';
import { useStoreInitialization } from './hooks/useStoreInitialization';
import { setUnauthorizedHandler } from '@/services/api';
import { useAuthStore } from '@/stores/authStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { useStaffSession } from '@/hooks/useStaffSession';

/**
 * Main App component with React Router and centralized WebSocket management
 */
const App: React.FC = () => {
  // Initialize stores (platforms, chats, etc.) once at app start
  const sessionReady = useStaffSession();
  useStoreInitialization(sessionReady);
  const toast = useContext(ToastContext);
  const { t } = useTranslation();
  const { themeMode } = useAppSettingsStore();

  // Apply theme based on user preference
  useEffect(() => {
    const root = document.documentElement;

    const applyTheme = (isDark: boolean) => {
      console.log('[Theme] Applying theme:', isDark ? 'dark' : 'light');
      if (isDark) {
        root.classList.add('dark');
        console.log('[Theme] Added dark class to html element');
      } else {
        root.classList.remove('dark');
        console.log('[Theme] Removed dark class from html element');
      }
      console.log('[Theme] Current classes:', root.className);
    };

    console.log('[Theme] Theme mode changed to:', themeMode);

    if (themeMode === 'system') {
      // Follow system preference
      const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
      console.log('[Theme] System prefers dark mode:', mediaQuery.matches);
      applyTheme(mediaQuery.matches);

      // Listen for system theme changes
      const handleChange = (e: MediaQueryListEvent) => {
        console.log('[Theme] System theme changed to:', e.matches ? 'dark' : 'light');
        applyTheme(e.matches);
      };

      mediaQuery.addEventListener('change', handleChange);
      return () => mediaQuery.removeEventListener('change', handleChange);
    } else {
      // Apply user preference
      applyTheme(themeMode === 'dark');
      return undefined;
    }
  }, [themeMode]);

  useEffect(() => {
    let isLoggingOut = false;
    setUnauthorizedHandler(() => {
      const { isAuthenticated, logout } = useAuthStore.getState();
      if (isLoggingOut) return;
      if (isAuthenticated) {
        isLoggingOut = true;
        try { toast?.showToast('warning', t('auth.sessionExpiredTitle', '会话已过期'), t('auth.sessionExpiredMessage', '请重新登录')); } catch {}
        // Persist a flash message for the login page to display after redirect
        try {
          localStorage.setItem('auth-flash', JSON.stringify({ message: t('auth.sessionExpiredMessage', '登录已过期，请重新登录') }));
        } catch {}
        logout();
      }
    });
  }, [toast, t]);

  if (!sessionReady) return <div role="status">{t('common.loading', '加载中…')}</div>;

  return (
    <ToastContainer>
      {/* Centralized WebSocket connection management */}
      <WebSocketManager />
      <QueueNotificationManager />
      <RouterProvider router={router} />
    </ToastContainer>
  );
};

export default App;
