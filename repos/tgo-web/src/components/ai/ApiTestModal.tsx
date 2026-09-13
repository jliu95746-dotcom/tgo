import React, { useState, useEffect } from 'react';
import { X, Settings, Play } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getMethodColorClass, type ParsedEndpoint } from '@/utils/schemaParser';
import AuthMethodModal, { type AuthConfig } from './AuthMethodModal';

interface ApiTestModalProps {
  isOpen: boolean;
  onClose: () => void;
  endpoint: ParsedEndpoint | null;
  authConfig: AuthConfig;
  onAuthConfigChange?: (config: AuthConfig) => void;
}

interface TestParameter {
  name: string;
  value: string;
  type: 'query' | 'path' | 'header' | 'body';
  required: boolean;
  description?: string;
  schema: {
    type: string;
    format?: string;
    enum?: any[];
    default?: any;
    minimum?: number;
    maximum?: number;
    minLength?: number;
    maxLength?: number;
    pattern?: string;
    example?: any;
    examples?: any[];
    title?: string;
    nullable?: boolean;
  };
  deprecated?: boolean;
  example?: any;
  examples?: Record<string, any>;
}

interface TestResult {
  status?: number;
  statusText?: string;
  headers?: Record<string, string>;
  data?: any;
  error?: string;
  timestamp?: string;
}

/**
 * API端点测试弹窗组件
 */
