import React, { useEffect, useId, useRef } from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { schemaParameters, type ToolDetailView, type SchemaParameterView } from '@/utils/agentToolDetails';

interface AiToolDetailModalProps {
  tool: ToolDetailView | null;
  isOpen: boolean;
  onClose: () => void;
}

/**
 * Tool工具详情模态框组件
 */
const AiToolDetailModal: React.FC<AiToolDetailModalProps> = ({
  tool,
  isOpen,
  onClose
}) => {
  const { t } = useTranslation();
  const titleId = useId();
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!isOpen || !dialog) return;
    if (!dialog.open) dialog.showModal();
    return () => { if (dialog.open) dialog.close(); };
  }, [isOpen]);
  if (!isOpen || !tool) return null;
  const closeDialog = () => {
    dialogRef.current?.close();
    onClose();
  };

  // Helper function to render schema parameters
  const renderSchemaParameter = (paramName: string, paramData: SchemaParameterView) => {
    const isRequired = paramData.required;
    const paramType = paramData.type || t('agentToolDetails.unspecifiedType');
    const description = paramData.description || '';

    return (
      <div key={paramName} className="mb-6">
        <div className="flex items-center gap-3 mb-2">
          <span className="font-mono text-base text-gray-900 dark:text-gray-100">{paramName}</span>
          <span className="px-2 py-1 bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200 text-xs rounded font-medium">
            {paramType}
          </span>
          {isRequired && (
            <span className="px-2 py-1 bg-red-100 dark:bg-red-900 text-red-800 dark:text-red-200 text-xs rounded font-medium">
              {t('agentToolDetails.required')}
            </span>
          )}
        </div>
        {description && (
          <p className="text-sm text-gray-600 dark:text-gray-300 leading-relaxed ml-0">
            {description}
          </p>
        )}
      </div>
    );
  };

  // Extract schema parameters from input_schema
  const parameters = schemaParameters(tool.input_schema);

  return (
    <dialog ref={dialogRef} aria-labelledby={titleId} aria-modal="true"
      onCancel={event => { event.preventDefault(); closeDialog(); }}
      onClick={event => { if (event.target === event.currentTarget) closeDialog(); }}
      className="m-auto p-0 w-[calc(100%_-_2rem)] max-w-4xl rounded-lg backdrop:bg-black/50 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 shadow-xl">
      <div className="max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-8 py-6 border-b border-gray-200 dark:border-gray-700">
          <div>
            <h1 id={titleId} className="text-xl font-normal text-gray-900 dark:text-gray-100">
              <span className="text-gray-500 dark:text-gray-400">{t('agentToolDetails.title')} · </span>
              <span className="text-gray-900 dark:text-gray-100 font-medium">{tool.name}</span>
            </h1>
          </div>
          <button
            onClick={closeDialog}
            type="button"
            aria-label={t('common.close')}
            className="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="overflow-y-auto max-h-[calc(90vh-200px)]">
          <div className="px-8 py-6 space-y-8">
            {/* Description */}
            <div>
              <p className="text-gray-600 dark:text-gray-300 leading-relaxed text-base">
                {tool.description || t('agentToolDetails.noDescription')}
              </p>
            </div>

            {/* Schema Section */}
            <div>
              <h2 className="text-lg font-medium text-gray-900 dark:text-gray-100 mb-6">{t('agentToolDetails.parameters')}</h2>
              <div className="bg-gray-50 dark:bg-gray-700 rounded-lg p-6">
                {parameters.length > 0 ? (
                  <div className="space-y-6">
                    {parameters.map((param) =>
                      renderSchemaParameter(param.name, param)
                    )}
                  </div>
                ) : (
                  <div className="text-center py-8">
                    <p className="text-gray-500 dark:text-gray-400">{t('agentToolDetails.noParameters')}</p>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end space-x-3 px-8 py-6 border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <button
            onClick={closeDialog}
            type="button"
            className="px-6 py-2 text-sm font-medium text-white bg-teal-600 dark:bg-teal-700 rounded-md hover:bg-teal-700 dark:hover:bg-teal-800 focus:outline-none focus:ring-2 focus:ring-teal-500 transition-colors"
          >
            {t('common.close')}
          </button>
        </div>
      </div>
    </dialog>
  );
};

export default AiToolDetailModal;
