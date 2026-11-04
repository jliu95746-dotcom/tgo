import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { PlusCircle, Trash2 } from 'lucide-react';
import type { InputNodeData, WorkflowInputVariable } from '@/types/workflow';

interface Props {
  data: InputNodeData;
  onUpdate: (updates: Partial<InputNodeData>) => void;
}

export default function InputNodeConfig({ data, onUpdate }: Props) {
  const { t } = useTranslation();
  const id = useId();
  const variables = data.input_variables ?? [];
  const update = (index: number, changes: Partial<WorkflowInputVariable>) => {
    onUpdate({ input_variables: variables.map((variable, position) => position === index ? { ...variable, ...changes } : variable) });
  };
  const add = () => {
    let number = variables.length + 1;
    while (variables.some(variable => variable.name === `input_${number}`)) number++;
    onUpdate({ input_variables: [...variables, { name: `input_${number}`, type: 'string', description: '', required: true }] });
  };
  const inputClass = 'w-full px-2 py-1 bg-white dark:bg-gray-900 border border-gray-100 dark:border-gray-700 rounded-lg text-xs outline-none focus:ring-1 focus:ring-blue-500';
  return (
    <div className="space-y-6">
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <span className="text-[11px] uppercase font-bold text-gray-400 tracking-wider">{t('workflow.fields.input_variables')}</span>
          <button type="button" onClick={add} className="flex items-center gap-1 text-[10px] font-bold text-blue-500 hover:text-blue-600 uppercase tracking-widest transition-colors">
            <PlusCircle className="w-3.5 h-3.5" />{t('common.add')}
          </button>
        </div>
        <div className="space-y-3">
          {variables.map((variable, index) => (
            <div key={index} className="p-4 bg-gray-50 dark:bg-gray-800/50 rounded-2xl border border-gray-100 dark:border-gray-700 space-y-3 relative group">
              <button type="button" aria-label={t('businessScenarios.workflow.inputs.remove', { name: variable.name })} onClick={() => onUpdate({ input_variables: variables.filter((_, position) => position !== index) })} className="absolute right-3 top-3 p-1 text-gray-400 hover:text-red-500 transition-colors opacity-0 group-hover:opacity-100 group-focus-within:opacity-100">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label htmlFor={`${id}-${index}-name`} className="text-[9px] uppercase font-bold text-gray-400">{t('workflow.placeholders.input_name')}</label>
                  <input id={`${id}-${index}-name`} value={variable.name} onChange={event => update(index, { name: event.target.value })} className={`${inputClass} font-mono`} />
                </div>
                <div className="space-y-1">
                  <label htmlFor={`${id}-${index}-type`} className="text-[9px] uppercase font-bold text-gray-400">{t('workflow.placeholders.input_type')}</label>
                  <select id={`${id}-${index}-type`} value={variable.type} onChange={event => {
                    const type = event.target.value;
                    if (type === 'string') update(index, { type, minimum: undefined, maximum: undefined });
                    if (type === 'number') update(index, { type, min_length: undefined, max_length: undefined, allowed_values: undefined });
                    if (type === 'boolean') update(index, {
                      type, min_length: undefined, max_length: undefined,
                      minimum: undefined, maximum: undefined, allowed_values: undefined,
                    });
                  }} className={`${inputClass} appearance-none cursor-pointer`}>
                    <option value="string">{t('businessScenarios.workflow.inputs.types.string')}</option>
                    <option value="number">{t('businessScenarios.workflow.inputs.types.number')}</option>
                    <option value="boolean">{t('businessScenarios.workflow.inputs.types.boolean')}</option>
                  </select>
                </div>
              </div>
              <div className="space-y-1">
                <label htmlFor={`${id}-${index}-description`} className="text-[9px] uppercase font-bold text-gray-400">{t('workflow.placeholders.input_description')}</label>
                <input id={`${id}-${index}-description`} value={variable.description ?? ''} onChange={event => update(index, { description: event.target.value })} placeholder={t('workflow.placeholders.input_placeholder')} className={inputClass} />
              </div>
              <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
                <input type="checkbox" checked={variable.required === true} onChange={event => update(index, { required: event.target.checked })} className="accent-blue-600" />
                {t('businessScenarios.workflow.inputs.requiredSetting')}
              </label>
              {variable.type === 'string' && <div className="grid grid-cols-2 gap-3">
                {(['min_length', 'max_length'] as const).map(field => <div key={field} className="space-y-1">
                  <label htmlFor={`${id}-${index}-${field}`} className="text-[9px] uppercase font-bold text-gray-400">{t(`businessScenarios.workflow.inputs.${field}`)}</label>
                  <input id={`${id}-${index}-${field}`} type="number" min={0} max={65535} step={1}
                    value={variable[field] ?? ''} onChange={event => update(index, { [field]: event.target.value === '' ? undefined : Number(event.target.value) })}
                    className={inputClass} />
                </div>)}
              </div>}
              {variable.type === 'string' && <div className="space-y-2">
                <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
                  <input type="checkbox" checked={variable.allowed_values != null}
                    onChange={event => update(index, { allowed_values: event.target.checked ? [''] : undefined })}
                    className="accent-blue-600" />
                  {t('businessScenarios.workflow.inputs.restrictChoices')}
                </label>
                {variable.allowed_values?.map((choice, choiceIndex) => (
                  <div key={choiceIndex} className="flex items-center gap-2">
                    <input value={choice} maxLength={100}
                      aria-label={t('businessScenarios.workflow.inputs.choiceLabel', { number: choiceIndex + 1 })}
                      onChange={event => update(index, { allowed_values: variable.allowed_values?.map((item, position) => position === choiceIndex ? event.target.value : item) })}
                      className={inputClass} />
                    <button type="button" aria-label={t('businessScenarios.workflow.inputs.removeChoice', { number: choiceIndex + 1 })}
                      disabled={variable.allowed_values?.length === 1}
                      onClick={() => update(index, { allowed_values: variable.allowed_values?.filter((_, position) => position !== choiceIndex) })}
                      className="text-gray-400 hover:text-red-500 disabled:opacity-40"><Trash2 className="w-3.5 h-3.5" /></button>
                  </div>
                ))}
                {variable.allowed_values && variable.allowed_values.length < 30 && <button type="button"
                  onClick={() => update(index, { allowed_values: [...(variable.allowed_values ?? []), ''] })}
                  className="text-[10px] font-bold text-blue-500 hover:text-blue-600">
                  {t('businessScenarios.workflow.inputs.addChoice')}
                </button>}
                {variable.allowed_values && <p className="text-[10px] text-gray-500 dark:text-gray-400">
                  {t('businessScenarios.workflow.inputs.choiceHelp')}
                </p>}
              </div>}
              {variable.type === 'number' && <div className="grid grid-cols-2 gap-3">
                {(['minimum', 'maximum'] as const).map(field => <div key={field} className="space-y-1">
                  <label htmlFor={`${id}-${index}-${field}`} className="text-[9px] uppercase font-bold text-gray-400">{t(`businessScenarios.workflow.inputs.${field}`)}</label>
                  <input id={`${id}-${index}-${field}`} type="number" step="any"
                    value={variable[field] ?? ''} onChange={event => update(index, { [field]: event.target.value === '' ? undefined : Number(event.target.value) })}
                    className={inputClass} />
                </div>)}
              </div>}
              {(variable.type === 'string' || variable.type === 'number') && <p className="text-[10px] text-gray-500 dark:text-gray-400">
                {t('businessScenarios.workflow.inputs.constraintHelp')}
              </p>}
            </div>
          ))}
          {variables.length === 0 && <div className="text-center py-6 px-4 border-2 border-dashed border-gray-100 dark:border-gray-800 rounded-2xl"><p className="text-[10px] text-gray-400 uppercase tracking-widest leading-relaxed">{t('workflow.debug.no_input_variables')}</p></div>}
        </div>
      </div>
    </div>
  );
}