const ApiTestModal: React.FC<ApiTestModalProps> = ({
  isOpen,
  onClose,
  endpoint,
  authConfig,
  onAuthConfigChange
}) => {
  const { t } = useTranslation();
  const [parameters, setParameters] = useState<TestParameter[]>([]);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [isTesting, setIsTesting] = useState(false);
  const [showAuthModal, setShowAuthModal] = useState(false);

  // Initialize parameters from endpoint schema
  useEffect(() => {
    if (endpoint && endpoint.parameters) {
      const initialParams: TestParameter[] = endpoint.parameters.map(param => ({
        name: param.name,
        value: param.schema.default?.toString() || param.example?.toString() || '',
        type: param.type,
        required: param.required,
        description: param.description,
        schema: param.schema,
        deprecated: param.deprecated,
        example: param.example,
        examples: param.examples
      }));
      setParameters(initialParams);
    } else {
      setParameters([]);
    }
    // Reset test result when endpoint changes
    setTestResult(null);
  }, [endpoint]);

  // 处理参数值变化
  const handleParameterChange = (index: number, value: string) => {
    const newParameters = [...parameters];
    newParameters[index].value = value;
    setParameters(newParameters);
  };

  // 验证参数
  const validateParameters = (): { isValid: boolean; errors: string[] } => {
    const errors: string[] = [];

    parameters.forEach(param => {
      // 检查必填参数
      if (param.required && !param.value.trim()) {
        errors.push(t('agents.modal.apiTest.errors.required', { name: param.name }));
        return;
      }

      if (!param.value.trim()) return; // 跳过空的非必填参数

      const value = param.value.trim();
      const schema = param.schema;

      // 类型验证
      if (schema.type === 'number' || schema.type === 'integer') {
        const numValue = Number(value);
        if (isNaN(numValue)) {
          errors.push(t('agents.modal.apiTest.errors.mustBeNumber', { name: param.name }));
          return;
        }

        if (schema.minimum !== undefined && numValue < schema.minimum) {
          errors.push(t('agents.modal.apiTest.errors.minValue', { name: param.name, min: schema.minimum }));
        }
        if (schema.maximum !== undefined && numValue > schema.maximum) {
          errors.push(t('agents.modal.apiTest.errors.maxValue', { name: param.name, max: schema.maximum }));
        }
      }

      // 字符串长度验证
      if (schema.type === 'string') {
        if (schema.minLength !== undefined && value.length < schema.minLength) {
          errors.push(t('agents.modal.apiTest.errors.minLength', { name: param.name, min: schema.minLength }));
        }
        if (schema.maxLength !== undefined && value.length > schema.maxLength) {
          errors.push(t('agents.modal.apiTest.errors.maxLength', { name: param.name, max: schema.maxLength }));
        }

        // 正则模式验证
        if (schema.pattern) {
          try {
            const regex = new RegExp(schema.pattern);
            if (!regex.test(value)) {
              errors.push(t('agents.modal.apiTest.errors.formatInvalid', { name: param.name }));
            }
          } catch {
            console.warn(`Invalid regex pattern for parameter ${param.name}:`, schema.pattern);
          }
        }
      }

      // 枚举值验证
      if (schema.enum && schema.enum.length > 0) {
        if (!schema.enum.includes(value)) {
          errors.push(t('agents.modal.apiTest.errors.enumInvalid', { name: param.name, values: schema.enum.join(', ') }));
        }
      }
    });

    return { isValid: errors.length === 0, errors };
  };

  // 构建请求URL
  const buildRequestUrl = (baseUrl: string): string => {
    let url = baseUrl;
    const queryParams: string[] = [];

    // 替换路径参数
    parameters.forEach(param => {
      if (param.type === 'path' && param.value.trim()) {
        url = url.replace(`{${param.name}}`, encodeURIComponent(param.value.trim()));
      }
    });

    // 添加查询参数
    parameters.forEach(param => {
      if (param.type === 'query' && param.value.trim()) {
        queryParams.push(`${encodeURIComponent(param.name)}=${encodeURIComponent(param.value.trim())}`);
      }
    });

    // 添加鉴权查询参数
    const effectiveAuthConfig = getEffectiveAuthConfig();
    if (effectiveAuthConfig.type === 'query' && effectiveAuthConfig.key && effectiveAuthConfig.value) {
      queryParams.push(`${encodeURIComponent(effectiveAuthConfig.key)}=${encodeURIComponent(effectiveAuthConfig.value)}`);
    }

    if (queryParams.length > 0) {
      url += (url.includes('?') ? '&' : '?') + queryParams.join('&');
    }

    return url;
  };

  // 构建请求头
  const buildRequestHeaders = (): Record<string, string> => {
    const headers: Record<string, string> = {};

    // 添加参数中的头部
    parameters.forEach(param => {
      if (param.type === 'header' && param.value.trim()) {
        headers[param.name] = param.value.trim();
      }
    });

    // 添加鉴权头部
    const effectiveAuthConfig = getEffectiveAuthConfig();
    if (effectiveAuthConfig.type === 'header' && effectiveAuthConfig.key && effectiveAuthConfig.value) {
      if (effectiveAuthConfig.headerPrefix === 'basic') {
        headers[effectiveAuthConfig.key] = `Basic ${effectiveAuthConfig.value}`;
      } else if (effectiveAuthConfig.headerPrefix === 'bearer') {
        headers[effectiveAuthConfig.key] = `Bearer ${effectiveAuthConfig.value}`;
      } else {
        headers[effectiveAuthConfig.key] = effectiveAuthConfig.value;
      }
    }

    return headers;
  };

  // 构建请求体
  const buildRequestBody = (): any => {
    const bodyParams = parameters.filter(param => param.type === 'body' && param.value.trim());

    if (bodyParams.length === 0) return null;

    // 如果只有一个body参数且名称为'body'，直接使用其值
    if (bodyParams.length === 1 && bodyParams[0].name === 'body') {
      try {
        return JSON.parse(bodyParams[0].value);
      } catch {
        return bodyParams[0].value;
      }
    }

    // 否则构建JSON对象
    const body: any = {};
    bodyParams.forEach(param => {
      let value: any = param.value.trim();

      // 尝试根据schema类型转换值
      if (param.schema.type === 'number' || param.schema.type === 'integer') {
        const numValue = Number(value);
        if (!isNaN(numValue)) {
          value = numValue;
        }
      } else if (param.schema.type === 'boolean') {
        value = value.toLowerCase() === 'true';
      } else if (param.schema.type === 'array') {
        try {
          value = JSON.parse(value);
        } catch {
          value = value.split(',').map((v: string) => v.trim());
        }
      } else if (param.schema.type === 'object') {
        try {
          value = JSON.parse(value);
        } catch {
          // 保持字符串值
        }
      }

      body[param.name] = value;
    });

    return body;
  };

  // 执行API测试
  const handleTest = async (e?: React.MouseEvent) => {
    // Prevent form submission and event bubbling
    if (e) {
      e.preventDefault();
      e.stopPropagation();
    }

    if (!endpoint) return;

    setIsTesting(true);
    setTestResult(null);

    try {
      // 验证参数
      const validation = validateParameters();
      if (!validation.isValid) {
        setTestResult({
          error: `${t('agents.modal.apiTest.errors.validationFailedPrefix')  }\n${  validation.errors.join('\n')}`,
          timestamp: new Date().toISOString()
        });
        return;
      }

      // 构建请求URL (使用示例基础URL，实际应用中应该从endpoint或配置中获取)
      const baseUrl = `https://api.example.com${  endpoint.path}`;
      const requestUrl = buildRequestUrl(baseUrl);

      // 构建请求头
      const headers = buildRequestHeaders();

      // 设置Content-Type
      const hasBodyData = ['POST', 'PUT', 'PATCH'].includes(endpoint.method.toUpperCase());
      if (hasBodyData) {
        headers['Content-Type'] = headers['Content-Type'] || 'application/json';
      }

      // 构建请求体
      const requestBody = buildRequestBody();

      // 构建fetch选项
      const fetchOptions: RequestInit = {
        method: endpoint.method.toUpperCase(),
        headers,
        mode: 'cors', // 处理CORS
        credentials: 'omit', // 不发送凭据以避免CORS问题
      };

      // 添加请求体（仅对支持body的方法）
      if (hasBodyData && requestBody !== null) {
        if (headers['Content-Type']?.includes('application/json')) {
          fetchOptions.body = JSON.stringify(requestBody);
        } else {
          fetchOptions.body = requestBody;
        }
      }

      console.log('发送API请求:', {
        url: requestUrl,
        method: endpoint.method.toUpperCase(),
        headers,
        body: requestBody
      });

      // 发送请求
      const response = await fetch(requestUrl, fetchOptions);

      // 解析响应头
      const responseHeaders: Record<string, string> = {};
      response.headers.forEach((value, key) => {
        responseHeaders[key] = value;
      });

      // 解析响应体
      let responseData: any;
      const contentType = response.headers.get('content-type');

      try {
        if (contentType?.includes('application/json')) {
          responseData = await response.json();
        } else if (contentType?.includes('text/')) {
          responseData = await response.text();
        } else {
          // 尝试解析为文本
          responseData = await response.text();
          // 如果是空响应，设置为null
          if (!responseData) {
            responseData = null;
          }
        }
      } catch (parseError) {
        console.warn('响应解析失败:', parseError);
        responseData = t('agents.modal.apiTest.errors.parseResponseFailed', '响应解析失败');
      }

      // 构建测试结果
      const testResult: TestResult = {
        status: response.status,
        statusText: response.statusText,
        headers: responseHeaders,
        data: responseData,
        timestamp: new Date().toISOString()
      };

      // 如果响应不成功，添加错误信息
      if (!response.ok) {
        testResult.error = `HTTP ${response.status}: ${response.statusText}`;
      }

      setTestResult(testResult);

    } catch (error) {
      console.error('API测试失败:', error);

      let errorMessage = t('agents.modal.apiTest.errors.requestFailed', '请求失败');

      if (error instanceof TypeError && error.message.includes('fetch')) {
        errorMessage = t('agents.modal.apiTest.errors.networkFailed', '网络连接失败，请检查URL是否正确或是否存在CORS问题');
      } else if (error instanceof Error) {
        errorMessage = error.message;
      }

      setTestResult({
        error: errorMessage,
        timestamp: new Date().toISOString()
      });
    } finally {
      setIsTesting(false);
    }
  };

  // 处理鉴权配置保存
  const handleAuthSave = (config: AuthConfig) => {
    if (onAuthConfigChange) {
      onAuthConfigChange(config);
    }
  };

  // 获取鉴权显示文本
  const getAuthDisplayText = () => {
    const autoAuth = endpoint?.autoDetectedAuth;

    let baseText = '';
    if (authConfig.type === 'none') {
      if (autoAuth && autoAuth.type !== 'none') {
        baseText = `自动检测: ${autoAuth.type === 'header' ? '请求头' : '查询参数'}`;
      } else {
        baseText = '无';
      }
    } else if (authConfig.type === 'header') {
      baseText = `请求头 (${authConfig.headerPrefix?.toUpperCase()})`;
    } else if (authConfig.type === 'query') {
      baseText = '查询参数';
    } else {
      baseText = '无';
    }

    return baseText;
  };

  // 检查是否为自动检测的鉴权
  const isAutoDetectedAuth = () => {
    const autoAuth = endpoint?.autoDetectedAuth;
    return autoAuth && (
      (authConfig.type === autoAuth.type && authConfig.key === autoAuth.key) ||
      (authConfig.type === 'none' && autoAuth.type !== 'none')
    );
  };

  // 获取有效的鉴权配置（优先使用手动配置，否则使用自动检测）
  const getEffectiveAuthConfig = (): AuthConfig => {
    if (authConfig.type !== 'none') {
      return authConfig;
    }

    // 如果没有手动配置，尝试使用自动检测的配置
    const autoAuth = endpoint?.autoDetectedAuth;
    if (autoAuth && autoAuth.type !== 'none') {
      return {
        type: autoAuth.type,
        headerPrefix: autoAuth.headerPrefix,
        key: autoAuth.key,
        value: '', // 需要用户提供实际的API密钥
      };
    }

    return authConfig;
  };

  if (!isOpen || !endpoint) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] overflow-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <div className="flex items-center space-x-3 min-w-0 flex-1">
            <h2
              className="text-xl font-semibold text-gray-900 truncate max-w-xs sm:max-w-sm md:max-w-md lg:max-w-lg"
              title={t('agents.modal.apiTest.title', { name: endpoint.name })}
            >
              {t('agents.modal.apiTest.title', { name: endpoint.name })}
            </h2>
            <span className={`px-2 py-1 text-xs font-medium rounded whitespace-nowrap ${getMethodColorClass(endpoint.method)} bg-opacity-10`}>
              {endpoint.method}
            </span>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors flex-shrink-0 ml-4"
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        {/* Content */}
        <div className="overflow-y-auto max-h-[calc(90vh-140px)]">
          <div className="p-6 space-y-6">
            {/* 端点信息 */}
            <div className="bg-gray-50 rounded-lg p-4">
              <div className="flex items-center space-x-2 text-sm text-gray-600">
                <span className={`font-medium ${getMethodColorClass(endpoint.method)}`}>
                  {endpoint.method}
                </span>
                <span className="font-mono">{endpoint.path}</span>
              </div>
              <p className="text-sm text-gray-700 mt-1">{endpoint.description}</p>
            </div>

            {/* 鉴权方法 */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center space-x-2">
                  <h3 className="text-sm font-medium text-gray-700">{t('agents.modal.apiTest.sections.authMethod', '鉴权方法')}</h3>
                  {isAutoDetectedAuth() && (
                    <span
                      className="text-xs px-2 py-1 bg-blue-100 text-blue-700 rounded-full"
                      title={t('agents.modal.apiTest.auth.autoDetectedTitle', { level: t(`agents.modal.apiTest.auth.level.${endpoint?.autoDetectedAuth?.source === 'operation' ? 'operation' : 'global'}`) })}
                    >
                      {t('agents.modal.apiTest.auth.autoDetected', '自动检测')}
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => setShowAuthModal(true)}
                  className="text-gray-400 hover:text-gray-600 transition-colors"
                >
                  <Settings className="w-4 h-4" />
                </button>
              </div>
              <div className="p-3 bg-gray-50 rounded-lg">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-gray-600">{getAuthDisplayText()}</span>
                  {endpoint?.autoDetectedAuth && (
                    <span className="text-xs text-gray-400">
                      {endpoint.autoDetectedAuth.description && (
                        <span title={endpoint.autoDetectedAuth.description}>
                          {endpoint.autoDetectedAuth.schemeName}
                        </span>
                      )}
                    </span>
                  )}
                </div>
              </div>
            </div>

            {/* 参数和值 */}
            <div>
              <h3 className="text-sm font-medium text-gray-700 mb-3">参数和值</h3>
              {parameters.length > 0 ? (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-4 text-sm font-medium text-gray-500 px-3">
                    <span>{t('agents.modal.apiTest.labels.param', '参数')}</span>
                    <span>{t('agents.modal.apiTest.labels.value', '值')}</span>
                  </div>
                  {parameters.map((param, index) => (
                    <div key={index} className="grid grid-cols-2 gap-4">
                      <div className="px-3 py-2 bg-gray-50 rounded border">
                        <div className="flex items-center space-x-2">
                          <span className={`text-sm font-medium ${param.deprecated ? 'line-through text-gray-400' : 'text-gray-700'}`}>
                            {param.name}
                          </span>
                          {param.required && (
                            <span className="text-xs text-red-500 font-medium">*</span>
                          )}
                          {param.deprecated && (
                            <span className="text-xs px-1.5 py-0.5 rounded font-medium bg-yellow-100 text-yellow-700">
                              deprecated
                            </span>
                          )}
                          <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${
                            param.type === 'query' ? 'bg-blue-100 text-blue-700' :
                            param.type === 'path' ? 'bg-green-100 text-green-700' :
                            param.type === 'header' ? 'bg-purple-100 text-purple-700' :
                            'bg-orange-100 text-orange-700'
                          }`}>
                            {param.type}
                          </span>
                        </div>
                        {param.description && (
                          <p className="text-xs text-gray-500 mt-1">{param.description}</p>
                        )}
                        <div className="flex items-center space-x-4 mt-1 text-xs text-gray-400">
                          {param.schema.type && (
                            <span>{t('agents.modal.apiTest.labels.type', '类型')}: {param.schema.type}</span>
                          )}
                          {param.schema.format && (
                            <span>{t('agents.modal.apiTest.labels.format', '格式')}: {param.schema.format}</span>
                          )}
                          {param.schema.minimum !== undefined && (
                            <span>{t('agents.modal.apiTest.labels.min', '最小值')}: {param.schema.minimum}</span>
                          )}
                          {param.schema.maximum !== undefined && (
                            <span>{t('agents.modal.apiTest.labels.max', '最大值')}: {param.schema.maximum}</span>
                          )}
                          {param.schema.minLength !== undefined && (
                            <span>{t('agents.modal.apiTest.labels.minLength', '最小长度')}: {param.schema.minLength}</span>
                          )}
                          {param.schema.maxLength !== undefined && (
                            <span>{t('agents.modal.apiTest.labels.maxLength', '最大长度')}: {param.schema.maxLength}</span>
                          )}
                        </div>
                        {param.schema.pattern && (
                          <p className="text-xs text-gray-400 mt-1">{t('agents.modal.apiTest.labels.pattern', '模式')}: {param.schema.pattern}</p>
                        )}
                      </div>
                      <div className="space-y-1">
                        {param.schema?.enum ? (
                          <select
                            value={param.value}
                            onChange={(e) => handleParameterChange(index, e.target.value)}
                            className="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                          >
                            <option value="">{t('agents.modal.apiTest.placeholders.select', '请选择')}</option>
                            {param.schema.enum.map((option, optIndex) => (
                              <option key={optIndex} value={option}>{option}</option>
                            ))}
                          </select>
                        ) : (
                          <input
                            type={param.schema.type === 'number' || param.schema.type === 'integer' ? 'number' : 'text'}
                            value={param.value}
                            onChange={(e) => handleParameterChange(index, e.target.value)}
                            className={`w-full px-3 py-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm ${
                              param.deprecated ? 'bg-gray-100 text-gray-500' : 'border-gray-300'
                            }`}
                            placeholder={
                              param.schema.example ? t('agents.modal.apiTest.placeholders.example', { value: String(param.schema.example) }) :
                              param.schema.default ? t('agents.modal.apiTest.placeholders.default', { value: String(param.schema.default) }) :
                              param.example ? t('agents.modal.apiTest.placeholders.example', { value: String(param.example) }) :
                              t('agents.modal.apiTest.placeholders.enter', '请输入')
                            }
                            required={param.required}
                            min={param.schema.minimum}
                            max={param.schema.maximum}
                            minLength={param.schema.minLength}
                            maxLength={param.schema.maxLength}
                            pattern={param.schema.pattern}
                            disabled={param.deprecated}
                            title={param.description}
                          />
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-8 text-gray-500 text-sm">
                  {t('agents.modal.apiTest.noParams', '此端点没有定义参数')}
                </div>
              )}
            </div>

            {/* 测试按钮 */}
            <button
              type="button"
              onClick={handleTest}
              disabled={isTesting}
              className="w-full flex items-center justify-center space-x-2 px-4 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Play className="w-4 h-4" />
              <span>{isTesting ? t('agents.modal.apiTest.buttons.testing', '测试中...') : t('agents.modal.apiTest.buttons.test', '测试')}</span>
            </button>

            {/* 测试结果 */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-medium text-gray-700">{t('agents.modal.apiTest.sections.testResult', '测试结果')}</h3>
                <div className="text-xs text-gray-500">
                  <span title={t('agents.modal.apiTest.result.corsHintTitle', '注意：由于浏览器CORS限制，某些API可能无法直接测试')}>ⓘ {t('agents.modal.apiTest.result.corsHint', 'CORS提示')}</span>
                </div>
              </div>
              <div className="min-h-[200px] p-4 bg-gray-50 rounded-lg border">
                {testResult ? (
                  <div className="space-y-3">
                    {testResult.error ? (
                      <div className="text-red-600 text-sm">
                        <strong>{t('agents.modal.apiTest.result.error', '错误:')}</strong>
                        <pre className="mt-2 whitespace-pre-wrap font-mono text-xs bg-red-50 p-2 rounded border">
                          {testResult.error}
                        </pre>
                      </div>
                    ) : (
                      <>
                        <div className="flex items-center space-x-4 text-sm">
                          <span className="text-green-600 font-medium">
                            {testResult.status} {testResult.statusText}
                          </span>
                          <span className="text-gray-500">
                            {testResult.timestamp && new Date(testResult.timestamp).toLocaleTimeString()}
                          </span>
                        </div>
                        
                        {testResult.headers && (
                          <div>
                            <h4 className="text-xs font-medium text-gray-600 mb-2">{t('agents.modal.apiTest.result.responseHeaders', '响应头:')}</h4>
                            <pre className="text-xs text-gray-700 bg-white p-2 rounded border overflow-x-auto">
                              {JSON.stringify(testResult.headers, null, 2)}
                            </pre>
                          </div>
                        )}
                        
                        {testResult.data && (
                          <div>
                            <h4 className="text-xs font-medium text-gray-600 mb-2">{t('agents.modal.apiTest.result.responseData', '响应数据:')}</h4>
                            <pre className="text-xs text-gray-700 bg-white p-2 rounded border overflow-x-auto">
                              {JSON.stringify(testResult.data, null, 2)}
                            </pre>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                ) : (
                  <div className="flex items-center justify-center h-full text-sm text-gray-500">
                    {t('agents.modal.apiTest.result.empty', '测试结果将显示在这里')}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* 鉴权方法设置弹窗 */}
        <AuthMethodModal
          isOpen={showAuthModal}
          onClose={() => setShowAuthModal(false)}
          onSave={handleAuthSave}
          initialConfig={getEffectiveAuthConfig()} // 使用有效配置，包括自动检测的设置
        />
      </div>
    </div>
  );
};

export default ApiTestModal;
