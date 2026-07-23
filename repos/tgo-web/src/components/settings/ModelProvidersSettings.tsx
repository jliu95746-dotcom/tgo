import React, { useMemo, useState, useContext, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { FiCpu, FiLoader } from 'react-icons/fi';
import { Sparkles, Settings, Zap } from 'lucide-react';
import Button from '@/components/ui/Button';
import ConfirmDialog from '@/components/ui/ConfirmDialog';
import Select from '@/components/ui/Select';
import SectionCard from '@/components/ui/SectionCard';
import { useProvidersStore, type ModelProviderConfig } from '@/stores/providersStore';
import { useAuthStore } from '@/stores/authStore';
import { useAppSettingsStore } from '@/stores/appSettingsStore';
import { ToastContext } from '@/components/ui/ToastContainer';
import AIProvidersApiService, { type ModelType } from '@/services/aiProvidersApi';
import ProjectConfigApiService from '@/services/projectConfigApi';
import ModelStoreModal from '@/components/ai/ModelStoreModal';
import { ToolToastProvider } from '@/components/ai/ToolToastProvider';
import ProviderCard from './ProviderCard';
import ProviderConfigModal from './ProviderConfigModal';
import AddModelModal from './AddModelModal';

interface ModelOption {
  value: string;
  label: string;
}

interface DefaultModelField {
  type: ModelType;
  labelKey: string;
  fallbackLabel: string;
}

const MODEL_TYPES: ModelType[] = ['chat', 'embedding', 'asr', 'ocr', 'vlm'];
const DEFAULT_MODEL_FIELDS: DefaultModelField[] = [
  { type: 'chat', labelKey: 'settings.models.defaults.llmLabel', fallbackLabel: '默认 LLM' },
  { type: 'embedding', labelKey: 'settings.models.defaults.embeddingLabel', fallbackLabel: '默认嵌入模型' },
  { type: 'asr', labelKey: 'settings.models.defaults.asrLabel', fallbackLabel: '默认语音识别模型' },
  { type: 'ocr', labelKey: 'settings.models.defaults.ocrLabel', fallbackLabel: '默认 OCR 模型' },
  { type: 'vlm', labelKey: 'settings.models.defaults.vlmLabel', fallbackLabel: '默认图片理解模型' },
];

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
  const { providers, isLoading, loadProviders, removeProvider } = useProvidersStore();
  const projectId = useAuthStore(s => s.user?.project_id);
  const { setDefaultLlmModel, setDefaultEmbeddingModel } = useAppSettingsStore();

  const [showModelStore, setShowModelStore] = useState(false);
  const [showConfigModal, setShowConfigModal] = useState(false);
  const [showAddModelModal, setShowAddModelModal] = useState(false);
  const [editingProvider, setEditingProvider] = useState<ModelProviderConfig | null>(null);
  const [addingModelToProvider, setAddingModelToProvider] = useState<ModelProviderConfig | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);

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

  // Track initialization to avoid infinite loops
  const isInitialized = React.useRef(false);

  const ensureFetchModelOptions = async (modelType: ModelType) => {
    if (modelLoading[modelType]) return;
    setModelLoading(current => ({ ...current, [modelType]: true }));
    try {
      const svc = new AIProvidersApiService();
      const res = await svc.listProjectModels({ model_type: modelType, is_active: true });
      const options = (res.data || []).map(model => ({
        value: `${model.provider_id}:${model.model_id}`,
        label: `${model.model_name} · ${model.provider_name}`,
      }));
      setModelOptions(current => ({ ...current, [modelType]: options }));
    } catch (error: unknown) {
      toast?.showToast('error', t('common.loadFailed', '加载失败'), getErrorMessage(error));
    } finally {
      setModelLoading(current => ({ ...current, [modelType]: false }));
    }
  };

  useEffect(() => {
    loadProviders().catch(() => {});
  }, [loadProviders]);

  // Load project-level AI defaults
  useEffect(() => {
    if (!projectId || isInitialized.current) return;
    
    const fetchConfig = async () => {
      try {
        // Fetch options first so they are available when config is set
        await Promise.all(MODEL_TYPES.map(ensureFetchModelOptions));

        const svc = new ProjectConfigApiService();
        const conf = await svc.getAIConfig(projectId);
        const selections: Record<ModelType, string> = {
          chat: selectedModelValue(conf.default_chat_provider_id, conf.default_chat_model),
          embedding: selectedModelValue(conf.default_embedding_provider_id, conf.default_embedding_model),
          asr: selectedModelValue(conf.default_asr_provider_id, conf.default_asr_model),
          ocr: selectedModelValue(conf.default_ocr_provider_id, conf.default_ocr_model),
          vlm: selectedModelValue(conf.default_vlm_provider_id, conf.default_vlm_model),
        };

        setModelSelections(selections);
        setDefaultLlmModel(selections.chat || null);
        setDefaultEmbeddingModel(selections.embedding || null);
        isInitialized.current = true;
      } catch (error: unknown) {
        toast?.showToast('error', t('common.loadFailed'), getErrorMessage(error));
      }
    };

    fetchConfig();
  }, [projectId, setDefaultLlmModel, setDefaultEmbeddingModel, toast, t]); // Kept dependencies but added Ref guard

  const onSaveDefaults = async () => {
    if (!projectId) return;
    setIsSavingDefaults(true);
    try {
      const parse = (v: string) => {
      const i = v.indexOf(':');
        if (i <= 0) return { providerId: null, model: null };
        return { providerId: v.slice(0, i), model: v.slice(i + 1) };
      };
      const chat = parse(modelSelections.chat);
      const embedding = parse(modelSelections.embedding);
      const asr = parse(modelSelections.asr);
      const ocr = parse(modelSelections.ocr);
      const vlm = parse(modelSelections.vlm);
      const svc = new ProjectConfigApiService();
      await svc.upsertAIConfig(projectId, {
        default_chat_provider_id: chat.providerId,
        default_chat_model: chat.model,
        default_embedding_provider_id: embedding.providerId,
        default_embedding_model: embedding.model,
        default_asr_provider_id: asr.providerId,
        default_asr_model: asr.model,
        default_ocr_provider_id: ocr.providerId,
        default_ocr_model: ocr.model,
        default_vlm_provider_id: vlm.providerId,
        default_vlm_model: vlm.model,
      });
      setDefaultLlmModel(modelSelections.chat || null);
      setDefaultEmbeddingModel(modelSelections.embedding || null);
      toast?.showToast('success', t('settings.models.toast.saved'));
    } catch (error: unknown) {
      toast?.showToast('error', t('common.saveFailed'), getErrorMessage(error));
    } finally {
      setIsSavingDefaults(false);
    }
  };

  const handleDelete = async (id: string) => {
    setDeletingId(null);
    try {
      await removeProvider(id);
      toast?.showToast('success', t('settings.providers.toast.deleted'));
    } catch (error: unknown) {
      toast?.showToast('error', t('common.deleteFailed'), getErrorMessage(error));
    }
  };

  const handleTest = async (p: ModelProviderConfig) => {
    setTestingId(p.id);
    try {
      const svc = new AIProvidersApiService();
      const res = await svc.testProvider(p.id);
      if (res.ok ?? res.success ?? true) {
        toast?.showToast('success', t('settings.providers.test.ok'));
      } else {
        toast?.showToast('error', t('settings.providers.test.failed'));
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

  const renderDefaultModelField = (field: DefaultModelField) => (
    <div key={field.type} className="space-y-2">
      <label className="text-[10px] font-black text-gray-400 dark:text-gray-500 uppercase tracking-widest px-1">
        {t(field.labelKey, field.fallbackLabel)}
      </label>
      <Select
        value={modelSelections[field.type]}
        onChange={value => setModelSelections(current => ({
          ...current,
          [field.type]: value,
        }))}
        onOpen={() => ensureFetchModelOptions(field.type)}
        isLoading={modelLoading[field.type]}
        options={[
          { value: '', label: t('settings.models.defaults.none', '未设置') },
          ...modelOptions[field.type],
        ]}
        className="w-full"
      />
    </div>
  );

    return (
    <ToolToastProvider>
      <div className="p-10 space-y-12 max-w-[1600px] mx-auto">
        {/* Header Section */}
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-6">
          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 rounded-2xl bg-blue-600 flex items-center justify-center text-white shadow-xl shadow-blue-200 dark:shadow-none">
                <FiCpu className="w-6 h-6" />
              </div>
              <h2 className="text-3xl font-black text-gray-900 dark:text-gray-100 tracking-tight">
                {t('settings.providers.title', '模型提供商')}
              </h2>
            </div>
            <p className="text-lg text-gray-500 dark:text-gray-400 font-medium max-w-2xl leading-relaxed">
              {t('settings.providers.subtitle', '集中管理各大 AI 提供商的访问配置：密钥、安全代理、模型清单与默认模型。')}
            </p>
          </div>

          <div className="flex items-center gap-4">
            {isLoading && <FiLoader className="animate-spin text-blue-600" />}
            <Button 
              variant="secondary" 
              size="lg" 
              onClick={() => { setEditingProvider(null); setShowConfigModal(true); }}
              className="rounded-2xl font-black px-8 py-4 bg-white dark:bg-gray-800 border-2 border-gray-100 dark:border-gray-700 hover:border-blue-500 transition-all active:scale-95"
            >
              <Settings className="mr-2 w-5 h-5" />
              {t('settings.providers.addCustom', '自定义配置')}
            </Button>
            <Button 
              variant="primary" 
              size="lg" 
              onClick={() => setShowModelStore(true)}
              className="rounded-2xl font-black px-8 py-4 shadow-xl shadow-blue-200 dark:shadow-none transition-all active:scale-95"
            >
              <Sparkles className="mr-2 w-5 h-5" />
              {t('settings.providers.fromStore', '从商店获取模型')}
          </Button>
        </div>
      </div>

      {/* Global Default Models Card */}
        <SectionCard className="border-blue-100 dark:border-blue-900/30 bg-gradient-to-br from-blue-50/50 to-transparent dark:from-blue-900/5">
          <div className="flex flex-col md:flex-row gap-8 items-start md:items-end">
            <div className="flex-1 space-y-6 w-full">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-lg bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center text-blue-600">
                  <Zap className="w-4 h-4" />
        </div>
          <div>
                  <h3 className="text-sm font-black text-gray-900 dark:text-gray-100 uppercase tracking-wider">
                    {t('settings.models.defaults.title', '默认模型配置')}
                  </h3>
                  <p className="text-xs text-gray-500 dark:text-gray-400 font-bold">
                    {t('settings.models.defaults.description', '选择全局默认模型，Agent 可单独覆盖')}
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {DEFAULT_MODEL_FIELDS.slice(0, 2).map(renderDefaultModelField)}
              </div>

              <div className="pt-6 border-t border-blue-100 dark:border-blue-900/30 space-y-4">
                <div>
                  <h4 className="text-sm font-black text-gray-900 dark:text-gray-100">
                    {t('settings.models.defaults.multimodalTitle', '多模态理解')}
                  </h4>
                  <p className="text-xs text-gray-500 dark:text-gray-400 font-medium mt-1">
                    {t('settings.models.defaults.multimodalDescription', '为语音识别、图片文字提取和图片理解选择全局默认模型。')}
                  </p>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  {DEFAULT_MODEL_FIELDS.slice(2).map(renderDefaultModelField)}
                </div>
              </div>
            </div>
            <div className="w-full md:w-auto">
              <Button 
                variant="primary" 
                size="md" 
                onClick={onSaveDefaults} 
                disabled={isSavingDefaults}
                className="w-full md:w-auto rounded-xl font-black px-8 shadow-lg shadow-blue-200 dark:shadow-none"
              >
                {isSavingDefaults ? <FiLoader className="animate-spin mr-2" /> : null}
                {t('common.save', '保存')}
              </Button>
            </div>
        </div>
        </SectionCard>

        {/* Providers Grid */}
        <div className="grid grid-cols-1 xl:grid-cols-2 2xl:grid-cols-3 gap-8">
          {sortedProviders.length === 0 && !isLoading && (
            <div className="col-span-full py-20 bg-gray-50 dark:bg-gray-900/50 rounded-[3rem] border-4 border-dashed border-gray-100 dark:border-gray-800 flex flex-col items-center justify-center text-center space-y-6">
              <div className="w-24 h-24 rounded-[2rem] bg-white dark:bg-gray-800 flex items-center justify-center text-gray-200 dark:text-gray-700 shadow-sm">
                <FiCpu className="w-12 h-12" />
              </div>
              <div className="space-y-2">
                <h3 className="text-xl font-black text-gray-900 dark:text-gray-100">
                  {t('settings.providers.emptyTitle', '开启您的 AI 之旅')}
                </h3>
                <p className="text-gray-500 dark:text-gray-400 font-medium">
                  {t('settings.providers.empty', '尚未配置任何模型提供商，点击右上角"从商店获取"或"自定义配置"开始。')}
                </p>
            </div>
              <Button 
                variant="primary" 
                size="lg" 
                onClick={() => setShowModelStore(true)}
                className="rounded-2xl font-black px-10 py-4 shadow-xl shadow-blue-200 dark:shadow-none"
              >
                {t('settings.providers.fromStore', '从商店获取模型')}
              </Button>
          </div>
        )}

        {sortedProviders.map((p) => (
            <ProviderCard
              key={p.id}
              provider={p}
              onEdit={(prov) => { setEditingProvider(prov); setShowConfigModal(true); }}
              onDelete={(id) => setDeletingId(id)}
              onAddModel={(prov) => { setAddingModelToProvider(prov); setShowAddModelModal(true); }}
              onTest={handleTest}
              isTesting={testingId === p.id}
            />
        ))}
      </div>

        {/* Modals */}
        <ProviderConfigModal
          isOpen={showConfigModal}
          onClose={() => { setShowConfigModal(false); setEditingProvider(null); }}
          editingProvider={editingProvider}
        />

        <AddModelModal
          isOpen={showAddModelModal}
          onClose={() => { setShowAddModelModal(false); setAddingModelToProvider(null); }}
          provider={addingModelToProvider}
        />

      <ConfirmDialog
          isOpen={!!deletingId}
        title={t('settings.providers.confirmDeleteTitle', '删除提供商')}
        message={t('settings.providers.confirmDeleteMsg', '确定要删除该提供商配置吗？此操作不可撤销。')}
        confirmText={t('common.delete', '删除')!}
        cancelText={t('common.cancel', '取消')!}
        confirmVariant="danger"
          onConfirm={() => deletingId && handleDelete(deletingId)}
          onCancel={() => setDeletingId(null)}
        />

        <ModelStoreModal 
          isOpen={showModelStore} 
          onClose={() => setShowModelStore(false)} 
      />
    </div>
    </ToolToastProvider>
  );
};

export default ModelProvidersSettings;
