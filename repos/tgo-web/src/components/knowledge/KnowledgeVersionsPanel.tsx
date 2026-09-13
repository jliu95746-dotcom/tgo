import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores/authStore';
import { KnowledgeBaseApiService } from '@/services/knowledgeBaseApi';
import { knowledgeVersionsApi as api, type SourceKind, type VersionAction, type VersionContent, type VersionHistory } from '@/services/knowledgeVersionsApi';

interface Source { id: string; name: string; content?: VersionContent }
interface Props { collectionId: string; kind: SourceKind; refreshKey?: number }
const control = 'rounded border border-gray-300 px-3 py-2 dark:border-gray-600 dark:bg-gray-900 disabled:opacity-40';

/** Daily updates are separated from advanced governance settings. */
export function KnowledgeVersionsPanel({ collectionId, kind, refreshKey = 0 }: Props) {
  const { t } = useTranslation();
  const admin = useAuthStore(state => state.user?.role === 'admin');
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<Source[]>([]);
  const [selected, setSelected] = useState('');
  const [history, setHistory] = useState<VersionHistory | null>(null);
  const [content, setContent] = useState<VersionContent>({});
  const [replacement, setReplacement] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [retention, setRetention] = useState(10);
  const [batchResults, setBatchResults] = useState<Record<string, string>>({});
  const historyRequest = useRef(0);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  const loadSources = useCallback(async () => {
    const items: Source[] = [];
    let offset = 0;
    let more = true;
    while (more) {
      if (kind === 'qa') {
        const response = await KnowledgeBaseApiService.getQAPairs(collectionId, { offset, limit: 100 });
        items.push(...response.data.map(pair => ({ id: pair.id, name: pair.question, content: {
          question: pair.question, answer: pair.answer, category: pair.category, tags: pair.tags, priority: pair.priority,
        } })));
        offset += response.data.length;
        more = response.data.length > 0 && offset < response.total;
      } else if (kind === 'website') {
        const response = await KnowledgeBaseApiService.getWebsitePages(collectionId, { offset, limit: 100 });
        items.push(...response.data.map(page => ({ id: page.id, name: page.title || page.url })));
        offset += response.data.length;
        more = response.data.length > 0 && response.pagination.has_next;
      } else {
        const response = await KnowledgeBaseApiService.getFiles({ collection_id: collectionId, offset, limit: 100 });
        items.push(...response.data.map(file => ({ id: file.id, name: file.original_filename })));
        offset += response.data.length;
        more = response.data.length > 0 && response.pagination.has_next;
      }
    }
    setSources(items);
    return items;
  }, [collectionId, kind]);

  const loadHistory = useCallback(async (id: string) => {
    const request = ++historyRequest.current;
    const result = await api.history(kind, id);
    if (selectedRef.current === id && request === historyRequest.current) setHistory(result);
    return result;
  }, [kind]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void loadSources().then(items => {
      if (!cancelled) setSelected(value => items.some(item => item.id === value) ? value : items[0]?.id || '');
    }).catch(reason => { if (!cancelled) setError(String(reason)); });
    return () => { cancelled = true; };
  }, [open, loadSources, refreshKey]);

  useEffect(() => {
    if (!open || !selected) return;
    let cancelled = false;
    setHistory(null); setReplacement(null); setError('');
    setContent(sources.find(source => source.id === selected)?.content || {});
    void loadHistory(selected).then(result => {
      if (cancelled) return;
      setRetention(result.retention);
      const draft = result.versions.find(version => ['ready', 'pending_review', 'failed'].includes(version.state));
      if (draft && kind === 'qa') setContent(draft.content);
    }).catch(reason => { if (!cancelled) setError(String(reason)); });
    return () => { cancelled = true; };
    // Source refresh must not overwrite an operator's unsaved edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, open, kind, refreshKey, loadHistory]);

  useEffect(() => {
    if (!open || !selected || !history?.versions.some(version => version.state === 'processing')) return;
    const timer = window.setInterval(() => {
      void loadHistory(selected).catch(reason => setError(String(reason)));
    }, 2500);
    return () => window.clearInterval(timer);
  }, [open, selected, history, loadHistory]);

  const run = async (operation: () => Promise<unknown>) => {
    setBusy(true); setError(''); setMessage('');
    try { await operation(); if (selected) await loadHistory(selected); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  };
  const update = (action: VersionAction, retryContent?: VersionContent) => run(async () => {
    let input = retryContent || content;
    if (kind === 'file' && replacement) {
      const uploaded = await api.upload(selected, replacement);
      input = { replacement_file_id: uploaded.id };
    }
    await api.change(kind, selected, input, action);
    setMessage(t('knowledge.versions.queued', '已提交处理，完成前继续使用原生效内容。'));
  });
  const cleanup = () => run(async () => {
    const preview = await api.cleanup(kind, selected, retention, false);
    if (!preview.removable_numbers.length) {
      await api.cleanup(kind, selected, retention, true);
      setMessage(t('knowledge.versions.noCleanup', '保留数量已保存，暂无需要清理的版本。'));
      return;
    }
    if (window.confirm(t('knowledge.versions.cleanupConfirm', '将永久清理以下历史版本的快照，无法再恢复：') + preview.removable_numbers.map(n => `V${n}`).join('、'))) {
      const result = await api.cleanup(kind, selected, retention, true, preview.removable_numbers);
      setMessage(result.file_cleanup_pending ? t('knowledge.versions.cleanupPartial', '历史快照已清理，部分旧文件被占用，尚未释放空间。') : t('knowledge.versions.cleaned', '已清理确认的历史快照及无引用文件，当前生效版本未改变。'));
    }
  });
  const batchCheck = () => run(async () => {
    const result: Record<string, string> = {};
    // Sequential dispatch keeps maintenance from flooding the crawler and model provider.
    for (const source of sources) {
      try {
        const revision = await api.change('website', source.id, {}, admin ? 'publish' : 'submit');
        result[source.id] = revision.state;
      } catch (reason) { result[source.id] = reason instanceof Error ? reason.message : String(reason); }
      setBatchResults({ ...result });
    }
  });
  const batchPublish = () => run(async () => {
    const candidates: { source: Source; revision: string; number: number }[] = [];
    for (const source of sources) {
      const current = await api.history(kind, source.id);
      const revision = current.versions.find(version => ['ready', 'pending_review'].includes(version.state));
      if (revision) candidates.push({ source, revision: revision.id, number: revision.number });
    }
    if (!candidates.length) {
      setMessage(t('knowledge.versions.nothingPending', '没有待生效的更新。'));
      return;
    }
    const listing = candidates.map(item => `${item.source.name} · V${item.number}`).join('\n');
    if (!window.confirm(t('knowledge.versions.batchConfirm', '确认将以下更新全部生效？\n') + listing)) return;
    const result: Record<string, string> = {};
    for (const item of candidates) {
      try { result[item.source.id] = (await api.transition(item.revision, 'publish')).state; }
      catch (reason) { result[item.source.id] = reason instanceof Error ? reason.message : String(reason); }
      setBatchResults({ ...result });
    }
  });
  useEffect(() => {
    if (!open || !Object.values(batchResults).includes('processing')) return;
    const timer = window.setTimeout(async () => {
      const next = { ...batchResults };
      for (const [id, status] of Object.entries(batchResults)) {
        if (status !== 'processing') continue;
        try { const result = await api.history(kind, id); next[id] = result.versions[0]?.state || 'unchanged'; }
        catch (reason) { next[id] = String(reason); }
      }
      setBatchResults(next);
    }, 4000);
    return () => window.clearTimeout(timer);
  }, [batchResults, open, kind]);

  const stateLabel = (state: string) => t(`knowledge.versions.states.${state}`, ({
    processing: '处理中', ready: '草稿', pending_review: '待审核', published: '已生效', retired: '历史版本',
    failed: '处理失败', unchanged: '无内容变化',
  } as Record<string, string>)[state] || state);
  const processing = history?.versions.some(version => version.state === 'processing');
  return <section className="my-4 rounded-lg border border-gray-200 bg-white text-gray-900 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100">
    <button type="button" className="w-full px-5 py-3 text-left font-medium" aria-expanded={open} onClick={() => setOpen(!open)}>
      {t('knowledge.versions.title', '更新与历史版本')} {open ? '−' : '+'}
    </button>
    {open && <div className="space-y-3 border-t border-gray-200 p-5 dark:border-gray-700">
      <p className="text-sm text-gray-500">{t('knowledge.versions.hint', '修改成功生效后，客服才会使用新内容；失败不影响原内容。无需重新绑定 AI 员工。')}</p>
      <div className="flex flex-wrap gap-2">
        <select aria-label={t('knowledge.versions.select', '选择要更新的资料')} value={selected} disabled={busy} onChange={event => setSelected(event.target.value)} className={`${control} min-w-0 flex-1`}>
          {!sources.length && <option value="">{t('knowledge.versions.empty', '暂无资料')}</option>}
          {sources.map(source => <option key={source.id} value={source.id}>{source.name}</option>)}
        </select>
        <button type="button" className={control} disabled={busy} onClick={() => void run(async () => { await loadSources(); })}>{t('common.refresh', '刷新')}</button>
        {kind === 'website' && <button type="button" className={control} disabled={busy || !sources.length} onClick={() => void batchCheck()}>{t('knowledge.versions.checkAll', '检查全部网站页面更新')}</button>}
        {admin && <button type="button" className={control} disabled={busy || !sources.length} onClick={() => void batchPublish()}>{t('knowledge.versions.batchPublish', '批量生效待更新内容')}</button>}
      </div>
      {selected && <>
        {kind === 'qa' && <div className="space-y-2">
          <input aria-label={t('knowledge.versions.question', '问题')} className={`${control} w-full`} value={content.question || ''} onChange={event => setContent({ ...content, question: event.target.value })} />
          <textarea aria-label={t('knowledge.versions.answer', '答案')} className={`${control} min-h-32 w-full resize-y`} value={content.answer || ''} onChange={event => setContent({ ...content, answer: event.target.value })} />
        </div>}
        {kind === 'file' && <label className="block text-sm">{t('knowledge.versions.replace', '选择替换文件（保留原资料和历史版本）')}
          <input type="file" className="mt-2 block w-full" disabled={busy || processing} onChange={event => setReplacement(event.target.files?.[0] || null)} />
        </label>}
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={`${control} bg-blue-600 text-white`} disabled={busy || processing || (kind === 'file' && !replacement)} onClick={() => void update(admin ? 'publish' : 'submit')}>
            {admin ? t('knowledge.versions.update', '更新并生效') : t('knowledge.versions.submit', '提交更新审核')}
          </button>
          <button type="button" className={control} disabled={busy || processing || (kind === 'file' && !replacement)} onClick={() => void update('save')}>{t('knowledge.versions.save', '保存草稿')}</button>
          {admin && <button type="button" className={control} disabled={busy || history?.disabled || !history} onClick={() => {
            if (window.confirm(t('knowledge.versions.disableConfirm', '停用后，客服将不再引用这份资料。确定停用？'))) void run(() => api.disable(kind, selected));
          }}>{t('knowledge.versions.disable', '停用')}</button>}
          {admin && history?.disabled && <button type="button" className={control} disabled={busy} onClick={() => void run(() => api.enable(kind, selected))}>{t('knowledge.versions.enable', '重新启用')}</button>}
          <span className="text-sm text-gray-500">{history?.disabled ? t('knowledge.versions.disabled', '已停用') : history?.active_number ? `V${history.active_number}` : t('knowledge.versions.original', '尚未产生更新版本')}</span>
        </div>
      </>}
      {error && <p role="alert" className="text-sm text-red-500">{error}</p>}
      {message && <p role="status" className="text-sm text-blue-500">{message}</p>}
      {busy && <p role="status">{t('common.loading', '处理中…')}</p>}
      {Object.keys(batchResults).length > 0 && <div className="max-h-44 overflow-auto text-sm">
        {Object.entries(batchResults).map(([id, status]) => <div key={id}>{sources.find(source => source.id === id)?.name}: {stateLabel(status)}
          {!['processing', 'published', 'unchanged', 'pending_review', 'ready'].includes(status) && <button className="ml-2 text-blue-500" onClick={() => setSelected(id)}>{t('knowledge.versions.viewRetry', '查看并重试')}</button>}
        </div>)}
      </div>}
      {history?.versions.map(version => <details key={`${version.id}-${version.state}`} className="rounded border border-gray-200 p-3 dark:border-gray-700">
        <summary className="cursor-pointer text-sm">{version.state === 'unchanged' ? stateLabel(version.state) : `V${version.number} · ${stateLabel(version.state)}`} · {version.author} · {new Date(version.created_at).toLocaleString()}</summary>
        {version.error && <p className="my-2 text-sm text-red-500">{version.error}</p>}
        {version.published_by && <p className="my-2 text-xs text-gray-500">{t('knowledge.versions.reviewer', '生效操作人')}: {version.published_by} · {version.published_at && new Date(version.published_at).toLocaleString()}</p>}
        <pre className="my-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-sm">{version.preview || version.content.answer}</pre>
        <div className="flex flex-wrap gap-2">
          {admin && ['ready', 'pending_review'].includes(version.state) && <button className={control} disabled={busy} onClick={() => void run(() => api.transition(version.id, 'publish'))}>{t('knowledge.versions.publish', '确认生效')}</button>}
          {version.state === 'ready' && !admin && <button className={control} disabled={busy} onClick={() => void run(() => api.transition(version.id, 'submit'))}>{t('knowledge.versions.submit', '提交更新审核')}</button>}
          {version.state === 'failed' && <button className={control} disabled={busy} onClick={() => void update(admin ? 'publish' : 'submit', version.content)}>{t('common.retry', '重试')}</button>}
          {admin && ['ready', 'pending_review', 'processing', 'failed'].includes(version.state) && <button className={control} disabled={busy} onClick={() => {
            if (window.confirm(t('knowledge.versions.discardConfirm', '放弃这次修改？原生效内容不会改变。'))) void run(() => api.transition(version.id, 'discard'));
          }}>{t('knowledge.versions.discard', '放弃修改')}</button>}
          {admin && version.state === 'retired' && <button className={control} disabled={busy || processing} onClick={() => {
            if (window.confirm(t('knowledge.versions.restoreConfirm', '将此历史内容恢复为一个新版本并立即生效？'))) void run(() => api.transition(version.id, 'restore'));
          }}>{t('knowledge.versions.restore', '恢复此版本')}</button>}
        </div>
      </details>)}
      {admin && history && <details><summary className="cursor-pointer text-sm text-gray-500">{t('knowledge.versions.maintenance', '历史维护')}</summary>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
          <label>{t('knowledge.versions.keep', '保留最近生效版本数')} <input type="number" min={2} max={100} value={retention} className={`${control} w-20`} onChange={event => setRetention(Number(event.target.value))} /></label>
          <button className={control} disabled={busy || retention < 2 || retention > 100} onClick={() => void cleanup()}>{t('knowledge.versions.cleanup', '预览并清理')}</button>
        </div>
      </details>}
    </div>}
  </section>;
}
