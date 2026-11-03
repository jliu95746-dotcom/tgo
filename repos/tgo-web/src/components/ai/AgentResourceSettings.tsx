import { useState, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { Layout, ChevronRight, ChevronUp, ChevronDown, Settings, XCircle } from 'lucide-react';
import type { CreateAgentFormData, FormValidationErrors } from '@/types';
import { SYSTEM_DEFAULT_MODEL } from '@/services/aiAgentsApi';
import Toggle from '@/components/ui/Toggle';
import AgentHumanizationField from './AgentHumanizationField';

interface Props {
  form: CreateAgentFormData;
  errors: FormValidationErrors;
  disabled: boolean;
  onChange: (value: Partial<CreateAgentFormData>) => void;
  llmOptions: Array<{ value: string; label: string }>;
  llmLoading: boolean;
  llmError: string | null;
  llmSelectRef: RefObject<HTMLSelectElement | null>;
}
export default function AgentResourceSettings({ form: createAgentFormData, errors: createAgentErrors, disabled: isCreatingAgent, onChange, llmOptions, llmLoading, llmError, llmSelectRef }: Props) {
  const { t } = useTranslation();
  const [isAdvancedOpen, setIsAdvancedOpen] = useState(false);
  const handleInputChange = <K extends keyof CreateAgentFormData>(key: K, value: CreateAgentFormData[K]) => onChange({ [key]: value });
  return <>
              {/* 模型配置 Section */}
              <div className="space-y-4">
                  <div className="flex items-center gap-2 px-1">
                    <Layout className="w-5 h-5 text-purple-600" />
                    <h3 className="font-bold text-gray-900 dark:text-gray-100 uppercase tracking-tight text-sm">
                      {t('agents.create.sections.modelConfig', '模型配置')}
                    </h3>
                  </div>
                  <div className="bg-white dark:bg-gray-900 p-6 rounded-3xl border border-gray-100 dark:border-gray-800 shadow-sm space-y-4">
                    <div className="space-y-2">
                      <label className="text-xs font-bold text-gray-500 dark:text-gray-400 ml-1 uppercase">
                        {t('agents.form.llmModel', 'LLM模型')} <span className="text-red-500">*</span>
                      </label>
                      <div className="relative">
                        <select
                          ref={llmSelectRef}
                          value={createAgentFormData.llmModel}
                          onChange={(e) => handleInputChange('llmModel', e.target.value)}
                          className={`w-full appearance-none px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border rounded-2xl focus:ring-4 focus:ring-purple-500/10 transition-all outline-none ${
                            createAgentErrors.llmModel ? 'border-red-500 focus:border-red-500' : 'border-gray-100 dark:border-gray-700 focus:border-purple-500'
                          }`}
                          disabled={isCreatingAgent || llmLoading}
                        >
                          <option value={SYSTEM_DEFAULT_MODEL}>{t('modelSetup.agentFollow')}</option>
                          {llmLoading ? (
                            <option value="">{t('agents.create.models.loading', '正在加载模型...')}</option>
                          ) : llmError ? (
                            <option value="">{t('agents.create.models.error', '加载模型失败')}</option>
                          ) : llmOptions.length === 0 ? (
                            <option value="">{t('agents.create.models.empty', '暂无可用模型')}</option>
                          ) : (
                            <>
                              {!createAgentFormData.llmModel && (
                                <option value="">{t('agents.create.models.selectPlaceholder', '请选择模型')}</option>
                              )}
                              {llmOptions.map(option => (
                                <option key={option.value} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </>
                          )}
                        </select>
                        <div className="absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none text-gray-400">
                          <ChevronRight className="w-4 h-4 rotate-90" />
                        </div>
                      </div>
                      <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">{t('modelSetup.agentFollowHint')}</p>
                      {createAgentErrors.llmModel && (
                        <p className="text-[11px] text-red-500 flex items-center gap-1 mt-1 ml-1">
                          <XCircle className="w-3 h-3" /> {createAgentErrors.llmModel}
                        </p>
                      )}
                      {llmError && (
                        <p className="text-[11px] text-red-500 flex items-center gap-1 mt-1 ml-1">
                          <XCircle className="w-3 h-3" /> {t('agents.create.models.loadFailedInline', '模型加载失败: {{error}}', { error: llmError })}
                        </p>
                      )}
                    </div>
                  </div>
                </div>

              <AgentHumanizationField
                disabled={isCreatingAgent}
                name={createAgentFormData.humanization_skill_name}
                enabled={createAgentFormData.humanization_skill_enabled}
                onChange={value => {
                  handleInputChange('humanization_skill_name', value.humanization_skill_name);
                  handleInputChange('humanization_skill_enabled', value.humanization_skill_enabled);
                }}
              />

              {/* Advanced Configuration Section */}
              <div className="space-y-4">
                  <button
                    type="button"
                    onClick={() => setIsAdvancedOpen(!isAdvancedOpen)}
                    className="flex items-center justify-between w-full px-6 py-4 bg-white dark:bg-gray-900 rounded-3xl border border-gray-100 dark:border-gray-800 hover:border-blue-500/50 transition-all group shadow-sm"
                  >
                    <div className="flex items-center gap-3">
                      <Settings className={`w-5 h-5 ${isAdvancedOpen ? 'text-blue-600' : 'text-gray-400'} transition-colors`} />
                      <h3 className="font-bold text-gray-900 dark:text-gray-100 uppercase tracking-tight text-sm">
                        {t('agents.config.advanced', '高级配置')}
                      </h3>
                    </div>
                    {isAdvancedOpen ? (
                      <ChevronUp className="w-5 h-5 text-gray-400" />
                    ) : (
                      <ChevronDown className="w-5 h-5 text-gray-400" />
                    )}
                  </button>

                  {isAdvancedOpen && (
                    <div className="p-6 bg-white dark:bg-gray-900 rounded-3xl border border-gray-100 dark:border-gray-800 shadow-sm space-y-6 animate-in slide-in-from-top-2 duration-200">
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        {/* Markdown */}
                        <div className="flex items-center justify-between p-4 bg-gray-50 dark:bg-gray-800/50 rounded-2xl border border-gray-50 dark:border-gray-700">
                          <div>
                            <p className="text-sm font-bold text-gray-700 dark:text-gray-200">{t('config.markdown', 'Markdown 格式')}</p>
                            <p className="text-xs text-gray-500">{t('config.markdownDesc', '使用 Markdown 格式化输出内容')}</p>
                          </div>
                          <Toggle
                            checked={!!createAgentFormData.markdown}
                            onChange={(checked) => handleInputChange('markdown', checked)}
                          />
                        </div>

                        {/* Add Datetime */}
                        <div className="flex items-center justify-between p-4 bg-gray-50 dark:bg-gray-800/50 rounded-2xl border border-gray-50 dark:border-gray-700">
                          <div>
                            <p className="text-sm font-bold text-gray-700 dark:text-gray-200">{t('config.addDatetime', '添加日期时间')}</p>
                            <p className="text-xs text-gray-500">{t('config.addDatetimeDesc', '在上下文中包含当前日期时间')}</p>
                          </div>
                          <Toggle
                            checked={!!createAgentFormData.add_datetime_to_context}
                            onChange={(checked) => handleInputChange('add_datetime_to_context', checked)}
                          />
                        </div>

                        {/* Skills Enabled */}
                        <div className="flex items-center justify-between p-4 bg-gray-50 dark:bg-gray-800/50 rounded-2xl border border-gray-50 dark:border-gray-700">
                          <div>
                            <p className="text-sm font-bold text-gray-700 dark:text-gray-200">{t('config.skillsEnabled', '启用技能')}</p>
                            <p className="text-xs text-gray-500">{t('config.skillsEnabledDesc', '允许 AI 员工自动发现并使用项目中的专业技能')}</p>
                          </div>
                          <Toggle
                            checked={!!createAgentFormData.skills_enabled}
                            onChange={(checked) => handleInputChange('skills_enabled', checked)}
                          />
                        </div>

                        {/* History Runs */}
                        <div className="space-y-2">
                          <label className="text-xs font-bold text-gray-500 dark:text-gray-400 ml-1 uppercase">
                            {t('config.numHistoryRuns', '历史会话轮数')}
                          </label>
                          <input
                            type="number"
                            min={0}
                            max={20}
                            value={createAgentFormData.num_history_runs}
                            onChange={(e) => handleInputChange('num_history_runs', parseInt(e.target.value) || 0)}
                            className="w-full px-4 py-3 rounded-2xl border border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all outline-none"
                          />
                        </div>

                        {/* Tool Call Limit */}
                        <div className="space-y-2">
                          <label className="text-xs font-bold text-gray-500 dark:text-gray-400 ml-1 uppercase">
                            {t('config.toolCallLimit', '工具调用限制')}
                          </label>
                          <input
                            type="number"
                            min={1}
                            max={50}
                            value={createAgentFormData.tool_call_limit}
                            onChange={(e) => handleInputChange('tool_call_limit', parseInt(e.target.value) || 0)}
                            className="w-full px-4 py-3 rounded-2xl border border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-all outline-none"
                          />
                        </div>
                      </div>
                    </div>
                  )}
                </div>


  </>;
}
