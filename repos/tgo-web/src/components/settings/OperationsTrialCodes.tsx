import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { TrialCodeRecord } from '../../types/trialActivation';

const button = 'rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm disabled:opacity-40';

export default function OperationsTrialCodes() {
  const { t, i18n } = useTranslation();
  const [codes, setCodes] = useState<TrialCodeRecord[]>([]);
  const [issued, setIssued] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    void operationsApi.trialCodes().then(setCodes).catch(cause => {
      setError(cause instanceof Error ? cause.message : t('trialActivation.failed'));
    });
  }, [t]);

  async function create() {
    if (busy) return;
    setBusy(true); setError(''); setIssued('');
    try {
      const result = await operationsApi.issueTrialCode();
      setIssued(result.code);
      setCodes(await operationsApi.trialCodes());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('trialActivation.failed'));
    } finally { setBusy(false); }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(issued);
    } catch {
      setError(t('trialActivation.copyFailed'));
    }
  }

  return <section className="my-6 rounded-xl border border-slate-200 bg-white p-5">
    <h3 className="font-semibold">{t('trialActivation.issueTitle')}</h3>
    <p className="my-3 text-sm text-slate-500">{t('trialActivation.issueHint')}</p>
    <button type="button" className={button} disabled={busy} onClick={() => void create()}>
      {t('trialActivation.issueButton')}
    </button>
    {error && <p role="alert" className="my-3 text-red-700">{error}</p>}
    {issued && <div className="my-4 rounded-lg border border-amber-200 bg-amber-50 p-4">
      <p className="mb-2 text-sm text-amber-900">{t('trialActivation.copyNow')}</p>
      <code className="block break-all font-mono text-sm text-slate-900">{issued}</code>
      <button type="button" className={`${button} mt-3`} onClick={() => void copy()}>
        {t('trialActivation.copy')}
      </button>
    </div>}
    <h4 className="mt-6 text-sm font-semibold">{t('trialActivation.recent')}</h4>
    <ul className="mt-2 divide-y text-sm text-slate-600">
      {codes.map(item => <li key={item.id} className="flex flex-wrap justify-between gap-2 py-2">
        <span>{new Intl.DateTimeFormat(i18n.language).format(new Date(item.created_at))}</span>
        <span>{item.redeemed_at ? t('trialActivation.used') : new Date(item.expires_at).getTime() <= Date.now() ? t('trialActivation.expired') : t('trialActivation.available')}</span>
      </li>)}
    </ul>
  </section>;
}
