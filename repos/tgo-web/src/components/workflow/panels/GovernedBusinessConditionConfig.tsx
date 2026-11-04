import { useTranslation } from 'react-i18next';
import type { ConditionNodeData } from '@/types/workflow';

const resultFields = [
  { value: 'order.outcome', labelKey: 'orderOutcome', values: ['passed', 'failed'] },
  { value: 'logistics.outcome', labelKey: 'logisticsOutcome', values: ['passed', 'failed'] },
  { value: 'order.error_code', labelKey: 'orderError', values: [
    'denied', 'timeout', 'provider_failed', 'audit_unavailable', 'authority_changed',
    'not_found', 'not_shipped', 'authentication_failed', 'business_failed',
  ] },
  { value: 'logistics.error_code', labelKey: 'logisticsError', values: [
    'denied', 'timeout', 'provider_failed', 'audit_unavailable', 'authority_changed',
    'not_found', 'not_shipped', 'authentication_failed', 'business_failed',
  ] },
] as const;

const selectClass = 'w-full rounded-xl border border-gray-100 bg-gray-50 px-4 py-2.5 text-sm text-gray-900 outline-none focus:border-purple-500 focus:ring-2 focus:ring-purple-500/20 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100';

export default function GovernedBusinessConditionConfig({ data, onUpdate }: {
  data: ConditionNodeData;
  onUpdate: (updates: Partial<ConditionNodeData>) => void;
}) {
  const { t } = useTranslation();
  const selectedField = resultFields.find(field => field.value === data.variable);
  const validCondition = data.condition_type === 'variable' && data.operator === 'equals';
  const selectedValue = validCondition && selectedField?.values.some(value => value === data.compare_value)
    ? data.compare_value : '';

  return <div className="space-y-4">
    <p className="rounded-xl border border-blue-200 bg-blue-50 p-3 text-xs leading-5 text-blue-700 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-300">
      {t('businessScenarios.workflow.governedCondition.hint')}
    </p>
    <label className="block space-y-2 text-xs font-semibold text-gray-700 dark:text-gray-200">
      <span>{t('businessScenarios.workflow.governedCondition.field')}</span>
      <select value={validCondition && selectedField ? selectedField.value : ''} className={selectClass}
        onChange={event => {
          const field = resultFields.find(item => item.value === event.target.value);
          if (!field) return;
          onUpdate({ condition_type: 'variable', variable: field.value, operator: 'equals',
            compare_value: field.values[0], expression: undefined, llm_prompt: undefined,
            provider_id: undefined, model_id: undefined });
        }}>
        <option value="">{t('businessScenarios.workflow.governedCondition.chooseField')}</option>
        {resultFields.map(field => <option key={field.value} value={field.value}>
          {t(`businessScenarios.workflow.governedCondition.fields.${field.labelKey}`)}
        </option>)}
      </select>
    </label>
    {selectedField && validCondition && <label className="block space-y-2 text-xs font-semibold text-gray-700 dark:text-gray-200">
      <span>{t('businessScenarios.workflow.governedCondition.value')}</span>
      <select value={selectedValue} className={selectClass} onChange={event => onUpdate({ compare_value: event.target.value })}>
        <option value="">{t('businessScenarios.workflow.governedCondition.chooseValue')}</option>
        {selectedField.values.map(value => <option key={value} value={value}>
          {t(`businessScenarios.workflow.governedCondition.values.${value}`)}
        </option>)}
      </select>
    </label>}
  </div>;
}
