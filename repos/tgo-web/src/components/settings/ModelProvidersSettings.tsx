import React, { useMemo, useState, useContext, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { FiCpu, FiLoader, FiPlus } from 'react-icons/fi';
import Button from '@/components/ui/Button';
import ConfirmDialog from '@/components/ui/ConfirmDialog';
import { useProvidersStore, type ModelProviderConfig } from '@/stores/providersStore';
import { useAuthStore } from '@/stores/authStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { ToastContext } from '@/components/ui/ToastContainer';
import AIProvidersApiService, { type ModelType } from '@/services/aiProvidersApi';
import ProjectConfigApiService from '@/services/projectConfigApi';
import ProviderCard from './ProviderCard';
import ProviderConfigModal from './ProviderConfigModal';
import { connectionDraft } from '@/utils/providerSetup';
import { changedModelUsage } from '@/utils/modelUsage';
import MediaModelTestModal from './MediaModelTestModal';
import type { MediaProbeTarget } from '@/types/mediaProbe';

interface ModelOption {
  value: string;
  label: string;
}

const MODEL_TYPES: ModelType[] = ['chat', 'embedding', 'asr', 'ocr', 'vlm'];

const createModelState = <T,>(valueFactory: () => T): Record<ModelType, T> => ({
  chat: valueFactory(),
  embedding: valueFactory(),
  asr: valueFactory(),
  ocr: valueFactory(),
  vlm: valueFactory(),
});

const selectedModelValue = (
  providerId: string | null,
  model: string | null,
): string => providerId && model ? `${providerId}:${model}` : '';

const getErrorMessage = (error: unknown): string | undefined =>
  error instanceof Error ? error.message : undefined;

const ModelProvidersSettings: React.FC = () => {
  const { t } = useTranslation();
  const toast = useContext(ToastContext);
  const { providers, isLoading, loadProviders, removeModelFromProvider } = useProvidersStore();
  const projectId = useAuthStore(s => s.user?.project_id);
  const { setDefaultLlmModel, setDefaultEmbeddingModel } = useAppSettingsStore();

  const [showConfigModal, setShowConfigModal] = useState(false);
  const [editingProvider, setEditingProvider] = useState<ModelProviderConfig | null>(null);
  const [editingModelId, setEditingModelId] = useState<string | undefined>();
  const [deletingModel, setDeletingModel] = useState<{ providerId: string; modelId: string } | null>(null);
  const [isDeletingModel, setIsDeletingModel] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [mediaTarget, setMediaTarget] = useState<MediaProbeTarget | null>(null);

  // Global default models UI state
  const [modelOptions, setModelOptions] = useState<Record<ModelType, ModelOption[]>>(
    () => createModelState(() => [])
  );
  const [modelSelections, setModelSelections] = useState<Record<ModelType, string>>(
    () => createModelState(() => '')
  );
  const [modelLoading, setModelLoading] = useState<Record<ModelType, boolean>>(
    () => createModelState(() => false)
  );
  const [isSavingDefaults, setIsSavingDefaults] = useState(false);
  const [configReady, setConfigReady] = useState(false);
  const [defaultsSynced, setDefaultsSynced] = useState(false);
  const [pendingUsage, setPendingUsage] = useState<{ type: ModelType; value: string } | null>(null);

  const isInitialized = React.useRef<string | null>(null);
  const activeProjectId = React.useRef(projectId);
  const fetchingModelTypes = React.useRef(new Set<string>());

  useEffect(() => {
    activeProjectId.current = projectId;
    isInitialized.current = null;
    setConfigReady(false);
    setDefaultsSynced(false);
    setShowConfigModal(false);
    setEditingProvider(null);
    setEditingModelId(undefined);
    setMediaTarget(null);
    setDeletingModel(null);
    setModelOptions(createModelState(() => []));
    setModelSelections(createModelState(() => ''));
    setPendingUsage(null);
    setModelLoading(createModelState(() => false));
  }, [projectId]);

  const ensureFetchModelOptions = useCallback(async (modelType: ModelType) => {
    if (!projectId) return;
    const requestKey = `${projectId}:${modelType}`;
    if (fetchingModelTypes.current.has(requestKey)) return;
    fetchingModelTypes.current.add(requestKey);
    setModelLoading(current => ({ ...current, [modelType]: true }));
    try {
      const svc = new AIProvidersApiService();
      const res = await svc.listProjectModels({ model_type: modelType, is_active: true });
      if (activeProjectId.current !== projectId) return;
      const options = (res.data || []).map(model => ({
        value: `${model.provider_id}:${model.model_id}`,
        label: `${model.model_name} · ${model.provider_name}`,
      }));
      setModelOptions(current => ({ ...current, [modelType]: options }));
    } catch (error: unknown) {
      if (activeProjectId.current === projectId) {
        toast?.showToast('error', t('common.loadFailed', '加载失败'), getErrorMessage(error));
      }
    } finally {
      fetchingModelTypes.current.delete(requestKey);
      if (activeProjectId.current === projectId) setModelLoading(current => ({ ...current, [modelType]: false }));
    }
  }, [projectId, toast, t]);

  useEffect(() => {
    loadProviders().catch(() => {});
  }, [loadProviders]);

  // Load project-level AI defaults
  useEffect(() => {
    if (!projectId || isInitialized.current === projectId) return;
    let cancelled = false;
    const fetchConfig = async () => {
      try {
        // Fetch options first so they are available when config is set
        await Promise.all(MODEL_TYPES.map(ensureFetchModelOptions));
        if (cancelled) return;

        const svc = new ProjectConfigApiService();
        const conf = await svc.getAIConfig(projectId);
        if (cancelled) return;
        const selections: Record<ModelType, string> = {
          chat: selectedModelValue(conf.default_chat_provider_id, conf.default_chat_model),
          embedding: selectedModelValue(conf.default_embedding_provider_id, conf.default_embedding_model),
          asr: selectedModelValue(conf.default_asr_provider_id, conf.default_asr_model),
          ocr: selectedModelValue(conf.default_ocr_provider_id, conf.default_ocr_model),
          vlm: selectedModelValue(conf.default_vlm_provider_id, conf.default_vlm_model),
        };

        setModelSelections(selections);
        setDefaultsSynced(conf.sync_status === 'synced');

        setConfigReady(true);
        setDefaultLlmModel(selections.chat || null);
        setDefaultEmbeddingModel(selections.embedding || null);
        isInitialized.current = projectId;
      } catch (error: unknown) {
        if (!cancelled) toast?.showToast('error', t('common.loadFailed'), getErrorMessage(error));
      }
    };

    fetchConfig();
    return () => { cancelled = true; };
  }, [projectId, setDefaultLlmModel, setDefaultEmbeddingModel, toast, t, ensureFetchModelOptions]);

  const applyUsage = async (selections: Record<ModelType, string>) => {
    if (!projectId || !configReady) throw new Error(t('modelSetup.defaultsNotReady'));
    const targetProject = projectId;
    const conf = await new ProjectConfigApiService().upsertAIConfig(projectId, changedModelUsage(modelSelections, selections));
    if (activeProjectId.current !== targetProject) return;
    setModelSelections(selections);
    setDefaultsSynced(conf.sync_status === 'synced');
    if (conf.sync_status !== 'synced') throw new Error(t('modelSetup.syncPending'));
    setDefaultLlmModel(selections.chat || null);
    setDefaultEmbeddingModel(selections.embedding || null);
  };

  const confirmUsage = async () => {
    if (!pendingUsage || isSavingDefaults) return;
    setIsSavingDefaults(true);
    try {
      await applyUsage({ ...modelSelections, [pendingUsage.type]: pendingUsage.value });
      setPendingUsage(null);
      toast?.showToast('success', t('settings.models.toast.saved'));
    } catch (error: unknown) {
      toast?.showToast('error', t('common.saveFailed'), getErrorMessage(error));
    } finally { setIsSavingDefaults(false); }
  };

  const retrySync = async () => {
    if (!projectId || isSavingDefaults) return;
    const targetProject = projectId;
    setIsSavingDefaults(true);
    try {
      const conf = await new ProjectConfigApiService().syncAIConfig(targetProject);
      if (activeProjectId.current !== targetProject) return;
      setDefaultsSynced(conf.sync_status === 'synced');
      if (conf.sync_status !== 'synced') throw new Error(t('modelSetup.syncPending'));
      setDefaultLlmModel(modelSelections.chat || null);
      setDefaultEmbeddingModel(modelSelections.embedding || null);
      toast?.showToast('success', t('settings.models.toast.saved'));
    } catch (error: unknown) {
      if (activeProjectId.current === targetProject) toast?.showToast('error', t('common.saveFailed'), getErrorMessage(error));
    } finally { setIsSavingDefaults(false); }
  };

  const usageLabel = (type: ModelType, value: string) =>
    modelOptions[type].find(option => option.value === value)?.label ||
    (value ? value.slice(value.indexOf(':') + 1) : t('modelSetup.notConfigured'));
  const pendingMessage = pendingUsage ? t('modelSetup.switchMessage', {
    usage: t(`modelSetup.uses.${pendingUsage.type}`),
    from: usageLabel(pendingUsage.type, modelSelections[pendingUsage.type]),
    to: usageLabel(pendingUsage.type, pendingUsage.value),
  }) + (pendingUsage.type === 'embedding' && modelSelections.embedding ? ` ${  t('modelSetup.embeddingWarning')}` : '') : '';

  const handleDelete = async () => {
    if (!deletingModel || isDeletingModel) return;
    const target = deletingModel;
    setIsDeletingModel(true);
    try {
      await removeModelFromProvider(target.providerId, target.modelId);
      setDeletingModel(null);
      MODEL_TYPES.forEach(type => void ensureFetchModelOptions(type));
      toast?.showToast('success', t('modelSetup.modelDeleted'));
    } catch (error: unknown) {
      toast?.showToast('error', t('common.deleteFailed'), getErrorMessage(error));
    } finally { setIsDeletingModel(false); }
  };

  const handleTest = async (p: ModelProviderConfig, modelId: string, modelType: ModelType) => {
    if (modelType === 'asr' || modelType === 'ocr' || modelType === 'vlm') {
      setMediaTarget({ providerId: p.id, providerName: p.name, modelId, capability: modelType });
      return;
    }
    setTestingId(`${p.id  }:${  modelId}`);
    try {
      const svc = new AIProvidersApiService();
      const res = await svc.probeModel({ ...connectionDraft(p), model_id: modelId, model_type: modelType });
      if (res.success === true) {
        toast?.showToast('success', t('modelSetup.testSuccess'), res.message);
      } else {
        toast?.showToast('error', t('modelSetup.testFailed'), res.message);
      }
    } catch (error: unknown) {
      toast?.showToast('error', t('settings.providers.test.failed'), getErrorMessage(error));
    } finally {
      setTestingId(null);
    }
  };

  const sortedProviders = useMemo(() => 
    providers.slice().sort((a, b) => Number(b.enabled) - Number(a.enabled)), 
    [providers]
  );

  const refreshOptions = () => { MODEL_TYPES.forEach(type => void ensureFetchModelOptions(type)); };
  return (
    <div className="p-5 lg:p-8 space-y-7 max-w-6xl mx-auto">
      <header className="flex items-center justify-between gap-4 flex-wrap">
        <div className="min-w-0"><h2 className="text-2xl font-semibold text-gray-900 dark:text-gray-100">{t('modelSetup.title')}</h2>
          <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">{t('modelSetup.subtitle')}</p></div>
        <Button onClick={() => { setEditingProvider(null); setEditingModelId(undefined); setShowConfigModal(true); }} className="shrink-0 whitespace-nowrap rounded-lg">
          <FiPlus className="mr-2" />{t('modelSetup.add')}
        </Button>
      </header>
      <section className="space-y-4">
        {configReady && !defaultsSynced && Object.values(modelSelections).some(Boolean) && <div role="status" className="rounded-lg border border-amber-400/40 bg-amber-50 dark:bg-amber-900/20 p-4 text-sm text-amber-800 dark:text-amber-200 flex items-center gap-4 justify-between">
          <span>{t('modelSetup.syncPending')}</span><Button disabled={isSavingDefaults} onClick={retrySync}>{t('modelSetup.retrySync')}</Button>
        </div>}
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div><h3 className="font-semibold text-gray-900 dark:text-gray-100">{t('modelSetup.services')}</h3><p className="text-xs text-gray-500 dark:text-gray-400 mt-2">{t('modelSetup.inlineUsageHint')}</p></div>

        </div>
        {isLoading && <div className="flex gap-2 text-gray-500"><FiLoader className="animate-spin" />{t('common.loading')}</div>}
        {!isLoading && !sortedProviders.length && <div className="border border-dashed dark:border-gray-700 rounded-xl p-8 text-center text-gray-500"><FiCpu className="mx-auto mb-3 w-6 h-6" />{t('modelSetup.empty')}</div>}
        {sortedProviders.map(provider => <ProviderCard key={provider.id} provider={provider}
          onEdit={(item, modelId) => { setEditingProvider(item); setEditingModelId(modelId); setShowConfigModal(true); }}
          onDelete={(providerId, modelId) => setDeletingModel({ providerId, modelId })} onTest={handleTest} testingId={testingId}
          selections={modelSelections} defaultsSynced={defaultsSynced}
          usageOptions={modelOptions} usageDisabled={!configReady || isSavingDefaults || Object.values(modelLoading).some(Boolean)}
          onUsageChange={(type, value) => setPendingUsage({ type, value })} />)}
      </section>
      {mediaTarget && <MediaModelTestModal key={`${projectId}:${mediaTarget.providerId}:${mediaTarget.modelId}`} target={mediaTarget} onClose={() => setMediaTarget(null)} />}
      <ProviderConfigModal isOpen={showConfigModal} editingProvider={editingProvider} editingModelId={editingModelId} onSaved={refreshOptions}
        onClose={() => { setShowConfigModal(false); setEditingProvider(null); }} />
      <ConfirmDialog isOpen={!!deletingModel} title={t('modelSetup.deleteModelTitle')} message={t('modelSetup.deleteModelHint', { model: deletingModel?.modelId || '' })}
        isLoading={isDeletingModel} confirmText={t('common.delete')} confirmVariant="danger" onConfirm={handleDelete} onCancel={() => !isDeletingModel && setDeletingModel(null)} />
      <ConfirmDialog isOpen={!!pendingUsage} title={t('modelSetup.switchTitle')} message={pendingMessage}
        confirmText={t('modelSetup.switchConfirm')} isLoading={isSavingDefaults}
        onConfirm={confirmUsage} onCancel={() => !isSavingDefaults && setPendingUsage(null)} />
    </div>
  );
};
export default ModelProvidersSettings;
