import React from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { X, Info } from 'lucide-react';

interface AboutModalProps {
  isOpen: boolean;
  onClose: () => void;
}

const AboutModal: React.FC<AboutModalProps> = ({ isOpen, onClose }) => {
  const { t } = useTranslation();

  if (!isOpen) return null;

  const version = import.meta.env?.VITE_APP_VERSION || '1.0.0';
  const env = import.meta.env?.MODE || 'development';
  const apiBase = import.meta.env?.VITE_API_BASE_URL || 'http://localhost:8000';

  const handleModalClick = (e: React.MouseEvent) => {
    e.stopPropagation();
  };

  // Ensure we have a valid DOM element to portal to
  if (typeof document === 'undefined') return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[9999] bg-black/40 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-white dark:bg-gray-800 w-full max-w-md rounded-lg shadow-lg overflow-hidden about-modal-fix"
        onClick={handleModalClick}
        style={{ minWidth: '320px' }}
      >
        {/* 标题栏 */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
          <div className="flex items-center gap-2">
            <Info className="w-5 h-5 text-blue-600 dark:text-blue-400 flex-shrink-0" />
            <h3 className="text-base font-semibold text-gray-800 dark:text-gray-100 whitespace-nowrap">
              {t('common.about', '关于')}
            </h3>
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            title={t('common.close', '关闭')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* 内容区域 */}
        <div className="px-4 py-4 text-sm text-gray-700 dark:text-gray-300 space-y-4">
          <div className="flex items-center justify-between">
            <span className="text-gray-600 dark:text-gray-400 whitespace-nowrap">
              {t('about.version', '版本')}
            </span>
            <span className="font-mono text-gray-800 dark:text-gray-100 ml-4">{version}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-gray-600 dark:text-gray-400 whitespace-nowrap">
              {t('about.environment', '环境')}
            </span>
            <span className="font-mono text-gray-800 dark:text-gray-100 ml-4">{env}</span>
          </div>
          <div className="space-y-2">
            <div className="text-gray-600 dark:text-gray-400">
              {t('about.apiEndpoint', '接口地址')}
            </div>
            <div
              className="font-mono text-xs text-gray-800 dark:text-gray-100 bg-gray-50 dark:bg-gray-700 p-2 rounded border dark:border-gray-600"
              style={{
                wordBreak: 'break-all',
                overflowWrap: 'break-word',
                writingMode: 'horizontal-tb',
                direction: 'ltr'
              }}
            >
              {apiBase}
            </div>
          </div>
          <div className="text-xs text-gray-500 dark:text-gray-400 pt-2 border-t border-gray-100 dark:border-gray-700">
            {t('brand.name')} · {t('brand.description')}
          </div>
        </div>

        {/* 底部按钮 */}
        <div className="px-4 py-3 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm bg-blue-600 dark:bg-blue-700 text-white rounded-md hover:bg-blue-700 dark:hover:bg-blue-800 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2"
          >
            {t('common.close', '关闭')}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
};

export default AboutModal;

