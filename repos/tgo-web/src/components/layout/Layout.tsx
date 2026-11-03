import React, { useEffect, useRef, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import OnboardingWelcome from '@/components/onboarding/OnboardingWelcome';
import { useOnboardingStore } from '@/stores/onboardingStore';
import NotificationPermissionNotice from '@/components/notifications/NotificationPermissionNotice';
import TrialActivationButton from './TrialActivationButton';
import MobileChatNavigation from './MobileChatNavigation';
import { useChatViewport } from '@/hooks/useChatViewport';
import '@/styles/chat-workspace.css';
import '@/styles/management-workspace.css';

/**
 * Main layout component with sidebar and content area
 */
const Layout: React.FC = () => {
  const location = useLocation();
  const isChatRoute = location.pathname === '/chat' || location.pathname.startsWith('/chat/');
  const workspace = useRef<HTMLDivElement>(null);
  useChatViewport(workspace, isChatRoute);
  const [trialStatus, setTrialStatus] = useState<'checking' | 'pending' | 'ready'>('checking');
  const {
    hasInitialized,
    isLoading,
    isCompleted,
    fetchProgress,
    startPolling,
    stopPolling
  } = useOnboardingStore();

  // Fetch onboarding progress on mount
  useEffect(() => {
    if (!hasInitialized && !isLoading) {
      fetchProgress();
    }
  }, [hasInitialized, isLoading, fetchProgress]);

  // Start polling when onboarding is not completed
  useEffect(() => {
    if (hasInitialized && !isCompleted) {
      startPolling();
    }

    // Cleanup on unmount or when completed
    return () => {
      stopPolling();
    };
  }, [hasInitialized, isCompleted, startPolling, stopPolling]);

  return (
    <div ref={workspace} data-module={location.pathname.split('/')[1]} className={`app-workspace flex flex-col bg-gray-100 dark:bg-gray-900 h-screen overflow-hidden font-sans antialiased ${isChatRoute ? 'chat-workspace' : 'mobile-management-workspace'}`}>
      <NotificationPermissionNotice />
      <TrialActivationButton onStatus={setTrialStatus} />
      <MobileChatNavigation />
      <div className="workspace-body flex flex-1 min-h-0 min-w-0 w-full">
        {/* Sidebar Navigation */}
        <div className="hidden shrink-0 min-[900px]:flex"><Sidebar /></div>

        {/* Main Content */}
        <Outlet />
      </div>

      {/* Onboarding Welcome Modal */}
      {trialStatus === 'ready' && <OnboardingWelcome />}
    </div>
  );
};

export default Layout;
