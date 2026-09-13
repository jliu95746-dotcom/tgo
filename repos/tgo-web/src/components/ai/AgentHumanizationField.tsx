import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import SkillsApiService, { type SkillSummary } from '@/services/skillsApi';
import HumanizationSkillModal from './HumanizationSkillModal';
import Toggle from '@/components/ui/Toggle';

interface Props {
  disabled?: boolean;
  name?: string | null;
  enabled?: boolean;
  onChange: (value: { humanization_skill_name: string | null; humanization_skill_enabled: boolean }) => void;
}

export default function AgentHumanizationField({ name, enabled, onChange, disabled = false }: Props) {
  const { t } = useTranslation();
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    SkillsApiService.listSkills().then(list => {
      if (!cancelled) setSkills(list.filter(skill => skill.skill_type === 'humanization'));
    }).catch((reason: unknown) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [reload]);
  return <section className="space-y-3 rounded-2xl border border-violet-200 bg-violet-50/50 p-5 dark:border-violet-800 dark:bg-violet-900/10">
    <div className="flex items-center justify-between gap-3">
      <h3 className="font-bold text-gray-900 dark:text-gray-100">{t('employeeStyle.title')}</h3>
      <div className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200">
        <span>{t('employeeStyle.enable')}</span>
        <Toggle aria-label={t('employeeStyle.enable')} checked={!!enabled} disabled={disabled || !name}
          onChange={checked => onChange({ humanization_skill_name: name || null, humanization_skill_enabled: checked })} />
      </div>
    </div>
    <p className="text-xs text-gray-500 dark:text-gray-400">{t('employeeStyle.explanation')}</p>
    <div className="flex gap-2">
      <select aria-label={t('employeeStyle.select')} value={name || ''} disabled={disabled || loading}
        onChange={event => onChange({ humanization_skill_name: event.target.value || null,
          humanization_skill_enabled: !!event.target.value })}
        className="min-w-0 flex-1 rounded-lg border border-gray-300 bg-white p-2 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100">
        <option value="">{t(loading ? 'employeeStyle.loading' : 'employeeStyle.none')}</option>
        {name && !skills.some(skill => skill.name === name) && <option value={name}>{name}</option>}
        {skills.map(skill => <option key={skill.name} value={skill.name}>{skill.display_name || skill.name}</option>)}
      </select>
      <button type="button" disabled={disabled} onClick={() => setCreating(true)} className="rounded-lg px-3 text-sm text-violet-600 dark:text-violet-300">{t('employeeStyle.create')}</button>
    </div>
    {error && <div role="alert" className="text-xs text-red-600">{error} <button type="button" onClick={() => setReload(value => value + 1)}>{t('common.retry', '重试')}</button></div>}
    <p className="text-xs text-gray-500 dark:text-gray-400">{t('employeeStyle.trainingHint')}</p>
    {creating && createPortal(<HumanizationSkillModal isOpen onClose={() => setCreating(false)} onSaved={skill => {
      setSkills(current => [...current.filter(item => item.name !== skill.name), skill]);
      onChange({ humanization_skill_name: skill.name, humanization_skill_enabled: true });
    }} />, document.body)}
  </section>;
}
