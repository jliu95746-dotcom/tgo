import React, { useState, useRef } from 'react';
import { X, Globe, Plus, Trash2, Terminal, Settings, ListTree, Info } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useToast } from './ToolToastProvider';
import { ProjectToolsApiService } from '@/services/projectToolsApi';
import { useProjectToolsStore } from '@/stores/projectToolsStore';
import { useAuthStore } from '@/stores/authStore';
import type { AiToolCreateRequest, HttpToolParameter } from '@/types';

interface AddHTTPToolModalProps {
  isOpen: boolean;
  onClose: () => void;
}

interface HeaderItem {
  key: string;
  value: string;
}

interface FormData {
  name: string;
  description: string;
  endpoint: string;
  method: string;
  headers: HeaderItem[];
  parameters: HttpToolParameter[];
}

interface FormErrors {
  name?: string;
  description?: string;
  endpoint?: string;
}

const AddHTTPToolModal: React.FC<AddHTTPToolModalProps> = ({ isOpen, onClose }) => {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { loadTools } = useProjectToolsStore();
  const { user } = useAuthStore();

  const [formData, setFormData] = useState<FormData>({
    name: '',
    description: '',
    endpoint: '',
    method: 'POST',
    headers: [{ key: 'Content-Type', value: 'application/json' }],
    parameters: [],
  });

  const [errors, setErrors] = useState<FormErrors>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const submittingRef = useRef(false);

  const handleInputChange = (field: keyof FormData, value: any) => {
    setFormData(prev => ({ ...prev, [field]: value }));
    if (errors[field as keyof FormErrors]) {
      setErrors(prev => ({ ...prev, [field]: undefined }));
    }
  };

  const addHeader = () => {
    setFormData(prev => ({
      ...prev,
      headers: [...prev.headers, { key: '', value: '' }]
    }));
  };

  const removeHeader = (index: number) => {
    setFormData(prev => ({
      ...prev,
      headers: prev.headers.filter((_, i) => i !== index)
    }));
  };

  const updateHeader = (index: number, field: keyof HeaderItem, value: string) => {
    setFormData(prev => ({
      ...prev,
      headers: prev.headers.map((h, i) => i === index ? { ...h, [field]: value } : h)
    }));
  };

  const addParameter = () => {
    setFormData(prev => ({
      ...prev,
      parameters: [...prev.parameters, { name: '', type: 'string', description: '', required: true }]
    }));
  };

  const removeParameter = (index: number) => {
    setFormData(prev => ({
      ...prev,
      parameters: prev.parameters.filter((_, i) => i !== index)
    }));
  };

  const updateParameter = (index: number, field: keyof HttpToolParameter, value: any) => {
    setFormData(prev => ({
      ...prev,
      parameters: prev.parameters.map((p, i) => i === index ? { ...p, [field]: value } : p)
    }));
  };

  const validateForm = (): boolean => {
    const newErrors: FormErrors = {};

    if (!formData.name.trim()) {
      newErrors.name = t('tools.addHttpToolModal.errors.nameRequired', '请输入工具名称');
    } else if (!/^[a-zA-Z0-9_-]+$/.test(formData.name.trim())) {
      newErrors.name = t('tools.addHttpToolModal.errors.invalidName', '工具名称只能包含英文字母、数字、下划线和连字符');
    }

    if (!formData.description.trim()) {
      newErrors.description = t('tools.addHttpToolModal.errors.descriptionRequired', '请输入工具描述');
    }

    if (!formData.endpoint.trim()) {
      newErrors.endpoint = t('tools.addHttpToolModal.errors.endpointRequired', '请输入接口地址');
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submittingRef.current) return;

    if (!validateForm()) {
      return;
    }

    if (!user?.project_id) {
      showToast('error', t('common.error', '错误'), t('tools.addToolModal.errors.noProject', '未选择项目'));
      return;
    }

    submittingRef.current = true;
    setIsSubmitting(true);

    try {
      const headersMap: Record<string, string> = {};
      formData.headers.forEach(h => {
        if (h.key.trim()) {
          headersMap[h.key.trim()] = h.value;
        }
      });

      const requestData: AiToolCreateRequest = {
        project_id: user.project_id,
        name: formData.name.trim(),
        description: formData.description.trim(),
        tool_type: 'FUNCTION',
        transport_type: 'http_webhook',
        endpoint: formData.endpoint.trim(),
        config: {
          method: formData.method,
          headers: headersMap,
          parameters: formData.parameters.filter(p => p.name.trim()),
        },
      };

      await ProjectToolsApiService.createAiTool(requestData);

      showToast(
        'success',
        t('tools.addHttpToolModal.success.title', '添加成功'),
        t('tools.addHttpToolModal.success.message', 'HTTP 工具 {{name}} 已成功添加', { name: formData.name })
      );

      await loadTools(false);
      handleClose();
    } catch (error) {
      console.error('Failed to add HTTP tool:', error);
      showToast(
        'error',
        t('tools.addHttpToolModal.error.title', '添加失败'),
        error instanceof Error ? error.message : t('tools.addHttpToolModal.error.message', '添加 HTTP 工具失败')
      );
    } finally {
      submittingRef.current = false;
      setIsSubmitting(false);
    }
  };

  const handleClose = () => {
    if (!isSubmitting) {
      setFormData({
        name: '',
        description: '',
        endpoint: '',
        method: 'POST',
        headers: [{ key: 'Content-Type', value: 'application/json' }],
        parameters: [],
      });
      setErrors({});
      onClose();
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4 transition-all duration-300">
      <div className="bg-white dark:bg-gray-900 rounded-[2rem] shadow-2xl max-w-5xl w-full max-h-[90vh] overflow-hidden flex flex-col border border-gray-100 dark:border-gray-800 animate-in fade-in zoom-in-95 duration-300">
        {/* Header */}
        <div className="flex items-center justify-between p-8 border-b border-gray-100 dark:border-gray-800 bg-white/50 dark:bg-gray-900/50 backdrop-blur-md sticky top-0 z-10">
          <div className="flex items-center space-x-4">
            <div className="w-12 h-12 rounded-2xl bg-blue-50 dark:bg-blue-900/20 flex items-center justify-center border border-blue-100 dark:border-blue-800">
              <Globe className="w-6 h-6 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h2 className="text-xl font-bold text-gray-900 dark:text-gray-100">
                {t('tools.addHttpToolModal.title', '添加 HTTP 工具')}
              </h2>
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
                {t('tools.addHttpToolModal.subtitle', '配置外部 API 接口作为 AI 员工的能力')}
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

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto custom-scrollbar bg-gray-50/30 dark:bg-gray-950/30 p-8 space-y-8">
          
          {/* Section 1: Basic Info */}
          <section className="bg-white dark:bg-gray-900 rounded-3xl p-6 border border-gray-100 dark:border-gray-800 shadow-sm space-y-6">
            <div className="flex items-center gap-2 mb-2 text-blue-600 dark:text-blue-400">
              <Info className="w-5 h-5" />
              <h3 className="font-bold">{t('tools.addHttpToolModal.sections.basic', '基本信息')}</h3>
            </div>
            
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div className="space-y-2">
                <label className="block text-sm font-bold text-gray-700 dark:text-gray-200">
                  {t('tools.addHttpToolModal.fields.name', '工具名称')}
                  <span className="text-red-500 ml-1">*</span>
                </label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => handleInputChange('name', e.target.value)}
                  className={`w-full px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border rounded-xl focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all dark:text-white outline-none ${errors.name ? 'border-red-500' : 'border-gray-200 dark:border-gray-700'}`}
                  placeholder={t('tools.addHttpToolModal.placeholders.name', '例如: search_user')}
                />
                {errors.name && <p className="text-xs text-red-500 font-medium">{errors.name}</p>}
              </div>

              <div className="space-y-2">
                <label className="block text-sm font-bold text-gray-700 dark:text-gray-200">
                  {t('tools.addHttpToolModal.fields.description', '工具描述')}
                  <span className="text-red-500 ml-1">*</span>
                </label>
                <input
                  type="text"
                  value={formData.description}
                  onChange={(e) => handleInputChange('description', e.target.value)}
                  className={`w-full px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border rounded-xl focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all dark:text-white outline-none ${errors.description ? 'border-red-500' : 'border-gray-200 dark:border-gray-700'}`}
                  placeholder={t('tools.addHttpToolModal.placeholders.description', '描述工具的功能，帮助 AI 理解调用时机')}
                />
                {errors.description && <p className="text-xs text-red-500 font-medium">{errors.description}</p>}
              </div>
            </div>
          </section>

          {/* Section 2: API Configuration */}
          <section className="bg-white dark:bg-gray-900 rounded-3xl p-6 border border-gray-100 dark:border-gray-800 shadow-sm space-y-6">
            <div className="flex items-center gap-2 mb-2 text-purple-600 dark:text-purple-400">
              <Settings className="w-5 h-5" />
              <h3 className="font-bold">{t('tools.addHttpToolModal.sections.config', '接口配置')}</h3>
            </div>

            <div className="space-y-6">
              {/* Method + URL Row */}
              <div className="flex flex-col sm:flex-row gap-4">
                <div className="sm:w-32">
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-200 mb-2">
                    {t('tools.addHttpToolModal.fields.method', '请求方法')}
                  </label>
                  <select
                    value={formData.method}
                    onChange={(e) => handleInputChange('method', e.target.value)}
                    className="w-full px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-xl focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none dark:text-white appearance-none cursor-pointer"
                  >
                    <option value="GET">GET</option>
                    <option value="POST">POST</option>
                    <option value="PUT">PUT</option>
                    <option value="DELETE">DELETE</option>
                    <option value="PATCH">PATCH</option>
                  </select>
                </div>
                <div className="flex-1">
                  <label className="block text-sm font-bold text-gray-700 dark:text-gray-200 mb-2">
                    {t('tools.addHttpToolModal.fields.endpoint', '接口地址 (Endpoint)')}
                    <span className="text-red-500 ml-1">*</span>
                  </label>
                  <input
                    type="text"
                    value={formData.endpoint}
                    onChange={(e) => handleInputChange('endpoint', e.target.value)}
                    className={`w-full px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border rounded-xl focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all dark:text-white outline-none ${errors.endpoint ? 'border-red-500' : 'border-gray-200 dark:border-gray-700'}`}
                    placeholder={t('tools.addHttpToolModal.placeholders.endpoint', 'https://api.example.com/v1/users')}
                  />
                  {errors.endpoint && <p className="text-xs text-red-500 font-medium mt-1">{errors.endpoint}</p>}
                </div>
              </div>

              {/* Headers Group */}
              <div className="space-y-4 pt-2">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-bold text-gray-700 dark:text-gray-200">
                    {t('tools.addHttpToolModal.fields.headers', '请求头 (Headers)')}
                  </label>
                  <button
                    type="button"
                    onClick={addHeader}
                    className="px-3 py-1.5 bg-purple-50 dark:bg-purple-900/20 text-purple-600 dark:text-purple-400 text-xs font-bold rounded-lg hover:bg-purple-100 transition-all flex items-center"
                  >
                    <Plus className="w-3.5 h-3.5 mr-1" /> {t('tools.addHttpToolModal.addHeader', '添加 Header')}
                  </button>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  {formData.headers.map((header, index) => (
                    <div key={index} className="flex items-center space-x-2 group animate-in slide-in-from-bottom-1 duration-200">
                      <div className="flex flex-1 items-center bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-xl overflow-hidden focus-within:ring-2 focus-within:ring-blue-500/20 focus-within:border-blue-500 transition-all">
                        <input
                          type="text"
                          value={header.key}
                          onChange={(e) => updateHeader(index, 'key', e.target.value)}
                          placeholder="Key"
                          className="w-1/3 px-3 py-2 bg-transparent text-sm border-r border-gray-200 dark:border-gray-700 outline-none dark:text-white"
                        />
                        <input
                          type="text"
                          value={header.value}
                          onChange={(e) => updateHeader(index, 'value', e.target.value)}
                          placeholder="Value"
                          className="flex-1 px-3 py-2 bg-transparent text-sm outline-none dark:text-white"
                        />
                      </div>
                      <button
                        type="button"
                        onClick={() => removeHeader(index)}
                        className="p-2.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-xl transition-all"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </section>

          {/* Section 3: Parameters */}
          <section className="bg-white dark:bg-gray-900 rounded-3xl p-6 border border-gray-100 dark:border-gray-800 shadow-sm space-y-6">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2 text-green-600 dark:text-green-400">
                <ListTree className="w-5 h-5" />
                <h3 className="font-bold">{t('tools.addHttpToolModal.fields.parameters', '输入参数')}</h3>
              </div>
              <button
                type="button"
                onClick={addParameter}
                className="px-4 py-2 bg-green-600 hover:bg-green-700 text-white text-sm font-bold rounded-xl transition-all flex items-center shadow-lg shadow-green-200 dark:shadow-none active:scale-95"
              >
                <Plus className="w-4 h-4 mr-1.5" /> {t('tools.addHttpToolModal.addParameter', '添加参数')}
              </button>
            </div>
            
            {formData.parameters.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 bg-gray-50/50 dark:bg-gray-800/30 rounded-3xl border border-dashed border-gray-200 dark:border-gray-700">
                <div className="w-12 h-12 rounded-2xl bg-gray-100 dark:bg-gray-800 flex items-center justify-center text-gray-400 mb-3">
                  <Terminal className="w-6 h-6" />
                </div>
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  {t('tools.addHttpToolModal.params.empty', '定义 AI 调用接口时需要的动态参数')}
                </p>
              </div>
            ) : (
              <div className="overflow-hidden border border-gray-100 dark:border-gray-800 rounded-2xl">
                <table className="w-full text-sm text-left">
                  <thead className="bg-gray-50/80 dark:bg-gray-800 text-gray-700 dark:text-gray-200 font-bold border-b border-gray-100 dark:border-gray-800">
                    <tr>
                      <th className="px-4 py-4 w-1/4">{t('tools.addHttpToolModal.params.name', '参数名')}</th>
                      <th className="px-4 py-4 w-1/6">{t('tools.addHttpToolModal.params.type', '类型')}</th>
                      <th className="px-4 py-4">{t('tools.addHttpToolModal.params.desc', '描述')}</th>
                      <th className="px-4 py-4 w-20 text-center">{t('tools.addHttpToolModal.params.required', '必填')}</th>
                      <th className="px-4 py-4 w-16 text-center">{t('common.action', '操作')}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-50 dark:divide-gray-800 bg-white dark:bg-gray-900">
                    {formData.parameters.map((param, index) => (
                      <tr key={index} className="group hover:bg-gray-50/30 dark:hover:bg-gray-800/30 transition-colors">
                        <td className="px-2 py-3">
                          <input
                            type="text"
                            value={param.name}
                            onChange={(e) => updateParameter(index, 'name', e.target.value)}
                            className="w-full px-3 py-2 bg-transparent border border-transparent hover:border-gray-200 dark:hover:border-gray-700 focus:bg-gray-50 dark:focus:bg-gray-800 rounded-lg outline-none transition-all dark:text-white"
                            placeholder="e.g. user_id"
                          />
                        </td>
                        <td className="px-2 py-3">
                          <select
                            value={param.type}
                            onChange={(e) => updateParameter(index, 'type', e.target.value)}
                            className="w-full px-3 py-2 bg-transparent border border-transparent hover:border-gray-200 dark:hover:border-gray-700 focus:bg-gray-50 dark:focus:bg-gray-800 rounded-lg outline-none transition-all dark:text-white cursor-pointer appearance-none"
                          >
                            <option value="string">String</option>
                            <option value="number">Number</option>
                            <option value="boolean">Boolean</option>
                            <option value="object">Object</option>
                            <option value="array">Array</option>
                          </select>
                        </td>
                        <td className="px-2 py-3">
                          <input
                            type="text"
                            value={param.description}
                            onChange={(e) => updateParameter(index, 'description', e.target.value)}
                            className="w-full px-3 py-2 bg-transparent border border-transparent hover:border-gray-200 dark:hover:border-gray-700 focus:bg-gray-50 dark:focus:bg-gray-800 rounded-lg outline-none transition-all dark:text-white"
                            placeholder={t('tools.addHttpToolModal.placeholders.paramDesc', '参数功能描述...')}
                          />
                        </td>
                        <td className="px-2 py-3 text-center">
                          <input
                            type="checkbox"
                            checked={param.required}
                            onChange={(e) => updateParameter(index, 'required', e.target.checked)}
                            className="w-5 h-5 rounded-md border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500 transition-all cursor-pointer mx-auto"
                          />
                        </td>
                        <td className="px-2 py-3 text-center">
                          <button
                            type="button"
                            onClick={() => removeParameter(index)}
                            className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-all"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </form>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 p-8 border-t border-gray-100 dark:border-gray-800 bg-white dark:bg-gray-900 sticky bottom-0 z-10">
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
            className="px-8 py-2.5 text-sm font-bold text-white bg-blue-600 hover:bg-blue-700 rounded-xl shadow-lg shadow-blue-200 dark:shadow-none transition-all active:scale-95 disabled:opacity-50 disabled:active:scale-100"
          >
            {isSubmitting ? (
              <div className="flex items-center">
                <svg className="animate-spin -ml-1 mr-2 h-4 w-4 text-white" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                {t('common.submitting', '提交中...')}
              </div>
            ) : t('tools.addHttpToolModal.confirm', '确认创建')}
          </button>
        </div>
      </div>
    </div>
  );
};

export default AddHTTPToolModal;
