import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { operationsApi } from '../../services/operationsApi';
import type { AuthorizationChange, AuthorizationPreview, CompanyDetail } from '../../types/operationsManagement';
import { localDateTime, OpsFeedback, OpsPanel, opsButton, opsInput, opsPrimary, useOpsResource } from './ui';

export default function OperationsAuthorization({ detail, onSaved }: { detail: CompanyDetail; onSaved: () => void }) {
  const { t, i18n } = useTranslation();
  const plans = useOpsResource(operationsApi.plans);
  const [plan, setPlan] = useState(detail.plan_id || '');
  const [expiry, setExpiry] = useState(localDateTime(detail.company.expires_at));
  const [seats, setSeats] = useState(String(detail.company.seats || Math.max(1, detail.company.used + detail.company.reserved)));
  const [credits, setCredits] = useState('0');
  const [reason, setReason] = useState('');
  const [preview, setPreview] = useState<AuthorizationPreview | null>(null);
  const [payload, setPayload] = useState<AuthorizationChange | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (preview) dialog.current?.showModal(); else dialog.current?.close(); }, [preview]);
  const published = plans.data?.filter(item => item.status === 'published') || [];
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (busy) return;
    setBusy(true); setError('');
    try {
      const request: AuthorizationChange = { request_id: crypto.randomUUID(), expected_version: detail.version,
        plan_id: plan, expires_at: new Date(expiry).toISOString(), seats: Number(seats), ai_credits: Number(credits), reason: reason.trim() };
      setPayload(request); setPreview(await operationsApi.previewAuthorization(detail.company.id, request));
    } catch (caught) { setError(caught instanceof Error ? caught.message : t('opsWorkspace.error')); }
    finally { setBusy(false); }
  };
  const confirm = async () => {
    if (!payload || busy) return; setBusy(true); setError('');
    try { await operationsApi.changeAuthorization(detail.company.id, payload); setPreview(null); onSaved(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : t('opsWorkspace.error')); }
    finally { setBusy(false); }
  };
  const date = (value: string | null) => value ? new Date(value).toLocaleString(i18n.language) : '—';
  return <OpsPanel>
    <h2 className="font-semibold">{t('opsWorkspace.authorization')}</h2><p className="mt-3 max-w-3xl text-sm leading-6 text-slate-500">{t('opsWorkspace.authorizationHint')}</p>
    <OpsFeedback {...plans} />
    {plans.data && published.length === 0 && <p className="mt-5 text-sm text-amber-800"><Link to="/ops/plans" className="underline">{t('opsWorkspace.noPlans')}</Link></p>}
    {published.length > 0 && <form onSubmit={event => void submit(event)} className="mt-6 grid max-w-3xl gap-5 sm:grid-cols-2">
      <label className="text-sm">{t('opsWorkspace.plan')}<select required value={plan} className={opsInput} onChange={event => { setPlan(event.target.value); const selected = published.find(item => item.id === event.target.value); if (selected) setSeats(String(Math.max(selected.definition.seats, detail.company.used + detail.company.reserved))); }}><option value="">{t('opsWorkspace.unnamed')}</option>{detail.plan_id && !published.some(item => item.id === detail.plan_id) && <option value={detail.plan_id} disabled>{detail.company.plan_name}</option>}{published.map(item => <option key={item.id} value={item.id}>{item.definition.name} · v{item.version}</option>)}</select></label>
      <label className="text-sm">{t('opsWorkspace.expiry')}<input type="datetime-local" required className={opsInput} value={expiry} onChange={event => setExpiry(event.target.value)} /></label>
      <label className="text-sm">{t('opsWorkspace.seats')}<input type="number" min={Math.max(1, detail.company.used + detail.company.reserved)} max={10000} step={1} required className={opsInput} value={seats} onChange={event => setSeats(event.target.value)} /><span className="mt-2 block text-xs text-slate-500">{t('opsWorkspace.seatsHint', { used: detail.company.used, reserved: detail.company.reserved })}</span></label>
      <label className="text-sm">{t('opsWorkspace.extraCredits')}<input type="number" min={0} max={100000000} step={1} required className={opsInput} value={credits} onChange={event => setCredits(event.target.value)} /></label>
      <label className="text-sm sm:col-span-2">{t('opsWorkspace.reason')}<textarea required minLength={5} maxLength={500} className={opsInput} value={reason} onChange={event => setReason(event.target.value)} /></label>
      {error && !preview && <p role="alert" className="text-sm text-red-700 sm:col-span-2">{error}</p>}
      <div className="sm:col-span-2"><button type="submit" disabled={busy || !published.some(item => item.id === plan)} className={opsPrimary}>{t(busy ? 'opsWorkspace.loading' : 'opsWorkspace.preview')}</button></div>
    </form>}
    <dialog ref={dialog} aria-labelledby="authorization-confirm-title" className="m-auto w-[min(94vw,40rem)] max-h-[90vh] overflow-y-auto rounded-2xl bg-white p-6 shadow-xl backdrop:bg-slate-950/40" onCancel={event => { event.preventDefault(); if (!busy) setPreview(null); }}>
      <h2 id="authorization-confirm-title" className="text-lg font-semibold">{t('opsWorkspace.confirmTitle')}</h2><p className="mt-2 text-sm text-slate-500">{detail.company.name}</p>
      {preview && <table className="mt-5 w-full text-left text-sm"><thead><tr><th className="py-3">{t('opsWorkspace.authorization')}</th><th>{t('opsWorkspace.before')}</th><th>{t('opsWorkspace.after')}</th></tr></thead><tbody className="divide-y divide-slate-100">
        <tr><th className="py-3 font-normal">{t('opsWorkspace.plan')}</th><td>{preview.before.plan_name || '—'}</td><td>{preview.after.plan_name}</td></tr><tr><th className="py-3 font-normal">{t('opsWorkspace.expiry')}</th><td>{date(preview.before.expires_at)}</td><td>{date(preview.after.expires_at)}</td></tr><tr><th className="py-3 font-normal">{t('opsWorkspace.seats')}</th><td>{preview.before.seats ?? '—'}</td><td>{preview.after.seats}</td></tr><tr><th className="py-3 font-normal">{t('opsWorkspace.extraCredits')}</th><td>—</td><td>+{preview.after.ai_credits}</td></tr>
      </tbody></table>}
      {preview?.after.status === 'suspended' && <p className="mt-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">{t('opsWorkspace.suspendedHint')}</p>}
      <p className="mt-4 whitespace-pre-wrap text-sm">{payload?.reason}</p>{error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
      <div className="mt-6 flex justify-end gap-3"><button className={opsButton} disabled={busy} onClick={() => setPreview(null)}>{t('opsWorkspace.cancel')}</button><button className={opsPrimary} disabled={busy} onClick={() => void confirm()}>{t('opsWorkspace.confirm')}</button></div>
    </dialog>
  </OpsPanel>;
}
