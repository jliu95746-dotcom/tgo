import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  FilePlus2,
  Loader2,
  Pencil,
  RefreshCw,
  Send,
  X,
  XCircle,
} from 'lucide-react';

import { KnowledgeGovernanceApiService } from '@/services/knowledgeGovernanceApi';
import { useAuthStore } from '@/stores/authStore';
import type {
  KnowledgeChannel,
  KnowledgeDocumentType,
  KnowledgeFile,
  KnowledgeGovernanceDraftRequest,
  KnowledgeGovernanceRecord,
  KnowledgeReviewStatus,
  KnowledgeSourceOrigin,
} from '@/types';

interface KnowledgeGovernancePanelProps {
  collectionId: string;
  collectionName: string;
  documents: KnowledgeFile[];
}

interface GovernanceFormState {
  fileId: string;
  recordId: string | null;
  sourceName: string;
  documentType: KnowledgeDocumentType;
  productLine: string;
  channels: KnowledgeChannel[];
  effectiveAt: string;
  expiresAt: string;
  owner: string;
  documentVersion: string;
  allowAutomaticReply: boolean;
  sourceOrigin: KnowledgeSourceOrigin;
}

const CHANNELS: KnowledgeChannel[] = ['wecom_kf', 'web', 'app', 'phone', 'internal'];

const toLocalInput = (value?: string | null): string => {
  const date = value ? new Date(value) : new Date();
  const localTime = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return localTime.toISOString().slice(0, 16);
};

const statusClasses: Record<KnowledgeReviewStatus, string> = {
  draft: 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200',
  pending_review: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  approved: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300',
  rejected: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
  revoked: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
};

