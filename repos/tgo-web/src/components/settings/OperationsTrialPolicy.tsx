import { useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { TrialPolicy } from '../../types/trialPolicy';

const button = 'rounded-lg border border-slate-300 px-4 py-2 text-sm disabled:opacity-40';

export default function OperationsTrialPolicy() {
  const { t } = useTranslation();
  const [policy, setPolicy] = useState<TrialPolicy | null>(null);
  const [replies, setReplies] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const apply = async (save: boolean) => {
    setBusy(true); setError('');
    try {
      const updated = save && policy
        ? await operationsApi.updateTrialPolicy({ expected_version: policy.version, ai_replies: Number(replies), reason: reason.trim() })
        : await operationsApi.trialPolicy();
      setPolicy(updated); setReplies(String(updated.ai_replies)); setReason('');
    } catch (caught) { setError(caught instanceof Error ? caught.message : t('billingSupport.error')); }
    finally { setBusy(false); }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!/^\d+$/.test(replies) || Number(replies) > 100000 || reason.trim().length < 5) return;
    void apply(true);
  };
  return <section className="my-6 rounded-xl border border-slate-200 bg-white p-5">
    <h3 className="font-semibold">{t('billingSupport.trialPolicy')}</h3>
    <p className="my-3 text-sm text-slate-500">{t('billingSupport.trialPolicyHint')}</p>
    <button className={button} disabled={busy} onClick={() => void apply(false)}>{t('billingSupport.refresh')}</button>
    {error && <p role="alert" className="my-3 text-red-700">{error}</p>}
    {policy && <form onSubmit={submit} className="mt-4 flex max-w-xl flex-col gap-3">
      <p className="text-sm">{t('billingSupport.trialPolicyCurrent', { days: policy.days, seats: policy.seats, replies: policy.ai_replies })}</p>
      <label className="text-sm">{t('billingSupport.trialReplies')}<input className="mt-2 block w-full rounded-lg border p-2" required type="number" min={0} max={100000} step={1} value={replies} onChange={event => setReplies(event.target.value)} /></label>
      <label className="text-sm">{t('billingSupport.reason')}<textarea className="mt-2 block w-full rounded-lg border p-2" required minLength={5} maxLength={500} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <button className={button} disabled={busy || Number(replies) === policy.ai_replies} type="submit">{t('billingSupport.trialPolicySave')}</button>
    </form>}
  </section>;
}
