import { useTranslation } from 'react-i18next';
import { KnowledgeBodyField } from './KnowledgeTextImport';

export type KnowledgeInputMode = 'paste' | 'upload';
interface Props {
  mode: KnowledgeInputMode; body: string; file: File | null; disabled?: boolean; bodyDisabled?: boolean;
  onMode: (mode: KnowledgeInputMode) => void; onBody: (body: string) => void; onFile: (file: File | null) => void;
}
export function KnowledgeMaterialInput({ mode, body, file, disabled, bodyDisabled, onMode, onBody, onFile }: Props) {
  const { t } = useTranslation();
  return <div className="min-w-0 space-y-3">
    <div role="tablist" aria-label={t('knowledgeOnboarding.inputMode')} className="flex flex-wrap gap-2">
      {(['paste', 'upload'] as const).map((value) => <button key={value} role="tab" type="button" aria-selected={mode === value} disabled={disabled} onClick={() => onMode(value)} className={`rounded-lg border px-4 py-2 text-sm ${mode === value ? 'border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-200' : 'border-gray-300 dark:border-gray-600'}`}>{t(`knowledgeOnboarding.mode.${value}`)}</button>)}
    </div>
    {mode === 'paste' ? <KnowledgeBodyField body={body} onChange={onBody} disabled={disabled || bodyDisabled} /> : <label className="block space-y-2 text-sm">
      <span>{t('knowledgeOnboarding.chooseMaterial')}</span>
      <input type="file" aria-label={t('knowledgeOnboarding.chooseMaterial')} disabled={disabled} accept=".pdf,.doc,.docx,.txt,.md,.csv,.xlsx,.xls,.ppt,.pptx,.html,.htm" onChange={(event) => onFile(event.target.files?.[0] || null)} className="block w-full min-w-0 rounded-lg border border-gray-300 p-3 dark:border-gray-600" />
      <span className="block break-words text-xs text-gray-500">{file ? file.name : t('knowledgeOnboarding.fileRequired')}</span>
    </label>}
  </div>;
}