export const KnowledgeGovernancePanel: React.FC<KnowledgeGovernancePanelProps> = ({
  collectionId,
  collectionName,
  documents,
}) => {
  const { t } = useTranslation();
  const currentUser = useAuthStore((state) => state.user);
  const [records, setRecords] = useState<KnowledgeGovernanceRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [activeAction, setActiveAction] = useState<string | null>(null);
  const [form, setForm] = useState<GovernanceFormState | null>(null);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const loadRecords = useCallback(async (): Promise<void> => {
    setIsLoading(true);
    try {
      const response = await KnowledgeGovernanceApiService.list(collectionId);
      setRecords(response.data);
    } catch (error) {
      setMessage({
        type: 'error',
        text: error instanceof Error ? error.message : t('knowledge.governance.loadFailed'),
      });
    } finally {
      setIsLoading(false);
    }
  }, [collectionId, t]);

  useEffect(() => {
    void loadRecords();
  }, [loadRecords]);

  const governedFileIds = useMemo(
    () => new Set(records.flatMap((record) => (record.file_id ? [record.file_id] : []))),
    [records],
  );
  const ungovernedDocuments = useMemo(
    () => documents.filter((document) => !governedFileIds.has(document.id)),
    [documents, governedFileIds],
  );

  const openNewDraft = (document: KnowledgeFile): void => {
    setForm({
      fileId: document.id,
      recordId: null,
      sourceName: document.name,
      documentType: 'product',
      productLine: collectionName,
      channels: ['wecom_kf', 'web'],
      effectiveAt: toLocalInput(),
      expiresAt: '',
      owner: currentUser?.nickname || currentUser?.username || '',
      documentVersion: 'v1.0',
      allowAutomaticReply: false,
      sourceOrigin: 'internal',
    });
  };

  const openExistingDraft = (record: KnowledgeGovernanceRecord): void => {
    if (!record.file_id) return;
    setForm({
      fileId: record.file_id,
      recordId: record.id,
      sourceName: record.source_name,
      documentType: record.document_type,
      productLine: record.product_line,
      channels: record.channels,
      effectiveAt: toLocalInput(record.effective_at),
      expiresAt: record.expires_at ? toLocalInput(record.expires_at) : '',
      owner: record.owner,
      documentVersion: record.document_version,
      allowAutomaticReply: record.allow_automatic_reply,
      sourceOrigin: record.source_origin,
    });
  };

  const saveDraft = async (): Promise<void> => {
    if (!form || !form.productLine.trim() || !form.owner.trim() || !form.documentVersion.trim()) {
      setMessage({ type: 'error', text: t('knowledge.governance.requiredFields') });
      return;
    }
    if (form.channels.length === 0) {
      setMessage({ type: 'error', text: t('knowledge.governance.channelRequired') });
      return;
    }
    const request: KnowledgeGovernanceDraftRequest = {
      document_type: form.documentType,
      product_line: form.productLine.trim(),
      channels: form.channels,
      effective_at: new Date(form.effectiveAt).toISOString(),
      owner: form.owner.trim(),
      document_version: form.documentVersion.trim(),
      allow_automatic_reply: form.allowAutomaticReply,
      source_origin: form.sourceOrigin,
    };
    if (form.expiresAt) request.expires_at = new Date(form.expiresAt).toISOString();

    setActiveAction(form.fileId);
    try {
      await KnowledgeGovernanceApiService.saveDraft(form.fileId, request);
      setForm(null);
      setMessage({ type: 'success', text: t('knowledge.governance.saved') });
      await loadRecords();
    } catch (error) {
      setMessage({
        type: 'error',
        text: error instanceof Error ? error.message : t('knowledge.governance.saveFailed'),
      });
    } finally {
      setActiveAction(null);
    }
  };

  const runRecordAction = async (
    record: KnowledgeGovernanceRecord,
    action: 'submit' | 'approved' | 'rejected' | 'revoked',
  ): Promise<void> => {
    setActiveAction(record.id);
    try {
      if (action === 'submit') {
        await KnowledgeGovernanceApiService.submit(record.id);
      } else {
        await KnowledgeGovernanceApiService.review(record.id, { status: action });
      }
      setMessage({ type: 'success', text: t(`knowledge.governance.actionSuccess.${action}`) });
      await loadRecords();
    } catch (error) {
      setMessage({
        type: 'error',
        text: error instanceof Error ? error.message : t('knowledge.governance.actionFailed'),
      });
    } finally {
      setActiveAction(null);
    }
  };

  const safeBackfill = async (): Promise<void> => {
    const baseRequest = {
      collection_id: collectionId,
      document_type: 'product' as const,
      product_line: collectionName,
      channels: ['wecom_kf', 'web'] as KnowledgeChannel[],
      effective_at: new Date().toISOString(),
      owner: currentUser?.nickname || currentUser?.username || 'knowledge-admin',
      document_version: `legacy-${new Date().toISOString().slice(0, 10).replace(/-/g, '')}`,
      source_origin: 'internal' as const,
    };
    setActiveAction('backfill');
    try {
      const preview = await KnowledgeGovernanceApiService.backfill({ ...baseRequest, dry_run: true });
      if (preview.missing_count === 0) {
        setMessage({ type: 'success', text: t('knowledge.governance.noBackfillNeeded') });
        return;
      }
      const confirmed = window.confirm(
        t('knowledge.governance.backfillConfirm', { count: preview.missing_count }),
      );
      if (!confirmed) return;
      const result = await KnowledgeGovernanceApiService.backfill({ ...baseRequest, dry_run: false });
      setMessage({
        type: 'success',
        text: t('knowledge.governance.backfillSuccess', { count: result.created_count }),
      });
      await loadRecords();
    } catch (error) {
      setMessage({
        type: 'error',
        text: error instanceof Error ? error.message : t('knowledge.governance.backfillFailed'),
      });
    } finally {
      setActiveAction(null);
    }
  };

  const toggleChannel = (channel: KnowledgeChannel): void => {
    if (!form) return;
    const channels = form.channels.includes(channel)
      ? form.channels.filter((item) => item !== channel)
      : [...form.channels, channel];
    setForm({ ...form, channels });
  };

  return (
    <div className="p-6 space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            {t('knowledge.governance.title')}
          </h2>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {t('knowledge.governance.description')}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => void loadRecords()}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
          >
            <RefreshCw className="h-4 w-4" /> {t('knowledge.governance.refresh')}
          </button>
          <button
            onClick={() => void safeBackfill()}
            disabled={activeAction === 'backfill'}
            className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {activeAction === 'backfill' ? <Loader2 className="h-4 w-4 animate-spin" /> : <FilePlus2 className="h-4 w-4" />}
            {t('knowledge.governance.safeBackfill')}
          </button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-4">
        {(['approved', 'pending_review', 'draft'] as KnowledgeReviewStatus[]).map((status) => (
          <div key={status} className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-800">
            <div className="text-xs text-gray-500 dark:text-gray-400">{t(`knowledge.governance.status.${status}`)}</div>
            <div className="mt-1 text-2xl font-semibold text-gray-900 dark:text-gray-100">
              {records.filter((record) => record.review_status === status).length}
            </div>
          </div>
        ))}
        <div className="rounded-lg border border-orange-200 bg-orange-50 p-4 dark:border-orange-900/50 dark:bg-orange-950/20">
          <div className="text-xs text-orange-700 dark:text-orange-300">{t('knowledge.governance.ungoverned')}</div>
          <div className="mt-1 text-2xl font-semibold text-orange-800 dark:text-orange-200">{ungovernedDocuments.length}</div>
        </div>
      </div>

      {message && (
        <div className={`flex items-center gap-2 rounded-lg px-4 py-3 text-sm ${message.type === 'success' ? 'bg-green-50 text-green-800 dark:bg-green-950/30 dark:text-green-300' : 'bg-red-50 text-red-800 dark:bg-red-950/30 dark:text-red-300'}`}>
          {message.type === 'success' ? <CheckCircle2 className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
          {message.text}
        </div>
      )}

      {form && (
        <div className="rounded-xl border border-blue-200 bg-white p-5 shadow-sm dark:border-blue-900 dark:bg-gray-800">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h3 className="font-medium text-gray-900 dark:text-gray-100">{t('knowledge.governance.editTitle')}</h3>
              <p className="text-sm text-gray-500 dark:text-gray-400">{form.sourceName}</p>
            </div>
            <button onClick={() => setForm(null)} className="rounded p-1 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700"><X className="h-4 w-4" /></button>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            <label className="text-sm text-gray-700 dark:text-gray-300">{t('knowledge.governance.documentType')}
              <select value={form.documentType} onChange={(event) => setForm({ ...form, documentType: event.target.value as KnowledgeDocumentType })} className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-900">
                {(['product', 'after_sales', 'faq', 'sop'] as KnowledgeDocumentType[]).map((type) => <option key={type} value={type}>{t(`knowledge.governance.documentTypes.${type}`)}</option>)}
              </select>
            </label>
            <label className="text-sm text-gray-700 dark:text-gray-300">{t('knowledge.governance.productLine')}
              <input value={form.productLine} onChange={(event) => setForm({ ...form, productLine: event.target.value })} className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-900" />
            </label>
            <label className="text-sm text-gray-700 dark:text-gray-300">{t('knowledge.governance.version')}
              <input value={form.documentVersion} onChange={(event) => setForm({ ...form, documentVersion: event.target.value })} className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-900" />
            </label>
            <label className="text-sm text-gray-700 dark:text-gray-300">{t('knowledge.governance.owner')}
              <input value={form.owner} onChange={(event) => setForm({ ...form, owner: event.target.value })} className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-900" />
            </label>
            <label className="text-sm text-gray-700 dark:text-gray-300">{t('knowledge.governance.effectiveAt')}
              <input type="datetime-local" value={form.effectiveAt} onChange={(event) => setForm({ ...form, effectiveAt: event.target.value })} className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-900" />
            </label>
            <label className="text-sm text-gray-700 dark:text-gray-300">{t('knowledge.governance.expiresAt')}
              <input type="datetime-local" value={form.expiresAt} onChange={(event) => setForm({ ...form, expiresAt: event.target.value })} className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-900" />
            </label>
            <label className="text-sm text-gray-700 dark:text-gray-300">{t('knowledge.governance.sourceOrigin')}
              <select value={form.sourceOrigin} onChange={(event) => setForm({ ...form, sourceOrigin: event.target.value as KnowledgeSourceOrigin, allowAutomaticReply: event.target.value === 'customer' ? false : form.allowAutomaticReply })} className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-3 py-2 dark:border-gray-600 dark:bg-gray-900">
                {(['internal', 'customer', 'website'] as KnowledgeSourceOrigin[]).map((origin) => <option key={origin} value={origin}>{t(`knowledge.governance.sourceOrigins.${origin}`)}</option>)}
              </select>
            </label>
            <div className="md:col-span-2">
              <div className="text-sm text-gray-700 dark:text-gray-300">{t('knowledge.governance.channels')}</div>
              <div className="mt-2 flex flex-wrap gap-3">
                {CHANNELS.map((channel) => (
                  <label key={channel} className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                    <input type="checkbox" checked={form.channels.includes(channel)} onChange={() => toggleChannel(channel)} />
                    {t(`knowledge.governance.channelNames.${channel}`)}
                  </label>
                ))}
              </div>
            </div>
          </div>
          <label className="mt-4 flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300">
            <input type="checkbox" className="mt-1" checked={form.allowAutomaticReply} disabled={form.sourceOrigin === 'customer'} onChange={(event) => setForm({ ...form, allowAutomaticReply: event.target.checked })} />
            <span>{t('knowledge.governance.allowAutomaticReply')}<span className="block text-xs text-gray-500">{t('knowledge.governance.allowAutomaticReplyHint')}</span></span>
          </label>
          <div className="mt-5 flex justify-end gap-2">
            <button onClick={() => setForm(null)} className="rounded-lg border border-gray-300 px-4 py-2 text-sm dark:border-gray-600">{t('common.cancel')}</button>
            <button onClick={() => void saveDraft()} disabled={activeAction === form.fileId} className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">{t('knowledge.governance.saveDraft')}</button>
          </div>
        </div>
      )}

      {ungovernedDocuments.length > 0 && (
        <div className="rounded-lg border border-orange-200 bg-orange-50/60 p-4 dark:border-orange-900/50 dark:bg-orange-950/20">
          <h3 className="text-sm font-medium text-orange-900 dark:text-orange-200">{t('knowledge.governance.ungovernedTitle')}</h3>
          <div className="mt-3 flex flex-wrap gap-2">
            {ungovernedDocuments.map((document) => (
              <button key={document.id} onClick={() => openNewDraft(document)} className="rounded-lg border border-orange-300 bg-white px-3 py-2 text-sm text-orange-800 hover:bg-orange-100 dark:border-orange-800 dark:bg-gray-800 dark:text-orange-200">
                {document.name} · {t('knowledge.governance.configure')}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="overflow-hidden rounded-lg border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-800">
        {isLoading ? (
          <div className="flex justify-center py-12"><Loader2 className="h-6 w-6 animate-spin text-blue-600" /></div>
        ) : records.length === 0 ? (
          <div className="py-12 text-center text-sm text-gray-500">{t('knowledge.governance.empty')}</div>
        ) : (
          <div className="divide-y divide-gray-200 dark:divide-gray-700">
            {records.map((record) => (
              <div key={record.id} className="grid gap-3 p-4 lg:grid-cols-[minmax(220px,2fr)_1fr_1fr_auto] lg:items-center">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">{record.source_name}</div>
                  <div className="mt-1 text-xs text-gray-500">{record.product_line} · {record.document_version} · {record.owner}</div>
                </div>
                <div><span className={`rounded-full px-2 py-1 text-xs font-medium ${statusClasses[record.review_status]}`}>{t(`knowledge.governance.status.${record.review_status}`)}</span></div>
                <div className="text-xs text-gray-500 dark:text-gray-400">{record.allow_automatic_reply ? t('knowledge.governance.autoReplyEnabled') : t('knowledge.governance.autoReplyDisabled')}</div>
                <div className="flex flex-wrap justify-end gap-1">
                  {(['draft', 'rejected'] as KnowledgeReviewStatus[]).includes(record.review_status) && <button title={t('knowledge.governance.edit')} onClick={() => openExistingDraft(record)} className="rounded p-2 text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"><Pencil className="h-4 w-4" /></button>}
                  {(['draft', 'rejected'] as KnowledgeReviewStatus[]).includes(record.review_status) && <button title={t('knowledge.governance.submit')} onClick={() => void runRecordAction(record, 'submit')} className="rounded p-2 text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-950/30"><Send className="h-4 w-4" /></button>}
                  {currentUser?.role === 'admin' && record.review_status === 'pending_review' && <button title={t('knowledge.governance.approve')} onClick={() => void runRecordAction(record, 'approved')} className="rounded p-2 text-green-600 hover:bg-green-50 dark:hover:bg-green-950/30"><CheckCircle2 className="h-4 w-4" /></button>}
                  {currentUser?.role === 'admin' && record.review_status === 'pending_review' && <button title={t('knowledge.governance.reject')} onClick={() => void runRecordAction(record, 'rejected')} className="rounded p-2 text-red-600 hover:bg-red-50 dark:hover:bg-red-950/30"><XCircle className="h-4 w-4" /></button>}
                  {currentUser?.role === 'admin' && record.review_status === 'approved' && <button title={t('knowledge.governance.revoke')} onClick={() => void runRecordAction(record, 'revoked')} className="rounded p-2 text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-700"><Ban className="h-4 w-4" /></button>}
                  {activeAction === record.id && <Loader2 className="m-2 h-4 w-4 animate-spin" />}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
