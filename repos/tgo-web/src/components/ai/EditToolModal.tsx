import React, { useState, useEffect, useRef } from 'react';
import { X, Edit, AlertCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useToast } from './ToolToastProvider';
import { useProjectToolsStore } from '@/stores/projectToolsStore';
import type { AiToolResponse, AiToolUpdateRequest } from '@/types';
import MCPHeadersField from './MCPHeadersField';
import { readMcpHeaders, serializeMcpHeaders, type MCPHeaderInput } from '@/utils/mcpHeaders';

interface EditToolModalProps {
  isOpen: boolean;
  onClose: () => void;
  tool: AiToolResponse | null;
}

interface FormData {
  headers: MCPHeaderInput[];
  name: string;
  description: string;
  transport_type: string;
  endpoint: string;
}

interface FormErrors {
  headers?: string;
  name?: string;
  description?: string;
  transport_type?: string;
  endpoint?: string;
}

const EditToolModal: React.FC<EditToolModalProps> = ({ isOpen, onClose, tool }) => {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { updateTool, loadTools } = useProjectToolsStore();

  const [formData, setFormData] = useState<FormData>({
    headers: [],
    name: '',
    description: '',
    transport_type: 'http',
    endpoint: '',
  });

  const [errors, setErrors] = useState<FormErrors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const submittingRef = useRef(false);

  // Populate form when tool changes
  useEffect(() => {
    if (tool) {
      setFormData({
        headers: readMcpHeaders(tool.config?.headers),
        name: tool.name || '',
        description: tool.description || '',
        transport_type: tool.transport_type || 'http',
        endpoint: tool.endpoint || '',
      });
      setErrors({});
    }
  }, [tool]);

  const handleInputChange = (field: Exclude<keyof FormData, 'headers'>, value: string) => {
    setFormData(prev => ({ ...prev, [field]: value }));
    // Clear error when user starts typing
    if (errors[field]) {
      setErrors(prev => ({ ...prev, [field]: undefined }));
    }
  };

  const validateForm = (): boolean => {
    const newErrors: FormErrors = {};
    try { serializeMcpHeaders(formData.headers); } catch {
      newErrors.headers = t('tools.mcpHeaders.invalid', '请求头名称不能重复或包含特殊空白，内容不能换行；请检查后重试。');
    }

    if (!formData.name.trim()) {
      newErrors.name = t('tools.editToolModal.errors.nameRequired', '请输入工具名称');
    } else if (!/^[a-zA-Z0-9_-]+$/.test(formData.name.trim())) {
      newErrors.name = t('tools.editToolModal.errors.invalidName', '工具名称只能包含英文字母、数字、下划线和连字符');
    }

    if (!formData.endpoint.trim()) {
      newErrors.endpoint = t('tools.editToolModal.errors.endpointRequired', '请输入端点地址');
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submittingRef.current) return;

    if (!validateForm() || !tool) {
      return;
    }

    submittingRef.current = true;
    setIsSubmitting(true);

    try {
      const updateData: AiToolUpdateRequest = {
        name: formData.name.trim(),
        description: formData.description.trim() || null,
        transport_type: formData.transport_type.trim() || null,
        endpoint: formData.endpoint.trim() || null,
        config: { ...tool.config, headers: serializeMcpHeaders(formData.headers) },
      };

      await updateTool(tool.id, updateData);

      showToast(
        'success',
        t('tools.editToolModal.success.title', '更新成功'),
        t('tools.editToolModal.success.message', '{{name}} 已成功更新', { name: formData.name })
      );

      // Refresh tool list
      await loadTools(false);

      onClose();
    } catch (error) {
      console.error('Failed to update tool:', error);
      showToast(
        'error',
        t('tools.editToolModal.error.title', '更新失败'),
        error instanceof Error ? error.message : t('tools.editToolModal.error.message', '更新工具失败，请稍后重试')
      );
    } finally {
      submittingRef.current = false;
      setIsSubmitting(false);
    }
  };

  const handleClose = () => {
    if (!isSubmitting) {
      setErrors({});
      onClose();
    }
  };

  if (!isOpen || !tool) return null;

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4 transition-all duration-300">
      <div className="bg-white dark:bg-gray-900 rounded-[2rem] shadow-2xl max-w-2xl w-full max-h-[90vh] overflow-hidden flex flex-col border border-gray-100 dark:border-gray-800 animate-in fade-in zoom-in-95 duration-300">
        {/* Header */}
        <div className="flex items-center justify-between p-8 border-b border-gray-100 dark:border-gray-800 bg-white/50 dark:bg-gray-900/50 backdrop-blur-md sticky top-0 z-10">
          <div className="flex items-center space-x-4">
            <div className="w-12 h-12 rounded-2xl bg-blue-50 dark:bg-blue-900/20 flex items-center justify-center border border-blue-100 dark:border-blue-800">
              <Edit className="w-6 h-6 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100">
                {t('tools.editToolModal.title', '编辑工具')}
              </h2>
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                {t('tools.editToolModal.subtitle', '修改 MCP 工具的连接配置')}
              </p>
            </div>
          </div>
          <button
            onClick={handleClose}
            disabled={isSubmitting}
            className="p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-xl transition-all"
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto p-8 space-y-6 custom-scrollbar">
          {/* Tool Name */}
          <div className="space-y-2">
            <label className="block text-sm font-bold text-gray-700 dark:text-gray-200">
              {t('tools.editToolModal.fields.name', '工具名称')}
              <span className="text-red-500 ml-1">*</span>
            </label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => handleInputChange('name', e.target.value)}
              className={`w-full px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border rounded-xl focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all dark:text-white outline-none ${
                errors.name ? 'border-red-500' : 'border-gray-200 dark:border-gray-700'
              }`}
              placeholder={t('tools.editToolModal.placeholders.name', '例如：weather-api')}
              disabled={isSubmitting}
            />
            {errors.name && (
              <div className="mt-2 flex items-center text-xs text-red-500 font-medium">
                <AlertCircle className="w-4 h-4 mr-1" />
                {errors.name}
              </div>
            )}
          </div>

          {/* Description */}
          <div className="space-y-2">
            <label className="block text-sm font-bold text-gray-700 dark:text-gray-200">
              {t('tools.editToolModal.fields.description', '工具描述')}
            </label>
            <textarea
              value={formData.description}
              onChange={(e) => handleInputChange('description', e.target.value)}
              rows={3}
              className="w-full px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all dark:text-white outline-none min-h-[100px]"
              placeholder={t('tools.editToolModal.placeholders.description', '简要描述该工具的功能')}
              disabled={isSubmitting}
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
            {/* Transport Type */}
            <div className="space-y-2">
              <label className="block text-sm font-bold text-gray-700 dark:text-gray-200">
                {t('tools.editToolModal.fields.transportType', '传输类型')}
              </label>
              <select
                value={formData.transport_type}
                onChange={(e) => handleInputChange('transport_type', e.target.value)}
                className="w-full px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none dark:text-white appearance-none cursor-pointer"
                disabled={isSubmitting}
              >
                <option value="http">HTTP</option>
                <option value="sse">SSE</option>
              </select>
            </div>

            {/* Endpoint */}
            <div className="space-y-2">
              <label className="block text-sm font-bold text-gray-700 dark:text-gray-200">
                {t('tools.editToolModal.fields.endpoint', '端点地址')}
                <span className="text-red-500 ml-1">*</span>
              </label>
              <input
                type="text"
                value={formData.endpoint}
                onChange={(e) => handleInputChange('endpoint', e.target.value)}
                className={`w-full px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border rounded-xl focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all dark:text-white outline-none ${
                  errors.endpoint ? 'border-red-500' : 'border-gray-200 dark:border-gray-700'
                }`}
                placeholder={t('tools.editToolModal.placeholders.endpoint', '例如：https://api.example.com/tool')}
                disabled={isSubmitting}
              />
              {errors.endpoint && (
                <div className="mt-2 flex items-center text-xs text-red-500 font-medium">
                  <AlertCircle className="w-4 h-4 mr-1" />
                  {errors.endpoint}
                </div>
              )}
            </div>
          </div>

          {/* Tool Type - Read Only */}
          <div className="space-y-2">
            <label className="block text-sm font-bold text-gray-700 dark:text-gray-200">
              {t('tools.editToolModal.fields.toolType', '工具类型')}
            </label>
            <div className="px-4 py-3 bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl text-gray-500 dark:text-gray-400 cursor-not-allowed">
              {tool.tool_type}
            </div>
            <p className="text-xs text-gray-400 dark:text-gray-500 flex items-center">
              <AlertCircle className="w-3 h-3 mr-1" />
              {t('tools.editToolModal.hints.toolTypeReadonly', '工具类型不可修改')}
            </p>
          </div>
          <MCPHeadersField value={formData.headers} disabled={isSubmitting} error={errors.headers}
            onChange={headers => { setFormData(prev => ({ ...prev, headers })); setErrors(prev => ({ ...prev, headers: undefined })); }} />
        </form>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-8 border-t border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-900/50 backdrop-blur-md">
          <button
            type="button"
            onClick={handleClose}
            disabled={isSubmitting}
            className="px-6 py-2.5 text-sm font-bold text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-xl transition-all active:scale-95"
          >
            {t('common.cancel', '取消')}
          </button>
          <button
            type="submit"
            onClick={handleSubmit}
            disabled={isSubmitting}
            className="px-8 py-2.5 text-sm font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-xl shadow-lg shadow-blue-200 dark:shadow-none transition-all active:scale-95 disabled:opacity-50 disabled:active:scale-100 disabled:cursor-not-allowed"
          >
            {isSubmitting ? (
              <div className="flex items-center">
                <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                {t('common.submitting', '提交中...')}
              </div>
            ) : t('tools.editToolModal.save', '保存修改')}
          </button>
        </div>
      </div>
    </div>
  );
};

export default EditToolModal;


