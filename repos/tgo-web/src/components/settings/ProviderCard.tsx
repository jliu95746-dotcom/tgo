import { useTranslation } from 'react-i18next';
import { FiCpu, FiLoader } from 'react-icons/fi';
import type { ModelProviderConfig } from '@/stores/providersStore';
import type { ModelType } from '@/services/aiProvidersApi';
import { modelUsageTypes, type ModelUsageSelections } from '@/utils/modelUsage';
import ModelRowActions from './ModelRowActions';
interface Props {
  provider: ModelProviderConfig;
  onEdit: (provider: ModelProviderConfig, modelId?: string) => void;
  onDelete: (providerId: string, modelId: string) => void;
  onTest: (provider: ModelProviderConfig, modelId: string, type: ModelType) => void;
  testingId: string | null;
  selections: ModelUsageSelections;
  defaultsSynced: boolean;
  usageOptions: Record<ModelType, { value: string }[]>;
  usageDisabled: boolean;
  onUsageChange: (type: ModelType, value: string) => void;
}
export default function ProviderCard({ provider, onEdit, onDelete, onTest, testingId, selections, defaultsSynced, usageOptions, usageDisabled, onUsageChange }: Props) {
  const { t } = useTranslation();
  return <article className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800/50 p-5">
    <header className="flex gap-3 items-center mb-4"><div className="p-3 rounded-lg bg-blue-600 text-white"><FiCpu className="w-5 h-5" /></div>
      <div className="min-w-0"><div className="flex items-center gap-3 flex-wrap"><h3 className="font-semibold text-lg dark:text-gray-100">{provider.name}</h3><span className="text-xs text-green-700 dark:text-green-300">{t(provider.enabled ? 'common.enabled' : 'channelManagement.disabled')}</span></div>
        <p className="text-xs text-gray-500 dark:text-gray-400 break-all mt-1">{provider.apiBaseUrl}</p></div></header>
    <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
      <table className="w-full text-sm text-left"><thead className="bg-gray-50 dark:bg-gray-700/40 text-gray-500 dark:text-gray-400 text-xs"><tr><th className="p-3 font-medium">{t('modelSetup.modelId')}</th><th className="p-3 font-medium">{t('modelSetup.usage')}</th><th className="p-3 font-medium">{t('modelSetup.operation')}</th></tr></thead>
        <tbody className="divide-y dark:divide-gray-700">{(provider.models || []).map(id => {
          const type = provider.modelTypes?.[id] || 'chat';
          const value = `${provider.id}:${id}`;
          const assignedUses = modelUsageTypes.filter(usage => selections[usage] === value);
          const selectedUsage = assignedUses[0] || '';
          return <tr key={id}><td className="p-3 dark:text-gray-100 break-all">{id}</td><td className="p-3">
            <select aria-label={t('modelSetup.usageFor', { model: id })} value={selectedUsage}
              disabled={usageDisabled || !provider.enabled}
              onChange={event => {
                const usage = event.target.value as ModelType;
                if (usageDisabled || !provider.enabled || !modelUsageTypes.includes(usage) || selections[usage] === value || !usageOptions[usage].some(option => option.value === value)) return;
                onUsageChange(usage, value);
              }}
              className="w-full min-w-36 max-w-64 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2.5 py-2 text-xs text-gray-700 dark:text-gray-200 disabled:opacity-50">
              <option value="" disabled>{t('modelSetup.selectUsage')}</option>
              {modelUsageTypes.map(usage => {
                const assigned = selections[usage] === value;
                const eligible = usageOptions[usage].some(option => option.value === value);
                return <option key={usage} value={usage} disabled={!eligible && !assigned}>
                  {assigned && !defaultsSynced ? t('modelSetup.pendingDefault', { usage: t(`modelSetup.uses.${usage}`) }) : assigned || eligible ? t(`modelSetup.uses.${usage}`) : t('modelSetup.incompatibleUsage', { usage: t(`modelSetup.uses.${usage}`) })}
                </option>;
              })}
            </select>
            {assignedUses.slice(1).map(usage => <p key={usage} className="mt-1 text-xs text-gray-500">{defaultsSynced ? t(`modelSetup.uses.${usage}`) : t('modelSetup.pendingDefault', { usage: t(`modelSetup.uses.${usage}`) })}</p>)}
          </td>
            <td className="p-3"><div className="flex items-center gap-3 whitespace-nowrap"><button disabled={!!testingId || !provider.enabled} onClick={() => onTest(provider, id, type)} className="text-blue-600 dark:text-blue-400 text-xs whitespace-nowrap disabled:opacity-40">{testingId === `${provider.id}:${id}` ? <FiLoader className="animate-spin" /> : t('modelSetup.test')}</button>
              <ModelRowActions modelId={id} disabled={!!testingId} onEdit={() => onEdit(provider, id)} onDelete={() => onDelete(provider.id, id)} />
            </div></td></tr>;
        })}</tbody></table>
      {!provider.models?.length && <div className="p-4 text-gray-500 text-sm"><p>{t('modelSetup.modelEmpty')}</p><button onClick={() => onEdit(provider)} className="mt-2 text-blue-600 dark:text-blue-400">{t('modelSetup.addModels')}</button></div>}
    </div>
  </article>;
}
