import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import type { CreateAgentFormData } from '@/types';
import type { ScenarioReadinessReport } from '@/types/scenarioResources';
import { BusinessScenariosApi } from '@/services/businessScenariosApi';
import { SYSTEM_DEFAULT_MODEL } from '@/services/aiAgentsApi';
import { useAuthStore } from '@/stores/authStore';
import { useScenarioResourceStore } from '@/stores/scenarioResourceStore';

export default function ScenarioReadinessCheck({ form }: { form: CreateAgentFormData }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const project = useAuthStore(state => state.user?.project_id);
  const channel = useScenarioResourceStore(state => state.channel);
  const [result, setResult] = useState<{ key: string; report: ScenarioReadinessReport } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const key = JSON.stringify([project, channel, form.scenarioId, form.llmModel, form.knowledgeBases, form.tools, form.workflows, form.boundDeviceId]);
  useEffect(() => {
    generation.current += 1;
    setError(''); setBusy(false);
    return () => { generation.current += 1; };
  }, [key]);
  const report = result?.key === key ? result.report : null;
  const check = async () => {
    if (!form.scenarioId || !project || busy) return;
    const version = ++generation.current;
    setBusy(true); setError(''); setResult(null);
    try {
      const boundary = form.llmModel.indexOf(':');
      if (form.llmModel && form.llmModel !== SYSTEM_DEFAULT_MODEL && boundary < 1) throw new Error(t('businessScenarios.readiness.invalidModel'));
      const bindings = {
        model: boundary > 0 ? { provider_id: form.llmModel.slice(0, boundary), model_id: form.llmModel.slice(boundary + 1) } : null,
        knowledge_ids: form.knowledgeBases, tool_ids: form.tools, workflow_ids: form.workflows,
        device_ids: form.boundDeviceId ? [form.boundDeviceId] : [], connection_ids: [],
      };
      const data = await BusinessScenariosApi.readiness({ template_id: form.scenarioId, channel, bindings });
      if (version !== generation.current) return;
      if (data.project_id !== project || data.channel !== channel || data.template_id !== form.scenarioId) throw new Error(t('businessScenarios.readiness.failed'));
      setResult({ key, report: data });
    } catch (cause) {
      if (version === generation.current) setError(cause instanceof Error ? cause.message : t('businessScenarios.readiness.failed'));
    } finally { if (version === generation.current) setBusy(false); }
  };
  if (!form.scenarioId) return null;
  return <div className="mt-4 border-t border-blue-100 pt-3 dark:border-blue-900">
    <button type="button" disabled={busy} onClick={() => void check()} className="rounded-lg bg-white px-3 py-2 text-xs font-medium text-blue-700 disabled:opacity-50 dark:bg-gray-800 dark:text-blue-300">{t(`businessScenarios.readiness.${busy ? 'checking' : 'check'}`)}</button>
    {error && <p role="alert" className="mt-2 text-xs text-red-600">{error}</p>}
    {report && <div className="mt-3 text-xs"><p className="text-gray-600 dark:text-gray-300">{t(`businessScenarios.readiness.${report.configuration_ready ? 'prepared' : 'incomplete'}`)}</p><ul className="mt-2 space-y-2">{report.issues.map((issue, index) => <li key={`${issue.kind}-${index}`} className="flex flex-wrap items-center justify-between gap-2 text-gray-600 dark:text-gray-300"><span>{issue.message}</span><button type="button" className="text-blue-600 hover:underline" onClick={() => issue.action === 'retry' ? void check() : navigate(issue.route)}>{t(`businessScenarios.readiness.actions.${issue.action}`)}</button></li>)}</ul><p className="mt-2 text-gray-500">{t('businessScenarios.readiness.notTested')}</p></div>}
  </div>;
}
