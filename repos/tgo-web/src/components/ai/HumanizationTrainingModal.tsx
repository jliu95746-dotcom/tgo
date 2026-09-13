import { useEffect, useState } from 'react';
import { X, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import SkillsApiService, {
  type SkillSummary, type TrainingReview, type TrainingExample, type HumanizationTryResponse,
} from '@/services/skillsApi';

interface Props {
  skill: Pick<SkillSummary, 'name' | 'display_name'>;
  onClose: () => void;
  onPublished: () => Promise<void>;
}
const cases = [
  { question: '绿色的法棍包有吗？', facts: '已确认这款法棍包没有绿色。' },
  { question: '有红色小羊皮女包吗？', facts: '本次未找到同时满足红色和小羊皮的款式，不能确定整个商品目录中是否有。' },
  { question: '这款是小羊皮吗？', facts: '这款材质尚不能确认。' },
  { question: '多少钱？', facts: '这款售价 399 元。' },
  { question: '怎么退货？', facts: '请在订单详情选择申请售后，再选择退货退款，填写退货原因后提交申请。提交申请不代表审核通过。' },
];

export default function HumanizationTrainingModal({ skill, onClose, onPublished }: Props) {
  const { t } = useTranslation();
  const [review, setReview] = useState<TrainingReview | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [tab, setTab] = useState<'pending' | 'published' | 'try'>('pending');
  const [question, setQuestion] = useState(cases[0].question);
  const [facts, setFacts] = useState(cases[0].facts);
  const [history, setHistory] = useState('');
  const [results, setResults] = useState<Array<{ question: string; result?: HumanizationTryResponse; error?: string }>>([]);
  useEffect(() => {
    let cancelled = false;
    setBusy('load');
    SkillsApiService.reviewTraining(skill.name)
      .then((value) => { if (!cancelled) setReview(value); })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (!cancelled) setBusy(''); });
    return () => { cancelled = true; };
  }, [skill.name]);

  const reviewedSamples = review?.pending.filter((sample) => sample.change_kind !== 'review').slice(0, 100) ?? [];
  const candidate = review && reviewedSamples.length
    ? { snapshot_id: review.snapshot_id, samples: reviewedSamples } : undefined;
  const selectedCount = review?.pending.filter((s) => s.selected && s.change_kind === 'expression').length ?? 0;
  const field = 'w-full rounded-lg border border-gray-300 bg-white p-3 text-sm text-gray-900 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100';
  const button = 'rounded-lg border border-gray-300 px-4 py-2 text-sm disabled:opacity-40 dark:border-gray-700';
  const updateSample = (id: string, patch: Partial<TrainingExample>) => {
    setReview((current) => current && ({ ...current, pending: current.pending.map((s) => s.id === id ? { ...s, ...patch } : s) }));
    setResults([]);
  };
  async function analyze() {
    setBusy('analyze'); setError('');
    try { setReview(await SkillsApiService.previewTraining(skill.name)); setResults([]); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(''); }
  }
  async function publish() {
    if (!candidate) return;
    setBusy('publish'); setError('');
    try {
      await SkillsApiService.applyHumanizationTraining(skill.name, candidate);
      setReview(await SkillsApiService.reviewTraining(skill.name));
      setResults([]);
      await onPublished();
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(''); }
  }
  async function tryCases(group: boolean) {
    setBusy('try'); setError(''); setResults([]);
    for (const entry of group ? cases : [{ question, facts }]) {
      try {
        const result = await SkillsApiService.tryReply(skill.name, {
          customer_message: entry.question, factual_draft: entry.facts,
          recent_messages: history.trim() ? [{ role: 'customer', content: history.trim().slice(0, 2000) }] : [],
          candidate: review?.pending.length ? candidate : undefined,
        });
        setResults((current) => [...current, { question: entry.question, result }]);
      } catch (err) {
        setResults((current) => [...current, { question: entry.question, error: err instanceof Error ? err.message : String(err) }]);
      }
    }
    setBusy('');
  }

  function exampleCard(sample: TrainingExample, pending: boolean) {
    return <article key={sample.id} className="space-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
      <div className="flex flex-wrap items-center gap-3">
        {pending && <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={sample.selected} disabled={!!busy || sample.change_kind !== 'expression'}
            onChange={(e) => updateSample(sample.id, { selected: e.target.checked })} />
          {t('skills.training.include', '纳入本次更新')}
        </label>}
        <span className="rounded bg-violet-100 px-2 py-1 text-xs text-violet-800 dark:bg-violet-950 dark:text-violet-200">
          {t(`skills.training.kind.${sample.change_kind}`)}
        </span>
        <span className="text-xs text-gray-500">{sample.scene_tags.join(' / ')}</span>
      </div>
      <p className="text-sm"><span className="text-gray-500">{t('skills.training.customer', '客户：')}</span>{sample.customer_message}</p>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-lg bg-gray-50 p-3 text-sm dark:bg-gray-900">
          <p className="mb-2 text-xs text-gray-500">{t('skills.training.original', 'AI 原稿')}</p>
          <p className="whitespace-pre-wrap">{sample.ai_draft}</p>
        </div>
        <div className="rounded-lg bg-violet-50 p-3 text-sm dark:bg-violet-950/30">
          <p className="mb-2 text-xs text-violet-500">{t('skills.training.final', '人工发送')}</p>
          <p className="whitespace-pre-wrap">{sample.final_reply}</p>
        </div>
      </div>
      {sample.warnings.map((warning, index) => <p key={index} className="text-sm text-amber-600">{warning}</p>)}
      {pending && sample.change_kind === 'expression' ? <label className="block space-y-2 text-sm">
        <span>{t('skills.training.rulesEdit', '提炼规则（每行一条，可修改）')}</span>
        <textarea className={field} rows={3} value={sample.rules.join('\n')} disabled={!!busy}
          onChange={(e) => updateSample(sample.id, { rules: e.target.value.split('\n').filter(Boolean).slice(0, 8) })} />
      </label> : sample.rules.map((rule, index) => <p key={index} className="text-sm text-gray-500">{rule}</p>)}
    </article>;
  }

  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
    <section role="dialog" aria-modal="true" aria-labelledby="training-title"
      className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-white text-gray-900 shadow-xl dark:bg-gray-800 dark:text-gray-100">
      <header className="flex items-start justify-between gap-4 border-b border-gray-200 p-5 dark:border-gray-700">
        <div>
          <h2 id="training-title" className="text-lg font-semibold">{skill.display_name || skill.name} · {t('skills.training.title', '训练与试答')}</h2>
          <p className="mt-2 text-sm text-gray-500">{t('skills.training.hint', '预览和试答不会生效，点击“更新技能”后才用于客服回复。')}</p>
          {review && <p className="mt-2 text-xs text-violet-500">
            {t('skills.training.stats', '已发布 v{{version}} · {{published}} 条案例 · {{pending}} 条待处理', {
              version: review.published_version, published: review.published.length, pending: review.pending.length,
            })}
          </p>}
        </div>
        <button onClick={onClose} disabled={!!busy && busy !== 'load'} aria-label={t('common.close', '关闭')} className="rounded p-1 disabled:opacity-40"><X /></button>
      </header>
      <nav className="flex gap-2 border-b border-gray-200 px-5 py-3 dark:border-gray-700">
        {(['pending', 'published', 'try'] as const).map((value) => <button key={value} disabled={!!busy} onClick={() => setTab(value)}
          className={`${button}${tab === value ? ' bg-violet-600 text-white' : ''}`}>{t(`skills.training.tabs.${value}`)}</button>)}
      </nav>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
        {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950">{error}</p>}
        {review?.analysis_error && <p role="alert" className="text-sm text-amber-600">{review.analysis_error}</p>}
        {busy && <p role="status" className="flex items-center gap-2 text-sm text-violet-500"><Loader2 size={16} className="animate-spin" />{t(`skills.training.busy.${busy}`)}</p>}
        {tab === 'pending' && review && <>
          <div className="flex items-center gap-3">
            <button className={button} disabled={!!busy || !review.pending.length} onClick={analyze}>{t('skills.training.analyze', '分析修改并提炼规则')}</button>
            <p className="text-xs text-gray-500">{t('skills.training.factHint')}</p>
          </div>
          {!review.pending.length && <p className="py-8 text-center text-gray-500">{t('skills.training.empty', '暂无待更新修正。在辅助模式修改回复并发送后，会出现在这里。')}</p>}
          {review.pending.map((sample) => exampleCard(sample, true))}
        </>}
        {tab === 'published' && review && <>
          <h3 className="font-medium">{t('skills.training.publishedRules', '已发布表达规则')}</h3>
          {review.rules.map((rule, index) => <p key={index} className="text-sm">{rule}</p>)}
          {!review.published.length && <p className="text-sm text-gray-500">{t('skills.training.noPublished', '还没有发布训练案例，目前使用基础表达规则。')}</p>}
          {review.published.map((sample) => exampleCard(sample, false))}
        </>}
        {tab === 'try' && <div className="space-y-4">
          <p className="text-sm text-gray-500">{t('skills.training.tryHint', '使用同一份业务事实比较发布前后表达，不会向客户发送消息。')}</p>
          <label className="block space-y-2 text-sm"><span>{t('skills.training.question', '客户问题')}</span>
            <textarea className={field} rows={2} value={question} disabled={!!busy} onChange={(e) => setQuestion(e.target.value)} />
          </label>
          <label className="block space-y-2 text-sm"><span>{t('skills.training.facts', '本轮业务事实（请保留不确定性和限制条件）')}</span>
            <textarea className={field} rows={3} value={facts} disabled={!!busy} onChange={(e) => setFacts(e.target.value)} />
          </label>
          <label className="block space-y-2 text-sm"><span>{t('skills.training.history', '客户已提供的条件（可选）')}</span>
            <textarea className={field} rows={2} value={history} disabled={!!busy} onChange={(e) => setHistory(e.target.value)} />
          </label>
          <div className="flex gap-2">
            <button className={button} disabled={!!busy || !question.trim() || !facts.trim()} onClick={() => void tryCases(false)}>{t('skills.training.tryOne', '试答当前问题')}</button>
            <button className={button} disabled={!!busy} onClick={() => void tryCases(true)}>{t('skills.training.tryGroup', '试答 5 个典型问题')}</button>
          </div>
          {results.map((entry, index) => <article key={index} className="space-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
            <h4 className="font-medium">{entry.question}</h4>
            {entry.error && <p role="alert" className="text-sm text-red-600">{entry.error}</p>}
            {entry.result && <div className="grid gap-4 md:grid-cols-2">
              <div><p className="mb-2 text-xs text-gray-500">{t('skills.training.currentReply', '当前已发布版本')}</p>
                <p className="whitespace-pre-wrap text-sm">{entry.result.published_reply}</p>
                <p className="mt-2 text-xs text-gray-500">{t('skills.training.matched', '参考 {{count}} 条相关案例', { count: entry.result.matched_example_ids.length })}</p>
              </div>
              {entry.result.candidate_reply && <div><p className="mb-2 text-xs text-violet-500">{t('skills.training.candidateReply', '待更新预览')}</p>
                <p className="whitespace-pre-wrap text-sm">{entry.result.candidate_reply}</p>
                <p className="mt-2 text-xs text-gray-500">{t('skills.training.matched', '参考 {{count}} 条相关案例', { count: entry.result.candidate_example_ids.length })}</p>
              </div>}
            </div>}
          </article>)}
        </div>}
      </div>
      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 p-5 dark:border-gray-700">
        <p className="text-sm text-gray-500">{t('skills.training.selected', '本次纳入 {{count}} 条表达修正', { count: selectedCount })}</p>
        <button className="rounded-lg bg-violet-600 px-5 py-2 text-sm text-white disabled:opacity-40"
          disabled={!!busy || !candidate} onClick={publish}>{t('skills.training.publish', '更新技能')}</button>
      </footer>
    </section>
  </div>;
}
