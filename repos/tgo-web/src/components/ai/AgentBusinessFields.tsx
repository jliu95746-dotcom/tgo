import { useTranslation } from 'react-i18next';
import { User, Sparkles, XCircle } from 'lucide-react';
import type { RefObject } from 'react';
import type { CreateAgentFormData, FormValidationErrors } from '@/types';

interface Props {
  form: CreateAgentFormData;
  errors: FormValidationErrors;
  disabled: boolean;
  onChange: (value: Partial<CreateAgentFormData>) => void;
  nameInputRef: RefObject<HTMLInputElement | null>;
  professionInputRef: RefObject<HTMLInputElement | null>;
  descriptionTextareaRef: RefObject<HTMLTextAreaElement | null>;
}
export default function AgentBusinessFields({ form: createAgentFormData, errors: createAgentErrors, disabled: isCreatingAgent, onChange, nameInputRef, professionInputRef, descriptionTextareaRef }: Props) {
  const { t } = useTranslation();
  const handleInputChange = <K extends keyof CreateAgentFormData>(key: K, value: CreateAgentFormData[K]) => onChange({ [key]: value });
  return <>
    <div className="space-y-2 rounded-3xl border border-gray-100 bg-white p-6 shadow-sm dark:border-gray-800 dark:bg-gray-900">
      <label htmlFor="agent-company-summary" className="text-sm font-bold text-gray-700 dark:text-gray-200">{t('businessScenarios.wizard.company')} <span className="text-red-500">*</span></label>
      <p className="text-xs text-gray-500">{t('businessScenarios.wizard.companyHint')}</p>
      <textarea id="agent-company-summary" maxLength={2000} rows={3} value={createAgentFormData.companySummary ?? ''} onChange={event => onChange({ companySummary: event.target.value })} disabled={isCreatingAgent} placeholder={t('businessScenarios.wizard.companyPlaceholder')} className="w-full rounded-2xl border border-gray-100 bg-gray-50 px-4 py-3 text-gray-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800/50 dark:text-gray-100" />
    </div>
              {/* 基本信息 Section */}
              <div className="space-y-4">
                <div className="flex items-center gap-2 px-1">
                  <User className="w-5 h-5 text-blue-600" />
                  <h3 className="font-bold text-gray-900 dark:text-gray-100 uppercase tracking-tight text-sm">
                    {t('agents.detail.basicInfo', '基本信息')}
                  </h3>
                </div>
                <div className="bg-white dark:bg-gray-900 p-6 rounded-3xl border border-gray-100 dark:border-gray-800 shadow-sm space-y-6">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {/* AI员工名称 */}
                    <div className="space-y-2">
                      <label htmlFor="agent-setup-name" className="text-xs font-bold text-gray-500 dark:text-gray-400 ml-1 uppercase">
                        {t('agents.form.name', 'AI员工名称')} <span className="text-red-500">*</span>
                      </label>
                      <input
                        ref={nameInputRef}
                        id="agent-setup-name"
                        type="text"
                        value={createAgentFormData.name}
                        onChange={(e) => handleInputChange('name', e.target.value)}
                        className={`w-full px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border rounded-2xl focus:ring-4 focus:ring-blue-500/10 transition-all outline-none ${
                          createAgentErrors.name ? 'border-red-500 focus:border-red-500' : 'border-gray-100 dark:border-gray-700 focus:border-blue-500'
                        }`}
                        placeholder={t('agents.create.placeholders.name', '请输入AI员工名称')}
                        disabled={isCreatingAgent}
                      />
                      {createAgentErrors.name && (
                        <p className="text-[11px] text-red-500 flex items-center gap-1 mt-1 ml-1">
                          <XCircle className="w-3 h-3" /> {createAgentErrors.name}
                        </p>
                      )}
                    </div>

                    {/* 职业/角色 */}
                    <div className="space-y-2">
                      <label htmlFor="agent-setup-profession" className="text-xs font-bold text-gray-500 dark:text-gray-400 ml-1 uppercase">
                        {t('agents.form.profession', '职业/角色')} <span className="text-red-500">*</span>
                      </label>
                      <input
                        ref={professionInputRef}
                        id="agent-setup-profession"
                        type="text"
                        value={createAgentFormData.profession}
                        onChange={(e) => handleInputChange('profession', e.target.value)}
                        className={`w-full px-4 py-3 bg-gray-50 dark:bg-gray-800/50 border rounded-2xl focus:ring-4 focus:ring-blue-500/10 transition-all outline-none ${
                          createAgentErrors.profession ? 'border-red-500 focus:border-red-500' : 'border-gray-100 dark:border-gray-700 focus:border-blue-500'
                        }`}
                        placeholder={t('agents.create.placeholders.profession', '例如：客服专员、技术支持')}
                        disabled={isCreatingAgent}
                      />
                      {createAgentErrors.profession && (
                        <p className="text-[11px] text-red-500 flex items-center gap-1 mt-1 ml-1">
                          <XCircle className="w-3 h-3" /> {createAgentErrors.profession}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              {/* 能力描述 Section */}
              <div className="space-y-4">
                  <div className="flex items-center gap-2 px-1">
                    <Sparkles className="w-5 h-5 text-green-600" />
                    <h3 className="font-bold text-gray-900 dark:text-gray-100 uppercase tracking-tight text-sm">
                      {t('agents.create.sections.description', '能力描述')}
                    </h3>
                  </div>
                  <div className="bg-white dark:bg-gray-900 p-6 rounded-3xl border border-gray-100 dark:border-gray-800 shadow-sm">
                    <div className="space-y-2">
                      <label htmlFor="agent-setup-instructions" className="text-xs font-bold text-gray-500 dark:text-gray-400 ml-1 uppercase">
                        {t('businessScenarios.wizard.instructions')} <span className="text-red-500">*</span>
                      </label>
                      <textarea
                        ref={descriptionTextareaRef}
                        id="agent-setup-instructions"
                        value={createAgentFormData.description}
                        onChange={(e) => handleInputChange('description', e.target.value)}
                        rows={6}
                        className={`w-full px-4 py-4 bg-gray-50 dark:bg-gray-800/50 border rounded-2xl focus:ring-4 focus:ring-green-500/10 transition-all outline-none resize-none ${
                          createAgentErrors.description ? 'border-red-500 focus:border-red-500' : 'border-gray-100 dark:border-gray-700 focus:border-green-500'
                        }`}
                        placeholder={t('agents.create.placeholders.description', '请详细描述AI员工的功能、职责和特点...')}
                        disabled={isCreatingAgent}
                      />
                      {createAgentErrors.description && (
                        <p className="text-[11px] text-red-500 flex items-center gap-1 mt-1 ml-1">
                          <XCircle className="w-3 h-3" /> {createAgentErrors.description}
                        </p>
                      )}
                    </div>
                  </div>
                </div>


  </>;
}
