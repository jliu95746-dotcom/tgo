import { Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import HiddenShadowProtocols from '@/components/chat/HiddenShadowProtocols';
import { useAuthStore } from '@/stores/authStore';

/** Administrator workspace for independent shadow evidence and human signoff. */
export default function HiddenShadowAcceptance() {
  const { t } = useTranslation();
  const isAdmin = useAuthStore(state => state.user?.role === 'admin');
  if (!isAdmin) return <Navigate to="/chat" replace />;

  return <main className="min-w-0 flex-1 overflow-y-auto bg-gray-50 p-4 dark:bg-gray-900 md:p-8">
    <div className="mx-auto max-w-5xl space-y-4">
      <header>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">{t('hiddenShadow.title')}</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{t('hiddenShadow.workspaceIntro')}</p>
      </header>
      <HiddenShadowProtocols revision={0} initialOpen />
    </div>
  </main>;
}
