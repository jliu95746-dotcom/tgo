import React, { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Settings, Save, RotateCcw, Info } from 'lucide-react';
import type { AiTool } from '@/types';

interface ToolConfigModalProps {
  isOpen: boolean;
  onClose: () => void;
  tool: AiTool | null;
  initialConfig?: Record<string, any>;
  onSave: (toolId: string, config: Record<string, any>) => void;
}

/**
 * Tool Configuration Modal Component
 * Allows users to configure tool-specific parameters
 */
const ToolConfigModal: React.FC<ToolConfigModalProps> = ({
  isOpen,
  onClose,
  tool,
  initialConfig,
  onSave
}) => {
  const { t } = useTranslation();
  const [config, setConfig] = useState<Record<string, any>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  // Extract schema info safely
  const schema = tool?.input_schema as any | undefined;
  const schemaProps: Record<string, any> = useMemo(() => schema?.properties || {}, [schema]);
  const schemaRequired: string[] = Array.isArray(schema?.required) ? schema.required : [];

  // Initialize config based on input_schema + initialConfig
  useEffect(() => {
    if (isOpen && tool) {
      const defaults: Record<string, any> = {};
      Object.entries(schemaProps).forEach(([key, prop]: [string, any]) => {
        if (prop && Object.prototype.hasOwnProperty.call(prop, 'default')) {
          defaults[key] = prop.default;
        }
      });

      const userConfig = initialConfig || {};
      setConfig({ ...defaults, ...userConfig });
      setErrors({});
    }
  }, [isOpen, tool, initialConfig, schemaProps]);

  if (!isOpen || !tool) return null;

  const handleConfigChange = (key: string, value: any) => {
    setConfig(prev => ({
      ...prev,
      [key]: value
    }));
    
    // Clear error for this field
    if (errors[key]) {
      setErrors(prev => {
        const newErrors = { ...prev };
        delete newErrors[key];
        return newErrors;
      });
    }
  };

  const validateConfig = (): boolean => {
    const newErrors: Record<string, string> = {};
    // Validate required fields from schema
    schemaRequired.forEach((key) => {
      const val = config[key];
      if (val === undefined || val === null || val === '') {
        newErrors[key] = t('tools.config.validation.required', '此字段为必填项');
      }
    });
    // Basic numeric check based on schema type
    Object.entries(schemaProps).forEach(([key, prop]: [string, any]) => {
      const tProp = prop?.type;
      if ((tProp === 'number' || tProp === 'integer') && config[key] !== undefined) {
        const numValue = Number(config[key]);
        if (Number.isNaN(numValue)) {
          newErrors[key] = t('tools.config.validation.number', '请输入有效的数字');
        }
      }
    });
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSave = () => {
    if (validateConfig()) {
      onSave(tool.id, config);
      onClose();
    }
  };

  const handleReset = () => {
    const defaults: Record<string, any> = {};
    Object.entries(schemaProps).forEach(([key, prop]: [string, any]) => {
      if (prop && Object.prototype.hasOwnProperty.call(prop, 'default')) {
        defaults[key] = prop.default;
      } else {
        defaults[key] = '';
      }
    });
    setConfig(defaults);
    setErrors({});
  };

  const renderConfigField = (key: string, value: any) => {
    const prop = schemaProps[key] || {};
    const isRequired = schemaRequired.includes(key);
    const hasError = errors[key];
    // Determine field type based on schema type first
    let fieldType = 'text';
    const schemaType = prop?.type;
    if (schemaType === 'boolean') fieldType = 'boolean';
    else if (schemaType === 'number' || schemaType === 'integer') fieldType = 'number';
    else if (schemaType === 'array') fieldType = 'array';
    else if (schemaType === 'string') fieldType = prop?.format === 'password' ? 'password' : 'text';

    const baseInputClasses = `w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent transition-colors dark:bg-gray-700 dark:text-gray-100 ${
      hasError ? 'border-red-300 bg-red-50 dark:border-red-600 dark:bg-red-950' : 'border-gray-300 dark:border-gray-600'
    }`;

    return (
      <div key={key} className="space-y-2">
        <label className="block text-sm font-medium text-gray-700 dark:text-gray-200">
          {key}
          {isRequired && <span className="text-red-500 dark:text-red-400 ml-1">*</span>}
        </label>
        {prop?.description && (
          <p className="text-xs text-gray-500 dark:text-gray-400">{prop.description}</p>
        )}
        
        {fieldType === 'boolean' ? (
          <div className="flex items-center space-x-3">
            <button
              type="button"
              onClick={() => handleConfigChange(key, true)}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                value === true
                  ? 'bg-blue-500 text-white dark:bg-blue-600'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-200 dark:hover:bg-gray-600'
              }`}
            >
              True
            </button>
            <button
              type="button"
              onClick={() => handleConfigChange(key, false)}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                value === false
                  ? 'bg-blue-500 text-white dark:bg-blue-600'
                  : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-700 dark:text-gray-200 dark:hover:bg-gray-600'
              }`}
            >
              False
            </button>
          </div>
        ) : fieldType === 'array' ? (
          <textarea
            value={Array.isArray(value) ? value.join(', ') : (value ?? '')}
            onChange={(e) => {
              const arrayValue = e.target.value.split(',').map(item => item.trim()).filter(Boolean);
              handleConfigChange(key, arrayValue);
            }}
            className={baseInputClasses}
            rows={3}
            placeholder={t('tools.config.placeholders.array', '用逗号分隔多个值')}
          />
        ) : (
          <input
            type={fieldType}
            value={value ?? (prop?.default ?? '')}
            onChange={(e) => {
              const newValue = fieldType === 'number' ? Number(e.target.value) : e.target.value;
              handleConfigChange(key, newValue);
            }}
            className={baseInputClasses}
            placeholder={isRequired ? t('tools.config.placeholders.required', '必填') : t('tools.config.placeholders.optional', '可选')}
          />
        )}
        
        {hasError && (
          <p className="text-sm text-red-600 dark:text-red-400 flex items-center">
            <Info className="w-4 h-4 mr-1" />
            {hasError}
          </p>
        )}
      </div>
    );
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200 dark:border-gray-700 bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-950 dark:to-indigo-950">
          <div className="flex items-center space-x-3">
            <div className="p-2 bg-blue-100 dark:bg-blue-900/30 rounded-lg">
              <Settings className="w-5 h-5 text-blue-600 dark:text-blue-400" />
            </div>
            <div>
              <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100">{t('tools.config.title', '工具配置')}</h2>
              <p className="text-sm text-gray-600 dark:text-gray-300">{tool.name}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
          >
            <X className="w-5 h-5 text-gray-500 dark:text-gray-400" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 p-6 overflow-y-auto dark:bg-gray-900">
          <div className="mb-6">
            <h3 className="text-lg font-medium text-gray-900 dark:text-gray-100 mb-2">{t('tools.config.info', '工具信息')}</h3>
            <p className="text-gray-600 dark:text-gray-300 text-sm">{tool.description}</p>
          </div>

          <div className="space-y-4">
            <h3 className="text-lg font-medium text-gray-900 dark:text-gray-100">{t('tools.config.parameters', '配置参数')}</h3>
            {Object.keys(schemaProps).length > 0 ? (
              <div className="space-y-4">
                {Object.keys(schemaProps).map((key) => renderConfigField(key, config[key]))}
              </div>
            ) : (
              <div className="text-center py-8 text-gray-500 dark:text-gray-400">
                <Settings className="w-12 h-12 mx-auto mb-3 text-gray-300 dark:text-gray-600" />
                <p>{t('tools.config.noParams', '此工具暂无可配置参数')}</p>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between p-6 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
          <button
            onClick={handleReset}
            className="flex items-center space-x-2 px-4 py-2 text-gray-600 dark:text-gray-300 hover:text-gray-800 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
          >
            <RotateCcw className="w-4 h-4" />
            <span>{t('common.reset', '重置')}</span>
          </button>
          
          <div className="flex items-center space-x-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-gray-600 dark:text-gray-300 hover:text-gray-800 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors"
            >
              {t('common.cancel', '取消')}
            </button>
            <button
              onClick={handleSave}
              className="flex items-center space-x-2 px-4 py-2 bg-blue-500 dark:bg-blue-600 text-white rounded-lg hover:bg-blue-600 dark:hover:bg-blue-700 transition-colors"
            >
              <Save className="w-4 h-4" />
              <span>{t('tools.config.save', '保存配置')}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ToolConfigModal;
