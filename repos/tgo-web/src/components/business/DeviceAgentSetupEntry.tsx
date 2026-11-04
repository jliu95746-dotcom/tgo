import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowRight, Loader2, MonitorSmartphone, X } from 'lucide-react';
import { useDeviceSetupStore } from '@/stores/deviceSetupStore';
import { useAuthStore } from '@/stores/authStore';
import { useAIStore } from '@/stores/aiStore';
import { useScenarioDraftStore } from '@/stores/scenarioDraftStore';
import { getDevice, getDeviceReadPolicy, getDeviceTools } from '@/services/deviceControlApi';
import { isVerifiedDeviceReadPolicy, supportsDeviceScenario } from '@/utils/deviceSetup';
import ConfirmDialog from '@/components/ui/ConfirmDialog';
import { SYSTEM_DEFAULT_MODEL } from '@/services/aiAgentsApi';

/** Continues an explicit selection without replacing another employee draft. */
export default function DeviceAgentSetupEntry() {
  const { t } = useTranslation();
  const { handoff, clear } = useDeviceSetupStore();
  const form = useAIStore(state => state.createAgentFormData);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirmReplace, setConfirmReplace] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), [handoff]);
  if (!handoff) return null;
  const hasDraft = Boolean(form.name || form.description || form.profession || form.companySummary
    || form.knowledgeBases.length || form.tools.length || form.workflows.length || useScenarioDraftStore.getState().current);

  const continueSetup = async (replacementConfirmed = false) => {
    if (busy || !handoff) return;
    const expectedDeviceId = useAIStore.getState().createAgentFormData.boundDeviceId;
    if (expectedDeviceId && expectedDeviceId !== handoff.deviceId && !replacementConfirmed) {
      setConfirmReplace(true); return;
    }
    setConfirmReplace(false); setBusy(true); setError('');
    const abort = new AbortController();
    request.current = abort;
    try {
      if (handoff.scenarioId !== 'files') throw new Error('Device action is not verified');
      const [device, result, policy] = await Promise.all([
        getDevice(handoff.deviceId, { signal: abort.signal }),
        getDeviceTools(handoff.deviceId, { signal: abort.signal }),
        getDeviceReadPolicy(handoff.deviceId, { signal: abort.signal }),
      ]);
      const user = useAuthStore.getState().user;
      if (abort.signal.aborted || useDeviceSetupStore.getState().handoff !== handoff) return;
      if (!user || user.id !== handoff.staffId || user.project_id !== handoff.projectId || user.role !== handoff.role) return;
      if (device.id !== handoff.deviceId || device.project_id !== handoff.projectId
          || device.status !== 'online' || !supportsDeviceScenario(handoff.scenarioId, result.tools)
          || !isVerifiedDeviceReadPolicy(policy, handoff.deviceId, handoff.projectId)) {
        throw new Error('Device selection no longer available');
      }
      const state = useAIStore.getState();
      if (state.createAgentFormData.boundDeviceId !== expectedDeviceId) throw new Error('Device draft changed');
      const current = state.createAgentFormData;
      const keepDraft = Boolean(current.name || current.description || current.profession || current.companySummary
        || current.knowledgeBases.length || current.tools.length || current.workflows.length || useScenarioDraftStore.getState().current);
      if (!keepDraft) {
        // Keep default/advanced preferences and fill only this role's fields.
        state.setCreateAgentFormData({ scenarioId: 'assistant', profession: t('businessScenarios.scenes.assistant.title'),
          name: t(`businessScenarios.deviceGuide.employeeNames.${handoff.scenarioId}`),
          description: t(`businessScenarios.deviceGuide.instructions.${handoff.scenarioId}`),
          llmModel: current.llmModel || SYSTEM_DEFAULT_MODEL });
        if (!useScenarioDraftStore.getState().pendingCreation) useScenarioDraftStore.getState().startNew();
      }
      state.setCreateAgentFormData({ boundDeviceId: device.id });
      useScenarioDraftStore.getState().setStep(keepDraft ? 'resources' : 'business');
      state.setShowCreateAgentModal(true);
      clear();
    } catch {
      if (!abort.signal.aborted && useDeviceSetupStore.getState().handoff === handoff) setError(t('businessScenarios.deviceGuide.changed'));
    } finally { if (request.current === abort) { request.current = null; setBusy(false); } }
  };
  return <>
    <section aria-label={t('businessScenarios.deviceGuide.handoffTitle')} className="rounded-2xl border border-blue-200 bg-blue-50 p-5 dark:border-blue-800 dark:bg-blue-900/20">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 font-semibold text-blue-800 dark:text-blue-200"><MonitorSmartphone size={18} />{t('businessScenarios.deviceGuide.handoffTitle')}</h3>
          <p className="mt-2 break-words text-sm text-blue-700 dark:text-blue-300">{t('businessScenarios.deviceGuide.handoffDescription', { device: handoff.deviceName, purpose: t(`businessScenarios.scenes.${handoff.scenarioId}.title`) })}</p>
          <p className="mt-2 text-xs leading-5 text-blue-700 dark:text-blue-300">{t('businessScenarios.deviceGuide.handoffHint')}</p>
        </div>
        <button type="button" disabled={busy} aria-label={t('businessScenarios.close')} onClick={clear} className="shrink-0 rounded-lg p-1.5 text-blue-600 hover:bg-blue-100 dark:hover:bg-blue-900"><X size={18} /></button>
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-red-600 dark:text-red-400">{error}</p>}
      <button type="button" disabled={busy} onClick={() => void continueSetup()} className="mt-4 flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
        {busy ? <Loader2 size={16} className="animate-spin" /> : <ArrowRight size={16} />}
        {t(`businessScenarios.deviceGuide.${hasDraft ? 'continueDraft' : 'createEmployee'}`)}
      </button>
    </section>
    <ConfirmDialog isOpen={confirmReplace} title={t('businessScenarios.deviceGuide.replaceTitle')}
      message={t('businessScenarios.deviceGuide.replaceMessage', { name: handoff.deviceName })}
      onConfirm={() => void continueSetup(true)} onCancel={() => setConfirmReplace(false)} />
  </>;
}
