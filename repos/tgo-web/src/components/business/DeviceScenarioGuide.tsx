import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { CheckCircle2, Circle, Loader2, Plus, RefreshCw, X } from 'lucide-react';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import { useAuthStore } from '@/stores/authStore';
import { useAIStore } from '@/stores/aiStore';
import { useDeviceControlStore } from '@/stores/deviceControlStore';
import { useDeviceSetupStore } from '@/stores/deviceSetupStore';
import { getDeviceReadPolicy, getDeviceScenarioDraft, getDeviceTools, publishDeviceReadPolicy, saveDeviceScenarioDraft, testDeviceReadPolicy } from '@/services/deviceControlApi';
import { APIError } from '@/services/api';
import { supportsDeviceScenario } from '@/utils/deviceSetup';
import type { DeviceToolDescription } from '@/types/businessScenarios';
import type { DeviceScenarioId } from '@/types/deviceSetup';
import type { DeviceReadPolicy } from '@/types/deviceControl';
import DeviceScriptPanel from './DeviceScriptPanel';

interface Props {
  scenarioId: DeviceScenarioId;
  initialDeviceId?: string;
  onClose: () => void;
  onConnect: (selectedDeviceId: string) => void;
}

export default function DeviceScenarioGuide({ scenarioId, initialDeviceId = '', onClose, onConnect }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const user = useAuthStore(state => state.user);
  const origin = useRef(user).current;
  const { devices, isLoading, loadError, hasLoadedDevices, loadDevices } = useDeviceControlStore();
  const [selectedId, setSelectedId] = useState(initialDeviceId);
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;
  const [tools, setTools] = useState<DeviceToolDescription[]>([]);
  const [checking, setChecking] = useState(false);
  const [checkedId, setCheckedId] = useState('');
  const [failed, setFailed] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [purpose, setPurpose] = useState('');
  const [businessSystem, setBusinessSystem] = useState('');
  const [targetDirectory, setTargetDirectory] = useState('');
  const [draftRevision, setDraftRevision] = useState(0);
  const [loadingDraft, setLoadingDraft] = useState(false);
  const [draftLoadFailed, setDraftLoadFailed] = useState(false);
  const [savingDraft, setSavingDraft] = useState(false);
  const [draftSaved, setDraftSaved] = useState(false);
  const [draftSaveFailed, setDraftSaveFailed] = useState(false);
  const [readPolicy, setReadPolicy] = useState<DeviceReadPolicy | null>(null);
  const [policyLoading, setPolicyLoading] = useState(false);
  const [policyLoadFailed, setPolicyLoadFailed] = useState(false);
  const [policySaving, setPolicySaving] = useState(false);
  const [policySaveFailed, setPolicySaveFailed] = useState(false);
  const [maxLines, setMaxLines] = useState(100);
  const [maxOffset, setMaxOffset] = useState(10000);
  const [policyRefresh, setPolicyRefresh] = useState(0);
  const [testPath, setTestPath] = useState('');
  const [testMarker, setTestMarker] = useState('');
  const [testingRead, setTestingRead] = useState(false);
  const [readTestError, setReadTestError] = useState(false);
  const [continuing, setContinuing] = useState(false);
  const dialogRef = useDialogFocus<HTMLElement>(onClose);
  const ownDevices = devices.filter(device => device.project_id === user?.project_id);
  const online = ownDevices.filter(device => device.status === 'online');
  const device = ownDevices.find(value => value.id === selectedId);
  const deviceId = device?.id;
  const deviceStatus = device?.status;
  const sameOrigin = Boolean(user && user.id === origin?.id && user.project_id === origin?.project_id && user.role === origin?.role);
  const capability = scenarioId === 'files' ? 'files' : 'commands';
  const supported = supportsDeviceScenario(scenarioId, tools);
  const ready = sameOrigin && !isLoading && !loadError && hasLoadedDevices
    && device?.status === 'online' && checkedId === device.id && !checking && !failed && supported;
  const policyMatchesDraft = readPolicy?.allowed_directory === targetDirectory.trim()
    && readPolicy.max_lines === maxLines && readPolicy.max_offset === maxOffset;
  const policyActive = policyMatchesDraft && readPolicy?.status === 'active';
  const canContinue = scenarioId === 'files' && ready && draftSaved && policyActive
    && readPolicy?.verified === true && !readTestError && !testingRead && !continuing
    && !loadingDraft && !savingDraft && !policyLoading && !policySaving;
  const isCurrentSelection = (expectedDeviceId: string) => {
    const currentUser = useAuthStore.getState().user;
    const currentDevices = useDeviceControlStore.getState();
    return Boolean(currentUser && currentUser.id === origin?.id
      && currentUser.project_id === origin?.project_id && currentUser.role === origin?.role
      && selectedIdRef.current === expectedDeviceId
      && !currentDevices.isLoading && !currentDevices.loadError && currentDevices.hasLoadedDevices
      && currentDevices.devices.some(value => value.id === expectedDeviceId
        && value.project_id === currentUser.project_id && value.status === 'online'));
  };

  useEffect(() => { if (!sameOrigin) onClose(); }, [sameOrigin, onClose]);
  useEffect(() => {
    setTestPath(''); setTestMarker(''); setReadTestError(false);
  }, [deviceId, scenarioId]);
  useEffect(() => {
    if (!selectedId && !isLoading && !loadError && online.length === 1) {
      selectedIdRef.current = online[0].id;
      setSelectedId(online[0].id);
    }
  }, [selectedId, isLoading, loadError, online]);
  useEffect(() => {
    const abort = new AbortController();
    setTools([]); setCheckedId(''); setFailed(false); setChecking(false);
    if (!sameOrigin || !deviceId || deviceStatus !== 'online' || isLoading || loadError || scenarioId !== 'files') return;
    setChecking(true);
    getDeviceTools(deviceId, { signal: abort.signal }).then(result => {
      if (abort.signal.aborted) return;
      if (!Array.isArray(result.tools)) throw new Error('Invalid device tool listing');
      setTools(result.tools); setCheckedId(deviceId);
    }).catch(() => { if (!abort.signal.aborted) setFailed(true); })
      .finally(() => { if (!abort.signal.aborted) setChecking(false); });
    return () => abort.abort();
  }, [deviceId, deviceStatus, sameOrigin, isLoading, loadError, refresh, scenarioId]);

  useEffect(() => {
    const abort = new AbortController();
    setPurpose(''); setBusinessSystem(''); setTargetDirectory(''); setDraftRevision(0);
    setDraftSaved(false); setDraftSaveFailed(false); setDraftLoadFailed(false);
    setLoadingDraft(false);
    if (!sameOrigin || !deviceId) return;
    setLoadingDraft(true);
    getDeviceScenarioDraft(deviceId, scenarioId, { signal: abort.signal }).then(draft => {
      if (abort.signal.aborted) return;
      if (draft.device_id !== deviceId || draft.project_id !== user?.project_id || draft.scenario !== scenarioId) {
        throw new Error('Device scenario draft scope mismatch');
      }
      setPurpose(draft.purpose); setBusinessSystem(draft.business_system ?? '');
      setTargetDirectory(draft.target_directory ?? '');
      setDraftRevision(draft.revision); setDraftSaved(true);
    }).catch(error => {
      if (abort.signal.aborted) return;
      if (!(error instanceof APIError && error.status === 404)) setDraftLoadFailed(true);
    }).finally(() => { if (!abort.signal.aborted) setLoadingDraft(false); });
    return () => abort.abort();
  }, [deviceId, scenarioId, sameOrigin, user?.project_id]);

  useEffect(() => {
    const abort = new AbortController();
    setReadPolicy(null); setPolicyLoadFailed(false); setPolicySaving(false);
    setPolicySaveFailed(false); setMaxLines(100); setMaxOffset(10000);
    setPolicyLoading(false);
    if (!sameOrigin || !deviceId || scenarioId !== 'files' || user?.role !== 'admin') return;
    setPolicyLoading(true);
    getDeviceReadPolicy(deviceId, { signal: abort.signal }).then(policy => {
      if (abort.signal.aborted) return;
      if (policy.device_id !== deviceId || policy.project_id !== user?.project_id || policy.tool_name !== 'fs_read') {
        throw new Error('Device read policy scope mismatch');
      }
      setReadPolicy(policy); setMaxLines(policy.max_lines); setMaxOffset(policy.max_offset);
    }).catch(error => {
      if (abort.signal.aborted) return;
      if (!(error instanceof APIError && error.status === 404)) setPolicyLoadFailed(true);
    }).finally(() => { if (!abort.signal.aborted) setPolicyLoading(false); });
    return () => abort.abort();
  }, [deviceId, deviceStatus, scenarioId, sameOrigin, user?.project_id, user?.role, policyRefresh]);

  const saveDraft = async () => {
    if (!sameOrigin || !deviceId || !purpose.trim() || loadingDraft || draftLoadFailed || savingDraft) return;
    setSavingDraft(true); setDraftSaveFailed(false); setDraftSaved(false);
    try {
      const draft = await saveDeviceScenarioDraft(deviceId, scenarioId, {
        expected_revision: draftRevision, purpose: purpose.trim(),
        business_system: businessSystem.trim() || null,
        target_directory: targetDirectory.trim() || null,
      });
      if (!isCurrentSelection(deviceId)) return;
      if (draft.device_id !== deviceId || draft.project_id !== user?.project_id || draft.scenario !== scenarioId) {
        throw new Error('Device scenario draft scope mismatch');
      }
      setDraftRevision(draft.revision); setDraftSaved(true);
    } catch { if (isCurrentSelection(deviceId)) setDraftSaveFailed(true); } finally { setSavingDraft(false); }
  };

  const publishPolicy = async () => {
    if (!sameOrigin || user?.role !== 'admin' || !deviceId || !draftSaved || !targetDirectory.trim()
      || policyLoading || policyLoadFailed || policySaving
      || !Number.isInteger(maxLines) || maxLines < 1 || maxLines > 500
      || !Number.isInteger(maxOffset) || maxOffset < 0 || maxOffset > 100000) return;
    setPolicySaving(true); setPolicySaveFailed(false);
    try {
      const policy = await publishDeviceReadPolicy(deviceId, {
        expected_draft_revision: draftRevision,
        expected_policy_revision: readPolicy?.revision ?? 0,
        max_lines: maxLines, max_offset: maxOffset,
      });
      if (!isCurrentSelection(deviceId)) return;
      if (policy.device_id !== deviceId || policy.project_id !== user?.project_id || policy.tool_name !== 'fs_read') {
        throw new Error('Device read policy scope mismatch');
      }
      setReadPolicy(policy);
    } catch { if (isCurrentSelection(deviceId)) setPolicySaveFailed(true); } finally { setPolicySaving(false); }
  };

  const testRead = async () => {
    if (!sameOrigin || user?.role !== 'admin' || !deviceId || !policyActive
      || !testPath.trim() || testMarker.trim().length < 4 || testingRead) return;
    setTestingRead(true); setReadTestError(false);
    try {
      const policy = await testDeviceReadPolicy(deviceId, {
        path: testPath.trim(), expected_text: testMarker.trim(),
        limit: Math.min(maxLines, 20),
      });
      if (!isCurrentSelection(deviceId)) return;
      if (policy.device_id !== deviceId || policy.project_id !== user?.project_id || policy.tool_name !== 'fs_read') {
        throw new Error('Device read policy scope mismatch');
      }
      setReadPolicy(policy);
      if (policy.verified) setTestMarker('');
    } catch { if (isCurrentSelection(deviceId)) setReadTestError(true); } finally { setTestingRead(false); }
  };

  const continueSetup = async () => {
    if (!canContinue || !device || !deviceId || !sameOrigin || user?.role !== 'admin') return;
    setContinuing(true);
    try {
      const current = await getDeviceReadPolicy(deviceId);
      if (!isCurrentSelection(deviceId)) return;
      setReadPolicy(current);
      if (current.device_id !== deviceId || current.project_id !== user.project_id
        || current.tool_name !== 'fs_read' || current.status !== 'active'
        || !current.verified || current.allowed_directory !== targetDirectory.trim()
        || current.max_lines !== maxLines || current.max_offset !== maxOffset) return;
      if (!useDeviceSetupStore.getState().prepare(scenarioId, device)) return;
      // Returning from an unfinished employee form must show the explicit handoff first.
      useAIStore.getState().setShowCreateAgentModal(false);
      onClose(); navigate('/ai/agents');
    } catch {
      if (isCurrentSelection(deviceId)) { setReadPolicy(null); setPolicyLoadFailed(true); }
    } finally { setContinuing(false); }
  };
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
    <section ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="device-scenario-title"
      className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-3xl bg-white p-6 shadow-xl dark:bg-gray-900">
      <div className="flex items-center justify-between gap-3">
        <h3 id="device-scenario-title" className="text-lg font-bold text-gray-900 dark:text-white">{t('businessScenarios.deviceGuide.title', { name: t(`businessScenarios.scenes.${scenarioId}.title`) })}</h3>
        <button type="button" aria-label={t('businessScenarios.close')} onClick={onClose} className="rounded-xl p-2 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"><X size={20} /></button>
      </div>
      <ol aria-label={t('businessScenarios.deviceGuide.progress')} className="my-5 grid grid-cols-3 gap-2">
        {(['choose', 'check', 'employee'] as const).map((step, index) => <li key={step} className={`rounded-xl p-3 text-xs ${index === 0 || (index === 1 && device) ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300' : 'bg-gray-50 text-gray-500 dark:bg-gray-800'}`}>
          <span className="mb-1 block font-semibold">{index + 1}</span>{t(`businessScenarios.deviceGuide.steps.${step}`)}
        </li>)}
      </ol>
      <p className="mb-5 text-sm leading-6 text-gray-600 dark:text-gray-300">{t(`businessScenarios.scenes.${scenarioId}.benefit`)}</p>
      <label className="block text-sm font-medium text-gray-700 dark:text-gray-200">
        {t('businessScenarios.deviceGuide.choose')}
        <select value={selectedId} onChange={event => {
          selectedIdRef.current = event.target.value;
          setSelectedId(event.target.value);
        }} disabled={isLoading || savingDraft || policySaving || testingRead || continuing || Boolean(loadError)}
          className="mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-sm dark:border-gray-700 dark:bg-gray-800">
          <option value="">{t('businessScenarios.deviceGuide.select')}</option>
          {ownDevices.map(value => <option key={value.id} value={value.id}>{value.device_name}{value.status !== 'online' ? ` · ${t('deviceControl.stats.offline')}` : ''}</option>)}
        </select>
      </label>
      <div className="mt-3 flex flex-wrap gap-3">
        <button type="button" onClick={() => onConnect(selectedId)} className="flex items-center gap-1 rounded-lg py-2 text-sm text-blue-600"><Plus size={15} />{t('businessScenarios.deviceGuide.connect')}</button>
        <button type="button" disabled={isLoading} onClick={() => void loadDevices()} className="flex items-center gap-1 rounded-lg py-2 text-sm text-gray-500"><RefreshCw size={14} className={isLoading ? 'animate-spin' : ''} />{t('businessScenarios.deviceGuide.refresh')}</button>
      </div>
      {loadError && <p role="alert" className="mt-3 rounded-xl bg-red-50 p-3 text-sm text-red-600 dark:bg-red-900/20">{t('businessScenarios.deviceGuide.listFailed')}</p>}
      {!isLoading && !loadError && !online.length && <p className="mt-3 text-sm text-gray-500">{t('businessScenarios.deviceGuide.noOnline')}</p>}
      {device && scenarioId === 'files' && <div className="mt-5 rounded-2xl border border-gray-200 p-4 dark:border-gray-700">
        <h4 className="text-sm font-medium text-gray-800 dark:text-gray-200">{t('businessScenarios.deviceGuide.required', { capability: t(`businessScenarios.device.${capability}`) })}</h4>
        <p role={failed ? 'alert' : 'status'} className="mt-3 flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
          {checking ? <Loader2 size={17} className="animate-spin" /> : ready ? <CheckCircle2 size={17} className="text-green-500" /> : <Circle size={17} />}
          {t(`businessScenarios.deviceGuide.${checking ? 'checking' : failed ? 'failed' : ready ? 'detected' : 'missing'}`)}
        </p>
        <button type="button" disabled={checking || device.status !== 'online'} onClick={() => setRefresh(value => value + 1)} className="mt-3 rounded-lg py-1 text-xs font-medium text-blue-600">{t('businessScenarios.device.retry')}</button>
      </div>}
      {device && scenarioId === 'files' && <div className="mt-5 rounded-2xl border border-gray-200 p-4 dark:border-gray-700">
        <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200">{t('businessScenarios.deviceGuide.readPolicyTitle')}</h4>
        <p className="mt-2 text-xs leading-5 text-gray-500">{t('businessScenarios.deviceGuide.readPolicyHint')}</p>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <label className="text-sm text-gray-700 dark:text-gray-200">{t('businessScenarios.deviceGuide.readPolicyMaxLines')}
            <input type="number" min={1} max={500} value={maxLines} disabled={policyLoading || policySaving}
              onChange={event => setMaxLines(Number(event.target.value))}
              className="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-800" />
          </label>
          <label className="text-sm text-gray-700 dark:text-gray-200">{t('businessScenarios.deviceGuide.readPolicyMaxOffset')}
            <input type="number" min={0} max={100000} value={maxOffset} disabled={policyLoading || policySaving}
              onChange={event => setMaxOffset(Number(event.target.value))}
              className="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-800" />
          </label>
        </div>
        {policyLoadFailed && <p role="alert" className="mt-2 text-sm text-red-600">{t('businessScenarios.deviceGuide.readPolicyLoadFailed')}</p>}
        {policySaveFailed && <p role="alert" className="mt-2 text-sm text-red-600">{t('businessScenarios.deviceGuide.readPolicySaveFailed')}</p>}
        {readPolicy && <p role="status" className={`mt-2 text-sm ${policyActive ? 'text-green-600' : 'text-amber-600'}`}>
          {t(`businessScenarios.deviceGuide.${policyMatchesDraft ? policyActive ? 'readPolicyActive' : 'readPolicyPending' : 'readPolicyChanged'}`)}
        </p>}
        <div className="mt-3 flex flex-wrap gap-3">
          <button type="button" onClick={() => void publishPolicy()}
            disabled={user?.role !== 'admin' || !draftSaved || !targetDirectory.trim() || policyLoading || policyLoadFailed || policySaving
              || !Number.isInteger(maxLines) || maxLines < 1 || maxLines > 500
              || !Number.isInteger(maxOffset) || maxOffset < 0 || maxOffset > 100000}
            className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
            {t(`businessScenarios.deviceGuide.${policySaving ? 'readPolicySaving' : 'readPolicyApply'}`)}
          </button>
          <button type="button" onClick={() => setPolicyRefresh(value => value + 1)} disabled={policySaving}
            className="rounded-xl px-3 py-2 text-sm text-blue-600">{t('businessScenarios.deviceGuide.readPolicyRefresh')}</button>
        </div>
        {user?.role !== 'admin' && <p className="mt-2 text-xs text-gray-500">{t('businessScenarios.deviceGuide.readPolicyAdmin')}</p>}
      </div>}
      {device && scenarioId === 'files' && <div className="mt-5 rounded-2xl border border-gray-200 p-4 dark:border-gray-700">
        <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200">{t('businessScenarios.deviceGuide.readTestTitle')}</h4>
        <p className="mt-2 text-xs leading-5 text-gray-500">{t('businessScenarios.deviceGuide.readTestHint')}</p>
        <label className="mt-3 block text-sm text-gray-700 dark:text-gray-200">{t('businessScenarios.deviceGuide.readTestPath')}
          <input value={testPath} maxLength={1024} disabled={testingRead} onChange={event => setTestPath(event.target.value)}
            className="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-800" />
        </label>
        <label className="mt-3 block text-sm text-gray-700 dark:text-gray-200">{t('businessScenarios.deviceGuide.readTestMarker')}
          <input value={testMarker} maxLength={100} disabled={testingRead} onChange={event => setTestMarker(event.target.value)}
            className="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-800" />
        </label>
        <button type="button" onClick={() => void testRead()}
          disabled={!policyActive || user?.role !== 'admin' || !testPath.trim() || testMarker.trim().length < 4 || testingRead}
          className="mt-3 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
          {t(`businessScenarios.deviceGuide.${testingRead ? 'readTestRunning' : 'readTestRun'}`)}
        </button>
        {readTestError && <p role="alert" className="mt-2 text-sm text-red-600">{t('businessScenarios.deviceGuide.readTestError')}</p>}
        {readPolicy?.verified && policyActive && !readTestError && !testingRead && <p role="status" className="mt-2 text-sm text-green-600">{t('businessScenarios.deviceGuide.readTestPassed')}</p>}
        {readPolicy?.last_test_status === 'failed' && <p role="status" className="mt-2 text-sm text-amber-600">{t('businessScenarios.deviceGuide.readTestFailed')}</p>}
      </div>}
      {device && scenarioId === 'report' && <DeviceScriptPanel device={device} draftSaved={draftSaved && !loadingDraft && !savingDraft && !draftLoadFailed} />}
      {device && <div className="mt-5 rounded-2xl border border-gray-200 p-4 dark:border-gray-700">
        <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200">{t('businessScenarios.deviceGuide.draftTitle')}</h4>
        <label className="mt-3 block text-sm text-gray-700 dark:text-gray-200">{t('businessScenarios.deviceGuide.draftPurpose')}
          <input value={purpose} maxLength={255} disabled={loadingDraft || draftLoadFailed || savingDraft}
            onChange={event => { setPurpose(event.target.value); setDraftSaved(false); }}
            className="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-800" />
        </label>
        <label className="mt-3 block text-sm text-gray-700 dark:text-gray-200">{t('businessScenarios.deviceGuide.draftBusinessSystem')}
          <input value={businessSystem} maxLength={120} disabled={loadingDraft || draftLoadFailed || savingDraft}
            onChange={event => { setBusinessSystem(event.target.value); setDraftSaved(false); }}
            className="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-800" />
        </label>
        {scenarioId === 'files' && <label className="mt-3 block text-sm text-gray-700 dark:text-gray-200">{t('businessScenarios.deviceGuide.draftDirectory')}
          <input value={targetDirectory} maxLength={1024} disabled={loadingDraft || draftLoadFailed || savingDraft}
            onChange={event => { setTargetDirectory(event.target.value); setDraftSaved(false); }}
            className="mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-800" />
        </label>}
        <p className="mt-2 text-xs leading-5 text-gray-500">{t('businessScenarios.deviceGuide.draftDirectoryHint')}</p>
        {draftLoadFailed && <p role="alert" className="mt-2 text-sm text-red-600">{t('businessScenarios.deviceGuide.draftLoadFailed')}</p>}
        {draftSaveFailed && <p role="alert" className="mt-2 text-sm text-red-600">{t('businessScenarios.deviceGuide.draftSaveFailed')}</p>}
        {draftSaved && <p role="status" className="mt-2 text-sm text-green-600">{t('businessScenarios.deviceGuide.draftSaved')}</p>}
        <button type="button" onClick={() => void saveDraft()}
          disabled={!purpose.trim() || loadingDraft || draftLoadFailed || savingDraft}
          className="mt-3 rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
          {t(`businessScenarios.deviceGuide.${savingDraft ? 'draftSaving' : 'draftSave'}`)}
        </button>
        <p className="mt-2 text-xs text-gray-500">{t('businessScenarios.deviceGuide.draftOnly')}</p>
      </div>}
      <p className="mt-5 rounded-xl bg-blue-50 p-3 text-xs leading-6 text-blue-700 dark:bg-blue-900/20 dark:text-blue-300">{t('businessScenarios.deviceGuide.permissions')}</p>
      <button type="button" disabled={!canContinue} onClick={() => void continueSetup()} className="mt-5 w-full rounded-xl bg-blue-600 px-4 py-3 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">{t('businessScenarios.deviceGuide.continue')}</button>
      {ready && !draftSaved && <p className="mt-2 text-xs text-gray-500">{t('businessScenarios.deviceGuide.draftFirst')}</p>}
      {scenarioId === 'files' && ready && draftSaved && !policyActive && <p className="mt-2 text-xs text-gray-500">{t('businessScenarios.deviceGuide.readPolicyFirst')}</p>}
      {scenarioId === 'files' && ready && policyActive && !readPolicy?.verified && <p className="mt-2 text-xs text-gray-500">{t('businessScenarios.deviceGuide.readTestFirst')}</p>}
      {scenarioId === 'report' && <p className="mt-2 text-xs text-amber-600">{t('businessScenarios.deviceGuide.reportPending')}</p>}
      <p className="mt-3 text-xs leading-5 text-gray-500">{t('businessScenarios.deviceGuide.testHint')}</p>
    </section>
  </div>;
}
