import { useState, useEffect, useContext, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { FiX, FiEye, FiEyeOff } from 'react-icons/fi';
import { useProvidersStore, type ModelProviderConfig, type AIModelConfig } from '@/stores/providersStore';
import { ToastContext } from '@/components/ui/ToastContainer';
import ProviderModelPicker from './ProviderModelPicker';
import { connectionDraft, existingModelConfigs, providerEditPatch, providerPresets } from '@/utils/providerSetup';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  editingProvider: ModelProviderConfig | null;
  editingModelId?: string;
  onSaved?: () => void;
}
const fieldClass = 'w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:ring-2 focus:ring-blue-500';
export default function ProviderConfigModal({ isOpen, onClose, editingProvider, editingModelId, onSaved }: Props) {
  const { t } = useTranslation();
  const toast = useContext(ToastContext);
  const { addProvider, updateProvider } = useProvidersStore();
  const [draft, setDraft] = useState<ModelProviderConfig | null>(null);
  const [models, setModels] = useState<AIModelConfig[]>([]);
  const [busy, setBusy] = useState(false);
  const [modelBusy, setModelBusy] = useState(false);
  const [reveal, setReveal] = useState(false);
  const [error, setError] = useState('');
  const busyRef = useRef(false);
  useEffect(() => {
    setDraft(isOpen ? editingProvider ? { ...editingProvider, apiKey: '' } : {
      id: '', kind: 'deepseek', name: 'DeepSeek', apiKey: '', apiBaseUrl: providerPresets[0].base,
      enabled: true, createdAt: 0, updatedAt: 0,
    } : null);
    setModels(editingProvider ? existingModelConfigs(editingProvider) : []);
    setError(''); setReveal(false); setBusy(false); setModelBusy(false); busyRef.current = false;
  }, [isOpen, editingProvider]);
  if (!isOpen || !draft) return null;
  const working = busy || modelBusy;
  const close = () => { if (!busyRef.current && !modelBusy) { onSaved?.(); onClose(); } };
  const validConnection = () => {
    if (!draft.name.trim() || !draft.apiBaseUrl?.trim() || (!editingProvider && !draft.apiKey.trim() && draft.kind !== 'ollama')) {
      setError(t('modelSetup.required')); return false;
    }
    try {
      const url = new URL(draft.apiBaseUrl);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new Error();
    } catch { setError(t('modelSetup.invalidAddress')); return false; }
    setError(''); return true;
  };
  const save = async () => {
    if (busyRef.current || modelBusy || !validConnection()) return;
    if (!models.length) { setError(t('modelSetup.chooseOne')); return; }
    busyRef.current = true; setBusy(true); setError('');
    try {
      if (editingProvider) {
        const patch = providerEditPatch(editingProvider, draft, models);
        if (Object.keys(patch).length) await updateProvider(editingProvider.id, patch);
      } else {
        await addProvider({
          ...draft, name: draft.name.trim(), apiKey: draft.apiKey.trim() || (draft.kind === 'ollama' ? 'ollama' : ''),
          apiBaseUrl: draft.apiBaseUrl?.trim(), models: models.map(model => model.id), modelConfigs: models,
          modelTypes: Object.fromEntries(models.map(model => [model.id, model.type])),
        });
      }
      toast?.showToast('success', t('modelSetup.saved')); onSaved?.(); onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('common.saveFailed'));
    } finally { busyRef.current = false; setBusy(false); }
  };
  return <div className="fixed inset-0 z-50 bg-black/50 flex items-center justify-center p-4" onKeyDown={event => { if (event.key === 'Escape') close(); }}>
    <div role="dialog" aria-modal="true" aria-labelledby="provider-setup-title" className="w-full max-w-2xl max-h-[92vh] rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-xl flex flex-col">
      <header className="px-6 py-4 border-b dark:border-gray-700 flex items-center justify-between">
        <h2 id="provider-setup-title" className="text-lg font-semibold dark:text-white">{t(editingProvider ? 'modelSetup.editModelConfig' : 'modelSetup.add')}</h2>
        <button aria-label={t('common.close')} disabled={working} onClick={close} className="p-2 text-gray-400"><FiX /></button>
      </header>
      <div className="p-6 overflow-y-auto space-y-5">
        <p className="text-sm text-gray-500 dark:text-gray-400">{t('modelSetup.vendorModelHint')}</p>
        {error && <p role="alert" className="rounded-lg p-3 bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300 text-sm">{error}</p>}
        <section className="rounded-lg border border-gray-200 dark:border-gray-700 p-4 space-y-3">
          <h3 className="font-semibold dark:text-white">{t('modelSetup.vendorSection')}</h3>
          <label className="block text-sm dark:text-gray-200">{t('modelSetup.serviceLabel')}
            <select disabled={!!editingProvider || working} value={draft.kind} className={fieldClass} onChange={event => {
              const preset = providerPresets.find(item => item.kind === event.target.value)!;
              setDraft({ ...draft, kind: preset.kind, name: preset.label, apiBaseUrl: preset.base, apiKey: '', params: undefined });
              setModels([]); setError('');
            }}>{providerPresets.map(preset => <option key={preset.kind} value={preset.kind}>{preset.label}</option>)}</select>
          </label>
          <details open={!editingProvider}>
            <summary className="cursor-pointer text-sm text-blue-600 dark:text-blue-400">{t('modelSetup.connectionSettings')}</summary>
            <div className="mt-3 space-y-3">
              {editingProvider && <p className="text-xs text-gray-500 dark:text-gray-400">{t('modelSetup.preserve')}</p>}
              <p className="text-xs text-gray-500 dark:text-gray-400">{t('modelSetup.sharedConnection')}</p>
              <label className="block text-sm dark:text-gray-200">{t('modelSetup.name')}<input disabled={working} maxLength={100} className={fieldClass} value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} /></label>
              <label className="block text-sm dark:text-gray-200">{t('modelSetup.address')}<input disabled={working} className={fieldClass} value={draft.apiBaseUrl || ''} onChange={event => setDraft({ ...draft, apiBaseUrl: event.target.value })} /></label>
              <label className="block text-sm dark:text-gray-200">{t('modelSetup.key')}<div className="relative mt-1">
                <input disabled={working} autoComplete="new-password" type={reveal ? 'text' : 'password'} className={`${fieldClass} pr-12`} value={draft.apiKey} placeholder={t(editingProvider ? 'modelSetup.keepKey' : 'modelSetup.enterKey')} onChange={event => setDraft({ ...draft, apiKey: event.target.value })} />
                <button type="button" aria-label={t('modelSetup.revealKey')} onClick={() => setReveal(!reveal)} className="absolute right-3 top-2.5 text-gray-400">{reveal ? <FiEyeOff /> : <FiEye />}</button>
              </div></label>
              {draft.kind === 'azure' && <label className="block text-sm dark:text-gray-200">API Version<input disabled={working} className={fieldClass} value={draft.params?.azure?.apiVersion || ''} onChange={event => setDraft({ ...draft, params: { ...draft.params, azure: { ...draft.params?.azure, apiVersion: event.target.value } } })} /></label>}
            </div>
          </details>
        </section>
        <section className="rounded-lg border border-gray-200 dark:border-gray-700 p-4 space-y-3">
          <p className="text-xs text-gray-500 dark:text-gray-400">{t('modelSetup.modelsBelongTo', { vendor: draft.name })}</p>
          {editingModelId && <p className="text-sm text-blue-600 dark:text-blue-400">{t('modelSetup.currentEditingModel', { model: editingModelId })}</p>}
          <ProviderModelPicker key={`${draft.id}:${draft.kind}`} connection={connectionDraft(draft)} models={models} lockedModelIds={editingProvider?.models} focusModelId={editingModelId} onChange={setModels} disabled={busy} beforeFetch={validConnection} onBusyChange={setModelBusy} />
        </section>
      </div>
      <footer className="px-6 py-4 border-t dark:border-gray-700 flex justify-end gap-3">
        <button disabled={working} onClick={close} className="px-4 py-2 rounded-lg border dark:border-gray-600 dark:text-gray-200">{t('common.cancel')}</button>
        <button disabled={working || !models.length} onClick={save} className="px-5 py-2 rounded-lg bg-blue-600 text-white disabled:opacity-50 whitespace-nowrap">{t(busy ? 'modelSetup.processing' : editingProvider ? 'modelSetup.save' : 'modelSetup.finish')}</button>
      </footer>
    </div>
  </div>;
}
