import React, { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import OnboardingWelcome from '@/components/onboarding/OnboardingWelcome';
import { useOnboardingStore } from '@/stores/onboardingStore';
import NotificationPermissionNotice from '@/components/notifications/NotificationPermissionNotice';
import TrialActivationButton from './TrialActivationButton';

/**
 * Main layout component with sidebar and content area
 */
const Layout: React.FC = () => {
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
    <div className="flex flex-col bg-gray-100 dark:bg-gray-900 h-screen overflow-hidden font-sans antialiased">
      <NotificationPermissionNotice />
      <TrialActivationButton onStatus={setTrialStatus} />
      <div className="flex flex-1 min-h-0 w-full">
        {/* Sidebar Navigation */}
        <Sidebar />

        {/* Main Content */}
        <Outlet />
      </div>

      {/* Onboarding Welcome Modal */}
      {trialStatus === 'ready' && <OnboardingWelcome />}
    </div>
  );
};

export default Layout;
