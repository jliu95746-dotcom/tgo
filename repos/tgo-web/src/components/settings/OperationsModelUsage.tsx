import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { ModelUsage } from '../../types/modelUsage';

const button = 'rounded-lg border border-slate-300 px-4 py-2 text-sm disabled:opacity-40';

export default function OperationsModelUsage() {
  const { t, i18n } = useTranslation();
  const [rows, setRows] = useState<ModelUsage[] | null>(null);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setBusy(true); setError('');
    void operationsApi.modelUsage(0).then(value => { if (active) { setRows(value); } })
      .catch(caught => { if (active) setError(caught instanceof Error ? caught.message : t('billingSupport.error')); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [t]);
  const load = async (next: number) => {
    setBusy(true); setError('');
    try { setRows(await operationsApi.modelUsage(next)); setOffset(next); }
    catch (caught) { setError(caught instanceof Error ? caught.message : t('billingSupport.error')); }
    finally { setBusy(false); }
  };
  const cost = (row: ModelUsage) => row.estimated_cost_fen === null
    ? t('billingSupport.costUnknown')
    : new Intl.NumberFormat(i18n.language, { style: 'currency', currency: row.currency, minimumFractionDigits: 2, maximumFractionDigits: 8 }).format(Number(row.estimated_cost_fen) / 100);
  return <section className="my-6 rounded-xl border border-slate-200 bg-white p-5">
    <h3 className="font-semibold">{t('billingSupport.modelUsage')}</h3>
    <p className="my-3 text-sm text-slate-500">{t('billingSupport.modelUsageHint')}</p>
    <button className={button} disabled={busy} onClick={() => void load(offset)}>{t('billingSupport.refresh')}</button>
    {error && <p role="alert" className="my-3 text-sm text-red-700">{error}</p>}
    {rows && <><div className="mt-4 overflow-x-auto"><table className="w-full text-left text-sm">
      <thead><tr>{['created', 'company', 'model', 'state', 'tokens', 'cost', 'round'].map(key => <th className="px-3 py-2" key={key}>{t(`billingSupport.${key}`)}</th>)}</tr></thead>
      <tbody>{rows.map(row => <tr key={row.id} className="border-t border-slate-100">
        <td className="whitespace-nowrap p-3">{new Date(row.created_at).toLocaleString(i18n.language)}</td>
        <td className="p-3 font-mono text-xs">{row.project_id}</td>
        <td className="p-3">{row.model_name}<p className="text-xs text-slate-500">{t(`billingSupport.modelPurpose.${row.purpose}`, { defaultValue: row.purpose })}</p></td>
        <td className="p-3">{t(`billingSupport.modelStatus.${row.status}`)}</td>
        <td className="p-3">{row.input_tokens ?? '—'} / {row.output_tokens ?? '—'}</td>
        <td className="whitespace-nowrap p-3">{cost(row)}</td>
        <td className="p-3 font-mono text-xs">{row.reservation_id ?? '—'}<p className="text-slate-500">{row.id}</p></td>
      </tr>)}</tbody>
    </table></div>
    {rows.length === 0 && <p className="my-3 text-sm text-slate-500">{t('billingSupport.empty')}</p>}
    <div className="mt-4 flex gap-3"><button className={button} disabled={busy || offset === 0} onClick={() => void load(Math.max(0, offset - 50))}>{t('billingSupport.previous')}</button><button className={button} disabled={busy || rows.length < 50} onClick={() => void load(offset + 50)}>{t('billingSupport.next')}</button></div></>}
  </section>;
}
