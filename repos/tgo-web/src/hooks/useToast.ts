import { useContext, useMemo } from 'react';
import { ToastContext } from '@/components/ui/ToastContainer';

/**
 * Toast Hook，用于在组件中显示Toast通知
 */
export const useToast = () => {
  const context = useContext(ToastContext);
  
  if (!context) {
    throw new Error('useToast must be used within a ToastContainer');
  }

  const { showToast } = context;

  return useMemo(() => ({
    showToast,
    showSuccess: (title: string, message?: string, duration?: number) =>
      showToast('success', title, message, duration),
    showError: (title: string, message?: string, duration?: number) =>
      showToast('error', title, message, duration),
    showWarning: (title: string, message?: string, duration?: number) =>
      showToast('warning', title, message, duration),
    showInfo: (title: string, message?: string, duration?: number) =>
      showToast('info', title, message, duration),
  }), [showToast]);
};
